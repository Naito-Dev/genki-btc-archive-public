#!/usr/bin/env python3
"""Notify Discord from a verified immutable result after static publication."""
import argparse
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
from btc_signal.core import SignalError
from btc_signal.notify import prepare_notification, send_notification


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=REPO)
    parser.add_argument("--record-file", required=True)
    parser.add_argument("--published-at", required=True)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--prepare", action="store_true", help="Save intent; commit delivery file before sending")
    action.add_argument("--send-attempt-id", help="Send only the persisted intent owned by this workflow run/attempt")
    args = parser.parse_args()
    try:
        if args.prepare:
            result = prepare_notification(args.root, args.record_file, args.published_at)
        else:
            result = send_notification(args.root, args.record_file, args.send_attempt_id)
        values = {key: result.get(key) for key in ("record_id", "notification_state", "message_id", "attempt_id")}
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
                for key in ("attempt_id", "notification_state"):
                    output.write(f"{key}={values[key] or ''}\n")
        print(json.dumps(values, ensure_ascii=False))
        return 0
    except SignalError as exc:
        print(json.dumps({"notification_state": "failed", "error_code": exc.code}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
