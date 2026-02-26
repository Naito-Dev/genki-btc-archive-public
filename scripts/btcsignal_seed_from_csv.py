#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parent.parent
OUT_LOG = ROOT / "btcsignal_log.json"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Seed btcsignal_log.json from D1 CSV")
    p.add_argument(
        "--csv",
        type=str,
        default=str(ROOT / "data" / "Binance_BTCUSDT_D1.csv"),
        help="Path to D1 CSV (must contain timestamp/date and close)",
    )
    p.add_argument("--seed-days", type=int, default=60, help="Number of latest rows to seed")
    return p.parse_args()


def save_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize_date(raw: str) -> str | None:
    s = str(raw).strip()
    if not s:
        return None
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]

    try:
        v = int(float(s))
    except Exception:
        return None

    # epoch units heuristic
    if v > 10**14:  # microseconds
        ts = v / 1_000_000
    elif v > 10**11:  # milliseconds
        ts = v / 1_000
    else:  # seconds
        ts = v

    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        return None


def compute_state(closes: list[float]) -> tuple[str, float, str]:
    close_today = closes[-1]
    if len(closes) < 51:
        return ("CASH", float(mean(closes)), f"seed_warmup({len(closes)}/51)")

    sma50_today = mean(closes[-50:])
    sma50_yday = mean(closes[-51:-1])
    close_yday = closes[-2]
    hold = (close_today > sma50_today) and (close_yday > sma50_yday) and (sma50_today >= sma50_yday)
    if hold:
        return ("HOLD", float(sma50_today), "seed_source=csv;hold_cond(close>SMA50_2days & SMA50_up)")
    return ("CASH", float(sma50_today), "seed_source=csv;cash(not_hold_cond)")


def main() -> int:
    args = parse_args()
    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"ERROR: CSV not found: {csv_path}")
        return 1

    with csv_path.open("r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    pts: list[tuple[str, float]] = []
    for r in rows:
        d = normalize_date(r.get("date") or r.get("ts_utc") or r.get("timestamp") or "")
        c = r.get("close")
        if not d or c is None or c == "":
            continue
        try:
            close = float(c)
        except Exception:
            continue
        pts.append((d, close))

    if not pts:
        print("ERROR: no usable rows in CSV")
        return 1

    pts.sort(key=lambda x: x[0])

    # de-dup by date
    dedup: dict[str, float] = {}
    for d, c in pts:
        dedup[d] = c
    pts = sorted(dedup.items(), key=lambda x: x[0])

    seed_n = max(51, int(args.seed_days))
    seed = pts[-seed_n:]

    closes: list[float] = []
    entries: list[dict] = []
    for d, c in seed:
        closes.append(float(c))
        state, sma, reason = compute_state(closes)
        entries.append(
            {
                "date": d,
                "state": state,
                "close": round(float(c), 2),
                "sma50": round(float(sma), 2),
                "reason": reason,
            }
        )

    save_json(OUT_LOG, {"entries": entries})
    print(f"OK_SEEDED: {len(entries)} entries ({entries[0]['date']} -> {entries[-1]['date']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
