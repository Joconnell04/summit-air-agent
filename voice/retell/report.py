"""Retell's own latency and cost per call, kept by the bridge from call_analyzed, for the bake-off
report beside our per-turn `reply_ms` in `make trace`.

Run on the server: python -m voice.retell.report [--db ~/summit-air/retell-data/bridge.db]
"""

import argparse
import json
import os
import sys

from .ledger import Ledger


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=os.environ.get("RETELL_BRIDGE_DB", "data/retell_bridge.db"))
    args = parser.parse_args()
    for row in Ledger(args.db).summaries():
        print(json.dumps(row))
    return 0


if __name__ == "__main__":
    sys.exit(main())
