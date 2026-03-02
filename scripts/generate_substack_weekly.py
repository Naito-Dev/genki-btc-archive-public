#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIVE_LOG = ROOT / "btcsignal_log_live.json"
LOGS_DIR = ROOT / "logs"
OUT = ROOT / "substack" / "weekly_latest.txt"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate Substack Weekly Ops draft")
    p.add_argument("--in", dest="infile", default=str(LIVE_LOG))
    p.add_argument("--logs-dir", dest="logs_dir", default=str(LOGS_DIR))
    p.add_argument("--out", dest="outfile", default=str(OUT))
    p.add_argument("--end-date", dest="end_date", default="", help="YYYY-MM-DD (optional)")
    return p.parse_args()


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def parse_day(s: str) -> date | None:
    s = (s or "").strip()[:10]
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def window_days(end: date) -> list[date]:
    start = end - timedelta(days=6)
    return [start + timedelta(days=i) for i in range(7)]


def collect_delay_values(logs_dir: Path, days: list[date]) -> list[int]:
    vals: list[int] = []
    for d in days:
        p = logs_dir / f"{d.isoformat()}.json"
        if not p.exists():
            continue
        try:
            j = load_json(p)
        except Exception:
            continue
        raw = None
        for k in ("delay_sec", "publish_delay_sec", "PUBLISH_DELAY_SEC"):
            if isinstance(j, dict) and k in j:
                raw = j.get(k)
                break
        if raw is None:
            continue
        try:
            n = int(raw)
            if n >= 0:
                vals.append(n)
        except Exception:
            continue
    return vals


def build_block(end_str: str, days_published: int, missing: list[str], max_delay: int | None) -> str:
    missing_days = 7 - days_published
    lines = [
        "[SUBSTACK_WEEKLY]",
        f"Subject: Weekly Ops • week ending {end_str}",
        "",
        "Body:",
        f"Window: last 7 days (ending {end_str})",
        f"Days published: {days_published}/7",
        f"Missing days: {missing_days}",
    ]
    if missing_days > 0:
        lines.append(f"Missing: {', '.join(missing)}")

    if max_delay is None:
        lines.append("Delay: data unavailable")
    else:
        lines.append(f"Max delay: {max_delay} sec")

    lines.extend(
        [
            "",
            "Record-only. No prediction. No reasoning. No advice. Not investment advice.",
            "Public: https://btcsignal.org",
            "[/SUBSTACK_WEEKLY]",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    src = Path(args.infile)
    logs_dir = Path(args.logs_dir)
    out = Path(args.outfile)

    if args.end_date.strip():
        end = parse_day(args.end_date)
        if end is None:
            print("ERROR: invalid --end-date; expected YYYY-MM-DD")
            return 1
    else:
        end = datetime.now().date()

    if not src.exists():
        print(f"ERROR: source not found: {src}")
        return 1

    try:
        data = load_json(src)
    except Exception as e:
        print(f"ERROR: failed to parse source: {type(e).__name__}: {e}")
        return 1

    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        print("ERROR: required inputs missing (entries)")
        return 1

    days = window_days(end)
    day_set = {d.isoformat() for d in days}
    published_set = set()
    for e in entries:
        if not isinstance(e, dict):
            continue
        d = str(e.get("date") or "").strip()[:10]
        if d in day_set:
            published_set.add(d)

    ordered_days = [d.isoformat() for d in days]
    missing = [d for d in ordered_days if d not in published_set]
    days_published = len(published_set)

    delay_vals = collect_delay_values(logs_dir, days)
    max_delay = max(delay_vals) if delay_vals else None

    block = build_block(end.isoformat(), days_published, missing, max_delay)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(block, encoding="utf-8")
    print(block)
    print(f"OK: wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
