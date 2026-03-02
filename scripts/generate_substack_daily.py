#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIVE_LOG = ROOT / "btcsignal_log_live.json"
OUT = ROOT / "substack" / "daily_latest.txt"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate Substack Daily draft")
    p.add_argument("--in", dest="infile", default=str(LIVE_LOG))
    p.add_argument("--out", dest="outfile", default=str(OUT))
    return p.parse_args()


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def build_block(latest_date: str, state: str, s1: str, s2: str, s3: str) -> str:
    return (
        "[SUBSTACK_DAILY]\n"
        f"Subject: {state} • Daily Record • {latest_date}\n\n"
        "Body:\n"
        f"Confirmed record (BTC / CASH): {state}\n"
        f"3-day record: {s1} → {s2} → {s3}\n"
        "Published when the public record updates.\n"
        "Record-only. No prediction. No reasoning. No advice. Not investment advice.\n"
        "Public: https://btcsignal.org\n"
        "[/SUBSTACK_DAILY]\n"
    )


def main() -> int:
    args = parse_args()
    src = Path(args.infile)
    dst = Path(args.outfile)

    if not src.exists():
        print(f"ERROR: source not found: {src}")
        return 1

    try:
        data = load_json(src)
    except Exception as e:
        print(f"ERROR: failed to parse source: {type(e).__name__}: {e}")
        return 1

    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, list) or len(entries) < 3:
        print("ERROR: required inputs missing (need latest_date/state/last3_states)")
        return 1

    last = entries[-1] if isinstance(entries[-1], dict) else {}
    latest_date = str(last.get("date") or "").strip()[:10]
    recorded_state = str(last.get("state") or "").strip().upper()

    if not latest_date or recorded_state not in {"BTC", "CASH"}:
        print("ERROR: required inputs missing (latest_date or recorded_state)")
        return 1

    last3 = []
    for item in entries[-3:]:
        if not isinstance(item, dict):
            continue
        st = str(item.get("state") or "").strip().upper()
        if st in {"BTC", "CASH"}:
            last3.append(st)
    if len(last3) != 3:
        print("ERROR: required inputs missing (last3_states)")
        return 1

    block = build_block(latest_date, recorded_state, last3[0], last3[1], last3[2])
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(block, encoding="utf-8")
    print(block)
    print(f"OK: wrote {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
