#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIVE_LOG = ROOT / "btcsignal_log_live.json"
OUT = ROOT / "substack" / "status.json"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate Substack status heartbeat")
    p.add_argument("--status", default="success", choices=["success", "failure", "cancelled"], help="GitHub job.status value")
    p.add_argument("--in", dest="infile", default=str(LIVE_LOG))
    p.add_argument("--out", dest="outfile", default=str(OUT))
    p.add_argument("--run-id", default="")
    p.add_argument("--workflow", default="")
    return p.parse_args()


def load_latest(path: Path) -> tuple[str, str, str]:
    if not path.exists():
      return ("unknown", "unknown", "live_log_missing")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return ("unknown", "unknown", "live_log_parse_error")

    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, list) or not entries:
        return ("unknown", "unknown", "record_missing")

    last = entries[-1] if isinstance(entries[-1], dict) else {}
    d = str(last.get("date") or "unknown").strip()[:10] or "unknown"
    st = str(last.get("state") or "unknown").strip().upper()
    if st not in {"BTC", "CASH"}:
        st = "unknown"
    return (d, st, "ok")


def main() -> int:
    args = parse_args()
    src = Path(args.infile)
    dst = Path(args.outfile)

    latest_date, latest_state, base_status = load_latest(src)
    status = base_status
    reason = ""

    if args.status != "success":
        status = "pipeline_failed"
        reason = f"job_status={args.status}"
    elif base_status != "ok":
        status = "record_missing"
        reason = base_status

    payload = {
        "run_id": args.run_id or "unknown",
        "run_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "workflow": args.workflow or "Daily Archive Update",
        "last_known_record_date": latest_date,
        "last_known_state": latest_state,
        "status": status,
        "reason": reason or "none",
    }

    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))
    print(f"OK: wrote {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
