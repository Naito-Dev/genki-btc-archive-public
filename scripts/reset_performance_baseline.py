#!/usr/bin/env python3
"""Reset performance baseline in log.json without touching trading logic/history."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--log", default="log.json", help="Path to log.json")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    path = Path(args.log)
    data = json.loads(path.read_text(encoding="utf-8"))
    latest = data.get("latest") or {}

    equity = latest.get("equity_usd")
    if equity is None:
        raise SystemExit("latest.equity_usd is missing; cannot reset baseline")

    base = float(equity)
    today_utc = dt.datetime.now(dt.timezone.utc).date().isoformat()

    data["base_equity"] = base
    data["start_date_utc"] = today_utc

    latest["base_equity"] = base
    latest["initial_capital"] = base
    latest["start_date_utc"] = today_utc
    latest["pnl_percent"] = 0.0
    data["latest"] = latest

    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"baseline_reset=ok base_equity={base:.2f} start_date_utc={today_utc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
