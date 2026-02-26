#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT_PATH = ROOT / "data" / "live_portfolio_snapshot.json"
BASELINE_PATH = ROOT / "data" / "live_test_baseline.json"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Ensure live testing baseline file exists.")
    p.add_argument("--start-date-jst", default="2026-02-26", help="Fixed baseline start date (JST)")
    return p.parse_args()


def load_json(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_num(v):
    try:
        n = float(v)
        return n
    except Exception:
        return None


def main() -> int:
    args = parse_args()
    if BASELINE_PATH.exists():
        print("BASELINE_EXISTS: keep current baseline")
        return 0

    snap = load_json(SNAPSHOT_PATH)
    if not isinstance(snap, dict):
        print("BASELINE_SKIPPED: snapshot missing")
        return 0

    usdt = parse_num(snap.get("usdt_balance"))
    btc = parse_num(snap.get("btc_balance"))
    px = parse_num(snap.get("price_at_snapshot"))
    if usdt is None or btc is None or px is None:
        print("BASELINE_SKIPPED: snapshot values unavailable")
        return 0

    equity = usdt + (btc * px)
    payload = {
        "start_date_jst": args.start_date_jst,
        "source": "BITGET_READONLY",
        "created_at_utc": snap.get("updated_at_utc") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "equity_usd": round(equity, 2),
        "usdt_balance": round(usdt, 8),
        "btc_balance": round(btc, 8),
        "price_at_baseline": round(px, 2),
    }
    save_json(BASELINE_PATH, payload)
    print(f"BASELINE_CREATED: start={args.start_date_jst} equity_usd={payload['equity_usd']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
