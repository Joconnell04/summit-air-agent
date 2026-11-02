"""Fail if the test count drops below the committed baseline.

The eval pass-rate floor is wired in once the eval suite exists.
Run with --update to raise the baseline after adding tests.
"""
import json
import subprocess
import sys
from pathlib import Path

BASELINE = Path(__file__).resolve().parent.parent / "evals" / "baseline.json"


def count_tests() -> int:
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        capture_output=True, text=True, check=False,
    ).stdout
    return sum(1 for line in out.splitlines() if "::" in line)


def check(count: int, baseline: dict) -> tuple[bool, str]:
    floor = baseline["min_tests"]
    if count < floor:
        return False, f"test count {count} is below the baseline of {floor}"
    return True, f"test count {count} (baseline {floor})"


def main() -> int:
    baseline = json.loads(BASELINE.read_text())
    count = count_tests()
    if "--update" in sys.argv:
        if count < baseline["min_tests"]:
            print(f"refusing to lower the baseline from {baseline['min_tests']} to {count}")
            return 1
        baseline["min_tests"] = count
        BASELINE.write_text(json.dumps(baseline, indent=2) + "\n")
        print(f"baseline raised to {count}")
        return 0
    ok, msg = check(count, baseline)
    print(("ok: " if ok else "ratchet: ") + msg)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
