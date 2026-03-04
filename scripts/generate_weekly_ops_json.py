#!/usr/bin/env python3
from __future__ import annotations

import argparse
import glob
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = ROOT / "logs"
VERIFY_DIR = ROOT / "verification"
OUT = ROOT / "substack" / "weekly_ops_latest.json"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate weekly ops facts JSON (single source)")
    p.add_argument("--logs-dir", default=str(LOGS_DIR))
    p.add_argument("--verification-dir", default=str(VERIFY_DIR))
    p.add_argument("--out", default=str(OUT))
    p.add_argument("--end-date", default="", help="YYYY-MM-DD (optional, UTC date)")
    return p.parse_args()


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def parse_day(s: str) -> date | None:
    raw = str(s or "").strip()[:10]
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None


def window_days(end: date) -> list[date]:
    start = end - timedelta(days=6)
    return [start + timedelta(days=i) for i in range(7)]


def day_log(logs_dir: Path, d: date) -> dict | None:
    p = logs_dir / f"{d.isoformat()}.json"
    if not p.exists():
        return None
    try:
        j = load_json(p)
    except Exception:
        return None
    return j if isinstance(j, dict) else None


def is_day_ok(j: dict | None) -> bool:
    if not isinstance(j, dict):
        return False
    # "ok" day is defined by immutable chain integrity.
    return str(j.get("chain_integrity") or "").strip().upper() == "VALID"


def extract_delay(j: dict | None) -> int | None:
    if not isinstance(j, dict):
        return None
    for k in ("delay_sec", "publish_delay_sec", "PUBLISH_DELAY_SEC"):
        v = j.get(k)
        if v is None:
            continue
        try:
            n = int(v)
        except Exception:
            continue
        if n >= 0:
            return n
    return None


def last30_match_status(verify_dir: Path, start: date, end: date) -> str:
    paths = sorted(glob.glob(str(verify_dir / "last30_match_report_*.txt")))
    latest = None
    latest_day = None
    for p in paths:
        name = Path(p).name
        ds = name.replace("last30_match_report_", "").replace(".txt", "")
        d = parse_day(ds)
        if d is None:
            continue
        if d < start or d > end:
            continue
        if latest_day is None or d > latest_day:
            latest_day = d
            latest = Path(p)
    if latest is None:
        return "unavailable"
    try:
        txt = latest.read_text(encoding="utf-8").upper()
    except Exception:
        return "unavailable"
    if "PASS" in txt:
        return "PASS"
    if "FAIL" in txt:
        return "FAIL"
    return "unavailable"


def main() -> int:
    args = parse_args()
    logs_dir = Path(args.logs_dir)
    verify_dir = Path(args.verification_dir)
    out = Path(args.out)

    end = parse_day(args.end_date) if args.end_date.strip() else datetime.now(timezone.utc).date()
    if end is None:
        print("ERROR: invalid --end-date; expected YYYY-MM-DD")
        return 1

    days = window_days(end)
    rows = [(d, day_log(logs_dir, d)) for d in days]

    ok_days = [d.isoformat() for d, j in rows if is_day_ok(j)]
    missing_dates = [d.isoformat() for d, j in rows if not is_day_ok(j)]
    delay_values = [v for _, j in rows if (v := extract_delay(j)) is not None]
    max_delay = max(delay_values) if delay_values else None
    valid_days = len(ok_days)
    match = last30_match_status(verify_dir, days[0], days[-1])

    payload = {
        "week_ending": end.isoformat(),
        "window_dates": [d.isoformat() for d in days],
        "days_published": len(ok_days),
        "missing_days": len(missing_dates),
        "missing_dates": missing_dates,
        "max_delay_sec": max_delay,
        "valid_days": valid_days,
        "last30_match_status": match,
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))
    print(f"OK: wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
