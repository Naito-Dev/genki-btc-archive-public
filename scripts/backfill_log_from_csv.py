#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG_JSON = ROOT / "log.json"


def _sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _canonical_entry_for_hash(entry: dict) -> str:
    clean = {k: v for k, v in entry.items() if k not in ("hash", "prev_hash")}
    return json.dumps(clean, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _date_jst_from_utc_date(date_utc: str) -> str:
    dt = datetime.fromisoformat(date_utc).replace(tzinfo=timezone.utc)
    jst = dt.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    # Keep existing project style date only
    return dt.astimezone(timezone.utc).date().isoformat()


def load_log() -> dict:
    if not LOG_JSON.exists():
        return {"start_date_utc": None, "last_updated_utc": None, "entries": [], "latest": {}}
    return json.loads(LOG_JSON.read_text(encoding="utf-8"))


def save_log(log: dict) -> None:
    tmp = LOG_JSON.with_suffix(LOG_JSON.suffix + ".tmp")
    tmp.write_text(json.dumps(log, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(LOG_JSON)


def load_csv_points(csv_path: Path) -> list[tuple[str, float]]:
    pts: dict[str, float] = {}
    with csv_path.open("r", encoding="utf-8") as f:
        rd = csv.DictReader(f)
        for r in rd:
            ts = float(r["timestamp"])
            while ts > 1e11:
                ts /= 1000.0
            d = datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
            pts[d] = float(r["close"])
    return sorted(pts.items(), key=lambda x: x[0])


def make_backfill_entry(date_utc: str, close: float) -> dict:
    fixed_updated = f"{date_utc}T00:00:00Z"
    return {
        "date": date_utc,
        "date_jst": _date_jst_from_utc_date(date_utc),
        "timestamp_utc": fixed_updated,
        "data_source": "BINANCE_D1_BACKFILL",
        "price_source": "BINANCE_D1_BACKFILL",
        "price_ts": fixed_updated,
        "btc_price": round(close, 2),
        "regime": "CASH",
        "regime_source": "BACKFILL",
        "allocation": 0,
        "logic_version": "backfill-v1",
        "position": "CASH",
        "target_btc_ratio": 0.0,
        "actual_btc_ratio": None,
        "actual_position": "unknown",
        "equity_usd": None,
        "base_equity": None,
        "initial_capital": None,
        "pnl_percent": None,
        "pnl_usd": None,
        "pnl_btc": None,
        "reason_summary": "historical_backfill",
        "trigger": "historical_backfill",
        "confidence_score": None,
        "status": "HISTORICAL_BACKFILL",
        "allocation_changed": False,
        "day": "Backfill",
        "notes": "Backfilled from Binance D1 CSV for Model D warmup/history.",
        "snapshot_status": "SYNC_PENDING",
        "balance_source": "unavailable",
        "balance_ts_utc": None,
        "portfolio_snapshot": {"status": "SYNC_PENDING", "source": "unavailable", "ts_utc": None},
        "pnl": None,
        "updated_at_utc": fixed_updated,
        "published_at_utc": fixed_updated,
        "chain_integrity": "VALID",
        "chain_reason": "",
        "prev_hash": None,
        "hash": None,
    }


def rebuild_chain(entries: list[dict]) -> list[dict]:
    entries = sorted(entries, key=lambda x: x.get("date", ""))
    prev_hash = None
    for e in entries:
        e["prev_hash"] = prev_hash
        e["hash"] = _sha256_hex(_canonical_entry_for_hash(e) + "|" + (prev_hash or ""))
        prev_hash = e["hash"]
    return entries


def main() -> int:
    csv_path = Path("/Users/Claw/tradep-test/data/Binance_BTCUSDT_D1.csv")
    if not csv_path.exists():
        print(f"ERROR: csv not found: {csv_path}")
        return 1

    log = load_log()
    entries = list(log.get("entries", [])) if isinstance(log.get("entries"), list) else []
    existing_dates = {e.get("date") for e in entries if isinstance(e, dict)}

    points = load_csv_points(csv_path)
    latest_date = (log.get("latest") or {}).get("date")
    if isinstance(latest_date, str):
        points = [(d, c) for d, c in points if d <= latest_date]

    add_count = 0
    for d, c in points:
        if d in existing_dates:
            continue
        entries.append(make_backfill_entry(d, c))
        add_count += 1

    entries = rebuild_chain(entries)
    log["entries"] = entries
    if entries:
        log["start_date_utc"] = entries[0].get("date")
        log["latest"] = entries[-1]
        log["last_updated_utc"] = entries[-1].get("updated_at_utc")

    save_log(log)
    print(f"OK_BACKFILL added={add_count} entries_len={len(entries)} first={entries[0].get('date') if entries else None} last={entries[-1].get('date') if entries else None}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
