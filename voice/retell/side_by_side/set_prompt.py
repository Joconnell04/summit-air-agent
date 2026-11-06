"""Sets an arm's general prompt from a file, for prompt rounds on a side-by-side arm.

    PYTHONPATH=. uv run --env-file .env python voice/retell/side_by_side/set_prompt.py <llm_id> <prompt.md>

Refuses Jackson's own LLM, so only a copy is ever changed. Prints the LLM's last-modified time,
which is what the round notes cite for when a change went live.
"""

import os
import sys
from pathlib import Path

from voice.retell.api import Retell

JACKSON_LLM = "llm_12eba5f1d4a3b067f0635097de30"


def main() -> None:
    llm_id, path = sys.argv[1], Path(sys.argv[2])
    if llm_id == JACKSON_LLM:
        raise SystemExit("refusing to change Jackson's own LLM")
    retell = Retell(os.environ["RETELL_API_KEY"])
    retell.request(
        "PATCH", f"/update-retell-llm/{llm_id}", json={"general_prompt": path.read_text()}
    )
    llm = retell.request("GET", f"/get-retell-llm/{llm_id}")
    same = llm.get("general_prompt") == path.read_text()
    print(llm_id, "live" if same else "MISMATCH", llm.get("last_modification_timestamp"))


if __name__ == "__main__":
    main()
