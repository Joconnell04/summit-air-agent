"""A batch of simulated calls to listen to, on our LiveKit stack, one call at a time.

    make sim CALLS="co-alarm-clear complaint-flag"     # these scenarios, in this order
    make sim TAGS=flow:safety                          # every scenario with these tags
    make listen                                        # then open the printed link

Each scenario runs once (the bake-off's three repeats of gas and CO calls are for scoring, not
listening), and the results file goes into the batch folder beside the recordings, so a listening
run never lands in evals/results or LangSmith. Stops once the batch has cost MAX_COST dollars.
"""
import argparse
import re

from evals.audio_rig import rig

DEFAULT_MAX_COST = 2.0


def rig_args(calls: str, tags: str, max_cost: float, stack: str = "livekit") -> list[str]:
    """The rig's arguments for a listening batch. Scenario ids may be split by spaces or commas."""
    args = ["--stack", stack, "--suite", "full", "--repeats", "1", "--samples", "1",
            "--no-upload", "--results-in-batch", "--max-cost", str(max_cost)]
    ids = [i for i in re.split(r"[\s,]+", calls) if i]
    if ids:
        args += ["--only", ",".join(ids)]
    if tags:
        args += ["--tags", tags]
    return args


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Record a batch of simulated calls.")
    parser.add_argument("--calls", default="", help="scenario ids, space or comma separated")
    parser.add_argument("--tags", default="", help="scenario tags, comma separated")
    parser.add_argument("--max-cost", type=float, default=DEFAULT_MAX_COST)
    args = parser.parse_args(argv)
    if not (args.calls.strip() or args.tags.strip()):
        parser.error("name scenarios with --calls or --tags")
    return rig.main(rig_args(args.calls, args.tags, args.max_cost))


if __name__ == "__main__":
    raise SystemExit(main())
