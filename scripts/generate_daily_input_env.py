#!/usr/bin/env python3
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib import error, parse, request

ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / "daily_input.env"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sign_message(secret: str, message: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def build_headers(method: str, request_path: str, query: dict[str, str], body: str) -> dict[str, str]:
    ts_ms = str(int(datetime.now(timezone.utc).timestamp() * 1000))
    query_str = parse.urlencode(query)
    path_with_query = request_path + (f"?{query_str}" if query_str else "")
    prehash = f"{ts_ms}{method.upper()}{path_with_query}{body}"

    key = os.getenv("BITGET_API_KEY", "").strip()
    secret = os.getenv("BITGET_API_SECRET", "").strip()
    passphrase = os.getenv("BITGET_API_PASSPHRASE", "").strip()
    sig = sign_message(secret, prehash)

    return {
        "ACCESS-KEY": key,
        "ACCESS-SIGN": sig,
        "ACCESS-TIMESTAMP": ts_ms,
        "ACCESS-PASSPHRASE": passphrase,
        "Content-Type": "application/json",
        "locale": "en-US",
        "User-Agent": "genki-btc-archive-public/daily-input-env",
    }


def write_env(payload: dict[str, str]) -> None:
    lines = [f"{k}={v}" for k, v in payload.items()]
    OUT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_pending(reason: str) -> None:
    now = utc_now_iso()
    payload = {
        "SNAPSHOT_STATUS": "SYNC_PENDING",
        "BALANCE_SOURCE": "unavailable",
        "BALANCE_TS_UTC": now,
        "BTC_UNITS": "",
        "USDT_UNITS": "",
        "SNAPSHOT_REASON": reason,
    }
    write_env(payload)
    print(f"PENDING: {reason}")


def to_float(v) -> float | None:
    try:
        return float(v)
    except Exception:
        return None


def main() -> int:
    key = os.getenv("BITGET_API_KEY", "").strip()
    secret = os.getenv("BITGET_API_SECRET", "").strip()
    passphrase = os.getenv("BITGET_API_PASSPHRASE", "").strip()
    if not key or not secret or not passphrase:
        write_pending("missing_bitget_credentials")
        return 0

    method = "GET"
    request_path = "/api/v2/spot/account/assets"
    query = {}
    body = ""
    headers = build_headers(method, request_path, query, body)
    url = f"https://api.bitget.com{request_path}"

    try:
        req = request.Request(url, headers=headers, method=method)
        with request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
    except (error.HTTPError, error.URLError, TimeoutError) as exc:
        write_pending(f"bitget_http_error:{exc}")
        return 0

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        write_pending("bitget_json_decode_error")
        return 0

    if str(data.get("code")) != "00000":
        write_pending(f"bitget_code:{data.get('code')}")
        return 0

    items = data.get("data")
    if not isinstance(items, list):
        write_pending("bitget_data_not_list")
        return 0

    btc_units = None
    usdt_units = None
    for item in items:
        if not isinstance(item, dict):
            continue
        coin = str(item.get("coin") or "").upper()
        avail = to_float(item.get("available"))
        frozen = to_float(item.get("frozen"))
        total = None
        if avail is not None and frozen is not None:
            total = avail + frozen
        elif avail is not None:
            total = avail
        if total is None:
            continue
        if coin == "BTC":
            btc_units = total
        elif coin == "USDT":
            usdt_units = total

    if btc_units is None or usdt_units is None:
        write_pending("missing_btc_or_usdt_balance")
        return 0

    now = utc_now_iso()
    payload = {
        "SNAPSHOT_STATUS": "SYNCED",
        "BALANCE_SOURCE": "BITGET_READONLY",
        "BALANCE_TS_UTC": now,
        "BTC_UNITS": f"{btc_units:.10f}",
        "USDT_UNITS": f"{usdt_units:.10f}",
    }
    write_env(payload)
    print(f"SYNCED: BTC_UNITS={payload['BTC_UNITS']} USDT_UNITS={payload['USDT_UNITS']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
