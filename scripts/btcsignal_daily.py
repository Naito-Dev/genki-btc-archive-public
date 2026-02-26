#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parent.parent
SRC_LOG = ROOT / "log.json"
OUT_LOG = ROOT / "btcsignal_log.json"


def load_json(path: Path, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, obj) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def _entry_to_point(e: dict) -> tuple[str, float] | None:
    date = e.get("date")
    price = e.get("btc_price")
    if not isinstance(date, str) or len(date) < 10:
        return None
    try:
        close = float(price)
    except Exception:
        return None
    return (date[:10], close)


def source_points(src: dict) -> list[tuple[str, float]]:
    points: list[tuple[str, float]] = []

    for e in src.get("entries", []) if isinstance(src.get("entries"), list) else []:
        pt = _entry_to_point(e)
        if pt is not None:
            points.append(pt)

    latest = src.get("latest") if isinstance(src.get("latest"), dict) else None
    if latest:
        pt = _entry_to_point(latest)
        if pt is not None:
            points.append(pt)

    dedup: dict[str, float] = {}
    for d, c in points:
        dedup[d] = c

    return sorted(dedup.items(), key=lambda x: x[0])


def compute_state(closes: list[float]) -> tuple[str, float, str]:
    close_today = closes[-1]

    if len(closes) < 51:
        sma_ref = mean(closes)
        return ("CASH", float(sma_ref), f"data_warmup")

    sma50_today = mean(closes[-50:])
    sma50_yday = mean(closes[-51:-1])
    close_yday = closes[-2]

    hold_cond = (
        (close_today > sma50_today)
        and (close_yday > sma50_yday)
        and (sma50_today >= sma50_yday)
    )

    if hold_cond:
        return ("HOLD", float(sma50_today), "trend_confirmation_2d")
    return ("CASH", float(sma50_today), "risk_off_not_confirmed")


def append_point(entries: list[dict], date: str, close: float) -> None:
    closes = [float(e["close"]) for e in entries if "close" in e]
    closes.append(float(close))
    state, sma, reason = compute_state(closes)
    entries.append(
        {
            "date": date,
            "state": state,
            "close": round(float(close), 2),
            "sma50": round(float(sma), 2),
            "reason": reason,
        }
    )


def main() -> int:
    src = load_json(SRC_LOG, None)
    if not isinstance(src, dict):
        print("ERROR: log.json missing or invalid")
        return 1

    points = source_points(src)
    if not points:
        print("ERROR: no usable (date, btc_price) points in log.json")
        return 1

    out = load_json(OUT_LOG, {"entries": []})
    if not isinstance(out, dict):
        out = {"entries": []}

    entries = out.get("entries")
    if not isinstance(entries, list):
        entries = []

    # Ensure deterministic order if file was manually edited.
    entries = sorted(entries, key=lambda e: str(e.get("date") or ""))

    last_date_existing = str(entries[-1].get("date") or "") if entries else ""

    # Append only new source dates greater than current tail.
    new_pts = [(d, c) for d, c in points if d > last_date_existing]
    if not new_pts:
        print("ALREADY_RAN_TODAY: btcsignal_log already has latest date")
        return 0

    for d, c in new_pts:
        append_point(entries, d, c)

    out["entries"] = entries
    save_json(OUT_LOG, out)
    print(
        f"OK_UPDATE: appended {len(new_pts)} entries, latest={entries[-1]['date']} "
        f"state={entries[-1]['state']} close={entries[-1]['close']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
