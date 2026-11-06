"""The test folders a commit needs, from the files it stages, so the pre-commit hook runs only
those. The full suite still runs in `make check` and `make ci`.

    uv run python scripts/changed_tests.py        prints test paths, "ALL", or nothing

Nothing means the commit touches only docs, eval write-ups or Retell snapshots. A path no rule
covers runs everything, so a new folder is checked in full until it gets a rule.
"""

import subprocess
import sys

# First match wins. The tool server is shared by every voice layer, so its changes also run
# the Retell bridge tests, which call it the way a live call does.
RULES: list[tuple[str, list[str]]] = [
    ("docs/", []),
    ("retell/", []),
    ("evals/rounds/", []),
    ("from-other-projects/", []),
    ("postman/", ["tests/tool_server/test_ts_postman.py"]),
    ("servicetitan_mock/", ["tests/servicetitan_mock", "tests/tool_server"]),
    ("app/", ["tests/tool_server", "tests/voice_retell"]),
    ("tenants/", ["tests/tool_server", "tests/voice_retell"]),
    ("trades/", ["tests/tool_server"]),
    ("prompts/", ["tests/tool_server", "tests/voice", "tests/voice_retell"]),
    ("config/", ["tests/test_models_config.py", "tests/voice", "tests/tool_server"]),
    ("voice/retell", ["tests/voice_retell"]),
    ("voice/elevenlabs", ["tests/voice_elevenlabs"]),
    ("voice/gemini_live", ["tests/voice_gemini_live"]),
    ("voice/openai_realtime", ["tests/voice_openai_realtime"]),
    ("voice/", ["tests/voice"]),
    ("evals/", ["tests/evals"]),
    ("scripts/", ["tests"]),
    ("deploy/", ["tests/test_deploy_crontab.py", "tests/test_deploy_scripts.py"]),
    ("tests/", None),  # a changed test file runs itself; see below
]
DOC_SUFFIXES = (".md",)


def staged() -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        capture_output=True, text=True, check=True,
    ).stdout
    return [line for line in out.splitlines() if line]


def select_tests(paths: list[str]) -> list[str] | None:
    """Test paths to run, [] for none, or None for the whole suite."""
    chosen: set[str] = set()
    for path in paths:
        if path.endswith(DOC_SUFFIXES):
            continue
        for prefix, tests in RULES:
            if path.startswith(prefix):
                if tests is None:
                    chosen.add(path if path.endswith(".py") else "tests")
                else:
                    chosen.update(tests)
                break
        else:
            return None
    return sorted(chosen)


def main() -> None:
    tests = select_tests(staged())
    if tests is None or "tests" in tests:
        print("ALL")
    elif tests:
        print(" ".join(tests))
    sys.exit(0)


if __name__ == "__main__":
    main()
