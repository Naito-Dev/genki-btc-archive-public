#!/usr/bin/env python3
"""Save yesterday's completed UTC daily rule result. Never accepts a historical date."""
import argparse
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
from btc_signal.core import RULE_ID, SignalError
from btc_signal.daily import record_daily


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=REPO)
    parser.add_argument("--run-id")
    args = parser.parse_args()
    try:
        record, created = record_daily(args.root, run_id=args.run_id)
        values = {"result_file": f"records/{RULE_ID}/{record['target_date']}.json",
                  "record_id": record["record_id"], "target_date": record["target_date"],
                  "decision": record["decision"], "result_sha256": record["result_sha256"],
                  "created": str(created).lower()}
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
                for key, value in values.items():
                    output.write(f"{key}={value}\n")
        print(json.dumps(values, ensure_ascii=False))
        return 0
    except SignalError as exc:
        print(json.dumps({"state": "failed", "label": "判定不能", "error_code": exc.code}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
