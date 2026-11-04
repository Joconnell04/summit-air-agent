from scripts.lanes import decisions, pr_numbers, render

BODY = """Backfilled.

**Pull requests**
- #89 Debug bench
- #101 Bench shows calls as recorded

**Decisions:** DECISIONS.md 055, 059, 064"""


def test_reads_pull_requests_and_decisions_from_the_issue_body():
    assert pr_numbers(BODY) == [89, 101]
    assert decisions(BODY) == "055, 059, 064"
    assert pr_numbers("Mentions #12 in prose only") == []


def test_open_lanes_come_first_and_open_pull_requests_are_marked():
    issues = [
        {"number": 116, "title": "Debug bench", "state": "OPEN", "body": BODY},
        {"number": 103, "title": "CO check", "state": "CLOSED", "body": "- #64 CO check"},
    ]
    out = render(issues, {89: "MERGED", 101: "OPEN", 64: "MERGED"})
    assert out.index("Debug bench") < out.index("CO check")
    assert "#101](https://github.com/Joconnell04/summit-air-agent/pull/101) (open)" in out
    assert "#89](https://github.com/Joconnell04/summit-air-agent/pull/89)," in out
    assert "none |" in render([], {})
