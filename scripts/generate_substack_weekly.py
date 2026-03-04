#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OPS_JSON = ROOT / "substack" / "weekly_ops_latest.json"
OUT = ROOT / "substack" / "weekly_latest.txt"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate Substack Weekly Ops draft")
    p.add_argument("--ops-json", dest="ops_json", default=str(OPS_JSON))
    p.add_argument("--out", dest="outfile", default=str(OUT))
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


def build_block(
    *,
    end_str: str,
    days_published: int,
    missing: list[str],
    max_delay: int | None,
    valid_days: int,
    match_report_status: str,
) -> str:
    missing_days = 7 - days_published

    lines = [
        "[SUBSTACK_WEEKLY]",
        f"Subject: Weekly Ops • week ending {end_str}",
        "",
        "Body:",
        f"Window: last 7 days (ending {end_str})",
        "",
        "Delivery:",
        f"Days published: {days_published}/7",
        f"Missing days: {missing_days}",
    ]
    if missing_days > 0:
        lines.append(f"Missing: {', '.join(missing)}")

    lines.extend(["", "Timeliness:"])
    if max_delay is None:
        lines.append("Delay: data unavailable")
    else:
        lines.append(f"Max delay: {max_delay} sec")

    lines.extend(
        [
            "",
            "Integrity:",
            f"Chain integrity (VALID days): {valid_days}/7",
            f"Last 30-day match report: {match_report_status}",
            "",
            "Links:",
            "Public log: https://btcsignal.org",
            f"Verification: https://btcsignal.org/verification/last30_match_report_{end_str}.txt",
            "",
            "Record-only. No prediction. No reasoning. No advice. Not investment advice.",
            "[/SUBSTACK_WEEKLY]",
            "",
        ]
    )
    return "\n".join(lines)


def require_non_null(payload: dict, key: str):
    v = payload.get(key)
    if v is None:
        raise ValueError(f"missing_required_field:{key}")
    return v


def main() -> int:
    args = parse_args()
    src = Path(args.ops_json)
    out = Path(args.outfile)

    if not src.exists():
        print(f"ERROR: ops json not found: {src}")
        return 1

    try:
        payload = load_json(src)
    except Exception as e:
        print(f"ERROR: failed to parse ops json: {type(e).__name__}: {e}")
        return 1

    try:
        end_str = str(require_non_null(payload, "week_ending"))
        days_published = int(require_non_null(payload, "days_published"))
        missing_days = int(require_non_null(payload, "missing_days"))
        missing = payload.get("missing_dates")
        if not isinstance(missing, list):
            raise ValueError("missing_required_field:missing_dates")
        if len(missing) != missing_days:
            raise ValueError("invalid_field:missing_days")
        max_delay = payload.get("max_delay_sec")
        if max_delay is not None:
            max_delay = int(max_delay)
        valid_days = int(require_non_null(payload, "valid_days"))
        match_report_status = str(require_non_null(payload, "last30_match_status"))
    except Exception as e:
        print(f"ERROR: invalid weekly ops payload: {e}")
        return 1

    block = build_block(
        end_str=end_str,
        days_published=days_published,
        missing=missing,
        max_delay=max_delay,
        valid_days=valid_days,
        match_report_status=match_report_status,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(block, encoding="utf-8")
    print(block)
    print(f"OK: wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
