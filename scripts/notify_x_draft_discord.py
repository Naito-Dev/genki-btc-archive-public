#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from urllib import request
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parent.parent
BTCSIGNAL_LOG = ROOT / "btcsignal_log.json"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Post daily X draft to Discord")
    p.add_argument("--dashboard-url", default=os.getenv("DASHBOARD_URL", "https://naito-dev.github.io/genki-btc-archive-public/"))
    p.add_argument("--ops-status", default=os.getenv("OPS_STATUS", "PASS"))
    return p.parse_args()


def load_latest() -> tuple[dict, list[str], int]:
    try:
        data = json.loads(BTCSIGNAL_LOG.read_text(encoding="utf-8"))
    except Exception:
        return ({}, [], 0)

    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, list) or not entries:
        return ({}, [], 0)

    last = entries[-1] if isinstance(entries[-1], dict) else {}
    states: list[str] = []
    for item in entries[-3:]:
        if isinstance(item, dict):
            st = str(item.get("state") or "").strip().upper()
            if st in {"HOLD", "CASH"}:
                states.append(st)
    return (last, states, len(entries))


def make_message(last: dict, states: list[str], day_n: int, ops_status: str, dashboard_url: str) -> str:
    status = str(last.get("state") or "").strip().upper()
    if status not in {"HOLD", "CASH"}:
        status = "unavailable"

    reason = str(last.get("reason") or "").strip() or "unavailable"
    updated = str(last.get("date") or "").strip()[:10] or "unavailable"
    last3 = "->".join(states) if len(states) == 3 else "unavailable"

    day = day_n if day_n > 0 else 0
    ops = ops_status.strip().upper()
    if ops not in {"PASS", "SAFE_STOP", "ERROR"}:
        ops = "unavailable"

    return (
        f"Genki Verification — Day {day}/365\n\n"
        f"Status: {status}\n"
        f"Reason: {reason}\n"
        f"Ops: {ops}\n"
        f"Updated: {updated}\n\n"
        f"Last 3: {last3}\n\n"
        f"No prediction. Just the record.\n"
        f"{dashboard_url}"
    )


def post_message(msg: str) -> int:
    webhook = os.getenv("DISCORD_X_WEBHOOK_URL", "").strip()
    if not webhook:
        print("INFO: DISCORD_X_WEBHOOK_URL not set; skip send")
        print(msg)
        return 0

    payload = json.dumps({"content": msg}, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        webhook,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "GitHubActions/1.0 (https://github.com/Naito-Dev/genki-btc-archive-public)",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=15):
            pass
        print("OK: X draft Discord notification sent")
    except (HTTPError, URLError) as exc:
        print(f"WARN: X draft Discord notification skipped ({exc})")
    return 0


def main() -> int:
    args = parse_args()
    last, states, day_n = load_latest()
    msg = make_message(last, states, day_n, args.ops_status, args.dashboard_url)
    return post_message(msg)


if __name__ == "__main__":
    raise SystemExit(main())
