"""Save Jackson's Retell agent into the repo, so its prompt, tools and settings are reviewed with
the code and every change has a commit.

    uv run --env-file .env python -m scripts.retell_snapshot [--note "what changed"]

Writes retell/summit-air-jackson/: agent.json and llm.json (settings, with the prompt and tools
taken out), prompt.md, tools.json, and kb/ for each knowledge base text source. A note appends a
line to CHANGELOG.md with the versions it saved.
"""

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import httpx

AGENT_ID = "agent_a3e38e6aa3d4e75dfac12aafc8"
OUT = Path(__file__).resolve().parents[1] / "retell" / "summit-air-jackson"
API = "https://api.retellai.com"
# Fields that change on every read, so a snapshot only differs when the agent does.
VOLATILE = {"last_modification_timestamp"}


def get(client: httpx.Client, path: str) -> dict:
    response = client.get(f"{API}{path}")
    response.raise_for_status()
    return response.json()


def no_dashes(text: str) -> str:
    """The repo keeps no em dashes (CLAUDE.md); Retell's own text can have them."""
    return text.replace("\u2014", ", ").replace("\\u2014", ", ")


def write_json(path: Path, data: dict) -> None:
    path.write_text(no_dashes(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False)) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--note", default="")
    args = parser.parse_args()
    headers = {"Authorization": f"Bearer {os.environ['RETELL_API_KEY']}"}
    with httpx.Client(headers=headers, timeout=30) as client:
        agent = get(client, f"/get-agent/{AGENT_ID}")
        llm_id = agent["response_engine"]["llm_id"]
        llm = get(client, f"/get-retell-llm/{llm_id}")
        kbs = [get(client, f"/get-knowledge-base/{kb}") for kb in llm.get("knowledge_base_ids") or []]

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "prompt.md").write_text(no_dashes((llm.get("general_prompt") or "").rstrip()) + "\n")
    write_json(OUT / "tools.json", {"general_tools": llm.get("general_tools") or []})
    settings = {k: v for k, v in llm.items() if k not in {"general_prompt", "general_tools"} | VOLATILE}
    write_json(OUT / "llm.json", settings)
    write_json(OUT / "agent.json", {k: v for k, v in agent.items() if k not in VOLATILE})

    kb_dir = OUT / "kb"
    kb_dir.mkdir(exist_ok=True)
    for kb in kbs:
        write_json(kb_dir / f"{kb['knowledge_base_id']}.json", {k: v for k, v in kb.items() if k != "knowledge_base_sources"} | {
            "sources": [{k: v for k, v in s.items() if k not in {"file_url"}} for s in kb.get("knowledge_base_sources") or []]
        })

    if args.note:
        stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
        line = f"- {stamp}: agent v{agent.get('version')}, LLM v{llm.get('version')}. {args.note}\n"
        log = OUT / "CHANGELOG.md"
        if not log.exists():
            log.write_text("# Summit Air Jackson: changes\n\nNewest last. Each line is one snapshot, made after a change was published in Retell.\n\n")
        with log.open("a") as f:
            f.write(line)
    print(f"saved agent v{agent.get('version')} and LLM v{llm.get('version')} to {OUT}")


if __name__ == "__main__":
    main()
