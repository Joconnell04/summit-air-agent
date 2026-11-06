"""Round knowledge-rag-01 (evals/rounds/knowledge-rag-01.md): retrieval for ten caller questions,
then the turn after the lookup written by the live model and by the quality model.

    uv run --env-file .env python -m voice.retell_llm.knowledge_eval retrieval [out.json]
    uv run --env-file .env python -m voice.retell_llm.knowledge_eval answers SYSTEM_PROMPT.txt [out.json]

Needs OPENAI_API_KEY (retrieval) and ANTHROPIC_API_KEY (answers). Paid: about 20 embeddings and 40
short model requests, under $0.50 (MEASURED in the round). The system prompt file is the real one,
from assemble.instructions(), so both models answer under the live rules."""

import asyncio
import json
import statistics
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]

from app import knowledge_search
from prompts.assemble import SpokenReply, for_model, job_types, tool_specs
from voice.archive.livekit.settings import load_profile

from .call import LOOKUP_ANSWER
from .model import Brain, Reply

TENANT = ROOT / "tenants" / "summit-air"
# (question, the page and idea a good lookup finds; "" when the pages hold nothing)
QUESTIONS = [
    ("My radiator is banging every morning.", "heating: steam banging"),
    ("Why is my mini split blinking?", "cooling: blinking light"),
    ("How often should I change my filter?", "filter every one to three months"),
    ("Is it normal my boiler makes the pipes clank?", "heating: steam banging"),
    ("My AC runs but the air isn't cold.", "cooling: not cold"),
    ("What's an ERV?", "air-and-ventilation: ERV"),
    ("There's water on the floor under my boiler.", "boiler leak"),
    ("What's the difference between steam heat and hot water heat?", "heating: steam vs hot water"),
    ("My mini split stops blowing heat for a few minutes in the winter and there's steam outside.", "defrost"),
    ("When does the landlord have to give me heat?", "heat season dates"),
    ("What are your hours on Saturday?", ""),
    ("How much does a new boiler cost?", ""),
    ("Can you send someone today?", ""),
]
FRAMING = ("reference", "document", "text", "according to", "knowledge base", "source")


def documents() -> tuple[dict[str, str], dict[str, str]]:
    agent = yaml.safe_load((TENANT / "agent.yaml").read_text())
    docs = {n: (TENANT / "knowledge" / f"{n}.md").read_text() for n in agent["lookup_knowledge"]}
    return docs, agent.get("lookup_synonyms", {})


def retrieval(out: Path) -> None:
    docs, synonyms = documents()
    index = knowledge_search.load_index(TENANT / "knowledge" / knowledge_search.INDEX_FILE, docs)
    config = knowledge_search.settings()
    rows = []
    for question, wanted in QUESTIONS:
        asked = knowledge_search.embed([question], config)[0]
        top = sorted(
            ((knowledge_search.cosine(asked, p["vector"]), p) for p in index["passages"]),
            key=lambda t: -t[0],
        )[:4]
        facts, method = knowledge_search.search(question, docs, synonyms, TENANT)
        rows.append({
            "question": question,
            "wanted": wanted,
            "method": method,
            "facts": [{"text": f.text, "file": f.file} for f in facts],
            "keyword": [f.text for f in knowledge_search.by_words(question, docs, synonyms)],
            "top_cosines": [(round(c, 3), p["file"], p["text"][:70]) for c, p in top],
        })
    out.write_text(json.dumps(rows, indent=1))
    for r in rows:
        print(r["question"], "->", r["method"], [round(c[0], 2) for c in r["top_cosines"]], len(r["facts"]))


def tools() -> list[dict]:
    specs = tool_specs(job_types(TENANT))
    return [{"name": s["name"], "description": s["description"], "input_schema": s["parameters"]} for s in specs]


async def answer(brain: Brain, system: str, question: str, facts: list[str]) -> dict:
    result = {"ok": True, "say": "", "data": {"facts": facts, "log": {"files": []}}}
    messages = [
        {"role": "user", "content": question},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "lookup_hvac_info", "input": {"question": question}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": json.dumps(for_model(result))},
            {"type": "text", "text": LOOKUP_ANSWER.read_text().strip()},
        ]},
    ]
    reply, text = Reply(), ""
    async for delta in brain.stream(system, messages, tools(), reply):
        text += delta
    return {"first_token_ms": round(reply.first_token_ms), "text": " ".join(text.split())}


async def answers(system_file: str, out: Path) -> None:
    system = Path(system_file).read_text()
    rows = json.loads(Path("retrieval.json").read_text())
    brains = {name: Brain(load_profile(name)) for name in ("fast", "quality")}
    results = []
    for row in rows:
        facts = [f["text"] for f in row["facts"]]
        if not facts:
            continue
        entry = {"question": row["question"], "facts": facts}
        for name, brain in brains.items():
            # Run 1 has a note the cache has never seen, so it pays for the whole prompt (the first
            # hand-off on a call); runs 2 and 3 share the prompt's cache (a later one).
            fresh = f"{system}\n\n(run {name} {row['question']})"
            cold = await answer(brain, fresh, row["question"], facts)
            runs = [cold] + [await answer(brain, system, row["question"], facts) for _ in range(2)]
            entry[name] = {
                "first_token_ms": [r["first_token_ms"] for r in runs],
                "cold_ms": cold["first_token_ms"],
                "warm_median_ms": statistics.median(r["first_token_ms"] for r in runs[1:]),
                "text": runs[-1]["text"],
                "framing": [w for w in FRAMING if w in runs[-1]["text"].lower()],
                "filtered_by_reply_rule": SpokenReply().sentence(runs[-1]["text"]) == "",
            }
        results.append(entry)
        print(row["question"], entry["fast"]["cold_ms"], entry["fast"]["warm_median_ms"],
              entry["quality"]["cold_ms"], entry["quality"]["warm_median_ms"])
    out.write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "retrieval":
        retrieval(Path(sys.argv[2] if len(sys.argv) > 2 else "retrieval.json"))
    else:
        asyncio.run(answers(sys.argv[2], Path(sys.argv[3] if len(sys.argv) > 3 else "answers.json")))
