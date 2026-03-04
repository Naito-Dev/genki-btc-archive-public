#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIVE_LOG = ROOT / "btcsignal_log_live.json"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate Phase 0 X thread (2 tweets) from live decision log")
    p.add_argument("--in", dest="infile", default=str(LIVE_LOG))
    return p.parse_args()


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def norm_state(raw: object) -> str | None:
    s = str(raw or "").strip().upper()
    return s if s in {"BTC", "CASH"} else None


def main() -> int:
    args = parse_args()
    src = Path(args.infile)
    if not src.exists():
        print("ERROR: source not found")
        return 1

    data = load_json(src)
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, list) or len(entries) < 3:
        print("ERROR: entries missing")
        return 1

    last = entries[-1] if isinstance(entries[-1], dict) else {}
    latest_date = str(last.get("date") or "").strip()[:10]
    if not latest_date:
        print("ERROR: latest_date missing")
        return 1

    s3 = []
    for item in entries[-3:]:
        if not isinstance(item, dict):
            print("ERROR: invalid entries")
            return 1
        st = norm_state(item.get("state"))
        if st is None:
            print("ERROR: invalid state")
            return 1
        s3.append(st)

    state = norm_state(last.get("state"))
    if state is None:
        print("ERROR: invalid recorded state")
        return 1

    tweet_1 = "\n".join(
        [
            "1/2",
            f"BTCSIGNAL — Confirmed record ({latest_date})",
            "",
            f"Recorded state: {state}",
            f"3-day record: {s3[0]} → {s3[1]} → {s3[2]}",
            "",
            "Record-only. Not investment advice.",
        ]
    )

    tweet_2 = "\n".join(
        [
            "2/2",
            "Verify log: https://btcsignal.org",
            "Get email: https://btcsignal.substack.com",
            "",
            "(Phase 0: Record-only system verification)",
        ]
    )

    out = {
        "latest_date": latest_date,
        "tweet_1": tweet_1,
        "tweet_2": tweet_2,
    }
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
