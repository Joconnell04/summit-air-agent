"""Makes or refreshes a side-by-side arm: a copy of Jackson's agent and its LLM with one change.

    PYTHONPATH=. uv run --env-file .env python voice/retell/side_by_side/make_arm.py "<agent name>" \
        [--model claude-5.5-sonnet] [--prompt path/to/prompt.md] [--eval-world]

Copies every setting of Jackson's LLM (llm_12eba5f1d4a3b067f0635097de30) and agent
(agent_a3e38e6aa3d4e75dfac12aafc8) as they are now, then changes only the model or only the
general prompt. --eval-world points every custom function and the agent's webhook at the eval
bridge (/eval/retell/...), so calls on the second line run in the eval world, never the live one;
any function that would still reach something else stops the copy. A second run with the same name updates the same copy, so the arm can be brought
level with Jackson's agent right before a run. Prints the arm's agent id. Uses only the endpoints
in voice/retell/api.ALLOWED.
"""

import argparse
import os
from pathlib import Path

from voice.retell.api import Retell

SOURCE_AGENT = "agent_a3e38e6aa3d4e75dfac12aafc8"
SOURCE_LLM = "llm_12eba5f1d4a3b067f0635097de30"
# Fields Retell sets itself; sending them back on create or update is refused or ignored.
LLM_READ_ONLY = {"llm_id", "version", "is_published", "last_modification_timestamp"}
LIVE = "https://45-77-105-247.sslip.io/retell/"
EVAL = "https://45-77-105-247.sslip.io/eval/retell/"
AGENT_READ_ONLY = {
    "agent_id",
    "version",
    "base_version",
    "is_published",
    "last_modification_timestamp",
}


def llm_body(source: dict, model: str | None, prompt: str | None) -> dict:
    body = {k: v for k, v in source.items() if k not in LLM_READ_ONLY}
    if model:
        body["model"] = model
        config = dict(body.get("model_config") or {})
        if config.get("primary"):
            config["primary"] = {**config["primary"], "model": model}
        body["model_config"] = config
    if prompt is not None:
        body["general_prompt"] = prompt
    return body


def to_eval(url: str, what: str) -> str:
    if url.startswith(LIVE):
        url = EVAL + url.removeprefix(LIVE)
    if not url.startswith(EVAL):
        raise SystemExit(f"{what} calls {url}, not the eval bridge")
    return url


def eval_world(llm: dict, agent: dict) -> None:
    for tool in llm.get("general_tools") or []:
        if tool.get("url"):
            tool["url"] = to_eval(tool["url"], f"function {tool.get('name')}")
    for state in llm.get("states") or []:
        for tool in state.get("tools") or []:
            if tool.get("url"):
                tool["url"] = to_eval(tool["url"], f"function {tool.get('name')}")
    agent["webhook_url"] = to_eval(agent["webhook_url"], "the agent webhook")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("name")
    parser.add_argument("--model")
    parser.add_argument("--prompt")
    parser.add_argument("--eval-world", action="store_true")
    args = parser.parse_args()
    prompt = Path(args.prompt).read_text() if args.prompt else None
    retell = Retell(os.environ["RETELL_API_KEY"])
    src_llm = retell.request("GET", f"/get-retell-llm/{SOURCE_LLM}")
    src_agent = retell.request("GET", f"/get-agent/{SOURCE_AGENT}")
    want_llm = llm_body(src_llm, args.model, prompt)

    existing = [
        a for a in retell.list_all("POST", "/v2/list-agents") if a.get("agent_name") == args.name
    ]
    if existing:
        agent_id = existing[0]["agent_id"]
        llm_id = retell.request("GET", f"/get-agent/{agent_id}")["response_engine"]["llm_id"]
        retell.request("PATCH", f"/update-retell-llm/{llm_id}", json=want_llm)
    else:
        llm_id = retell.request("POST", "/create-retell-llm", json=want_llm)["llm_id"]
        agent_id = None

    want_agent = {k: v for k, v in src_agent.items() if k not in AGENT_READ_ONLY}
    want_agent["agent_name"] = args.name
    want_agent["response_engine"] = {"type": "retell-llm", "llm_id": llm_id}
    if args.eval_world:
        eval_world(want_llm, want_agent)
        retell.request("PATCH", f"/update-retell-llm/{llm_id}", json=want_llm)
    if agent_id:
        retell.request("PATCH", f"/update-agent/{agent_id}", json=want_agent)
    else:
        agent_id = retell.request("POST", "/create-agent", json=want_agent)["agent_id"]

    llm = retell.request("GET", f"/get-retell-llm/{llm_id}")
    agent = retell.request("GET", f"/get-agent/{agent_id}")
    urls = sorted(
        {t["url"].rsplit("/", 1)[0] for t in llm.get("general_tools") or [] if t.get("url")}
    )
    print(agent_id, llm_id, llm.get("model"), urls, agent.get("webhook_url"))


if __name__ == "__main__":
    main()
