#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from urllib import request
from urllib.error import HTTPError, URLError


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Send BTCSIGNAL proof message to Discord")
    p.add_argument("--ops-status", default="PASS", help="PASS|SAFE_STOP|ERROR")
    p.add_argument("--log-path", default="", help="Optional btcsignal_log.json path override")
    return p.parse_args()


def load_json(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def btcsignal_paths(cli_path: str) -> list[Path]:
    if cli_path:
        return [Path(cli_path)]
    out: list[Path] = []
    env_path = os.getenv("BTCSIGNAL_LOG_PATH", "").strip()
    if env_path:
        out.append(Path(env_path))
    root = Path(__file__).resolve().parent.parent
    out.append(root / "btcsignal_log.json")
    return out


def load_latest(log_path: str) -> dict[str, str]:
    unavailable = {
        "status": "unavailable",
        "reason": "unavailable",
        "close": "unavailable",
        "sma50": "unavailable",
        "last3": "unavailable",
        "updated": "unavailable",
    }

    for path in btcsignal_paths(log_path):
        data = load_json(path)
        if not isinstance(data, dict):
            continue
        entries = data.get("entries")
        if not isinstance(entries, list) or not entries:
            continue

        last = entries[-1] if isinstance(entries[-1], dict) else {}
        raw_state = str(last.get("state") or "").strip().upper()
        status = raw_state if raw_state in {"HOLD", "CASH"} else "unavailable"
        reason = str(last.get("reason") or "").strip() or "unavailable"
        updated = str(last.get("date") or "").strip()[:10] or "unavailable"

        try:
            close = f"{float(last.get('close')):.2f}"
        except Exception:
            close = "unavailable"

        try:
            sma50 = f"{float(last.get('sma50')):.2f}"
        except Exception:
            sma50 = "unavailable"

        tail_states: list[str] = []
        for item in entries[-3:]:
            if not isinstance(item, dict):
                tail_states = []
                break
            s = str(item.get("state") or "").strip().upper()
            if s not in {"HOLD", "CASH"}:
                tail_states = []
                break
            tail_states.append(s)
        last3 = " -> ".join(tail_states) if len(tail_states) == 3 else "unavailable"

        return {
            "status": status,
            "reason": reason,
            "close": close,
            "sma50": sma50,
            "last3": last3,
            "updated": updated,
        }

    return unavailable


def build_message(*, ops_status: str, payload: dict[str, str]) -> str:
    return (
        "BTCSIGNAL (Proof)\n\n"
        f"Status: {payload['status']}\n"
        f"Reason: {payload['reason']}\n\n"
        f"Close: {payload['close']}\n"
        f"SMA50: {payload['sma50']}\n\n"
        f"Last 3: {payload['last3']}\n"
        f"Updated: {payload['updated']}\n\n"
        f"Ops: {ops_status}\n"
        "Safety-first. No noise."
    )


def post_discord(message: str) -> int:
    webhook = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook:
        print("INFO: DISCORD_WEBHOOK_URL not set; skip send")
        print(message)
        return 0

    body = json.dumps({"content": message}, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        webhook,
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "GitHubActions/1.0 (https://github.com/Naito-Dev/genki-btc-archive-public)",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=15):
            pass
        print("OK: Discord notification sent")
    except (HTTPError, URLError) as exc:
        # Notification channel failure must not break daily proof generation.
        print(f"WARN: Discord notification skipped ({exc})")
    return 0


def main() -> int:
    args = parse_args()
    ops_status = str(args.ops_status or "PASS").strip().upper()
    if ops_status not in {"PASS", "SAFE_STOP", "ERROR"}:
        ops_status = "ERROR"

    payload = load_latest(args.log_path)
    msg = build_message(ops_status=ops_status, payload=payload)
    return post_discord(msg)


if __name__ == "__main__":
    raise SystemExit(main())
