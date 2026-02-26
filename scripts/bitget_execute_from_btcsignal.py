#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
BTCSIGNAL_LOG = ROOT / "btcsignal_log.json"
STATE_PATH = ROOT / "data" / "btcsignal_execute_state.json"
EXEC_LOG = ROOT / "data" / "btcsignal_execution_log.csv"
SNAPSHOT_PATH = ROOT / "data" / "live_portfolio_snapshot.json"


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def append_exec_log(row: dict[str, Any]) -> None:
    EXEC_LOG.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "ts_utc",
        "signal_date",
        "state",
        "side",
        "qty",
        "price",
        "notional_usd",
        "status",
        "reason",
        "order_id",
        "dry_run",
    ]
    write_header = not EXEC_LOG.exists()
    with EXEC_LOG.open("a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if write_header:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in fields})


@dataclass
class ExecConfig:
    dry_run: bool
    enabled: bool
    symbol: str
    min_trade_usd: float
    min_btc_qty: float
    usdt_fraction: float
    btc_fraction: float


def cfg() -> ExecConfig:
    return ExecConfig(
        dry_run=os.getenv("DRY_RUN", "1").strip() != "0",
        enabled=os.getenv("BITGET_EXECUTE_ENABLED", "0").strip() == "1",
        symbol=os.getenv("BITGET_SYMBOL", "BTC/USDT").strip() or "BTC/USDT",
        min_trade_usd=float(os.getenv("MIN_TRADE_USD", "5")),
        min_btc_qty=float(os.getenv("MIN_BTC_QTY", "0.00001")),
        usdt_fraction=float(os.getenv("EXECUTE_USDT_FRACTION", "1.0")),
        btc_fraction=float(os.getenv("EXECUTE_BTC_FRACTION", "1.0")),
    )


def load_latest_signal() -> tuple[str, str] | None:
    data = load_json(BTCSIGNAL_LOG, {})
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, list) or not entries:
        return None
    last = entries[-1]
    if not isinstance(last, dict):
        return None
    date = str(last.get("date") or "").strip()[:10]
    state = str(last.get("state") or "").strip().upper()
    if len(date) != 10 or state not in {"HOLD", "CASH"}:
        return None
    return date, state


def fetch_price_public(symbol: str) -> float:
    # Public ticker fallback for DRY_RUN and safety checks.
    import urllib.request

    compact = symbol.replace("/", "")
    url = f"https://api.bitget.com/api/v2/spot/market/tickers?symbol={compact}"
    with urllib.request.urlopen(url, timeout=10) as r:
        payload = json.loads(r.read().decode("utf-8"))
    arr = payload.get("data") or []
    if not arr:
        raise RuntimeError("UNKNOWN_PRICE_STALE")
    last = arr[0].get("lastPr")
    if last is None:
        raise RuntimeError("UNKNOWN_PRICE_STALE")
    return float(last)


def load_snapshot_balances() -> tuple[float, float]:
    snap = load_json(SNAPSHOT_PATH, {})
    if not isinstance(snap, dict):
        return (float(os.getenv("DRY_USDT_BALANCE", "100")), float(os.getenv("DRY_BTC_BALANCE", "0")))
    usdt = snap.get("usdt_balance")
    btc = snap.get("btc_balance")
    try:
        usdt_f = float(usdt)
    except Exception:
        usdt_f = float(os.getenv("DRY_USDT_BALANCE", "100"))
    try:
        btc_f = float(btc)
    except Exception:
        btc_f = float(os.getenv("DRY_BTC_BALANCE", "0"))
    return usdt_f, btc_f


def load_state() -> dict[str, Any]:
    return load_json(STATE_PATH, {}) if STATE_PATH.exists() else {}


def save_state(signal_date: str, state: str, status: str) -> None:
    save_json(
        STATE_PATH,
        {
            "signal_date": signal_date,
            "state": state,
            "status": status,
            "updated_at_utc": now_utc_iso(),
        },
    )


def create_exchange() -> Any:
    import ccxt  # type: ignore

    key = os.getenv("BITGET_API_KEY", "").strip()
    sec = os.getenv("BITGET_API_SECRET", "").strip()
    pw = os.getenv("BITGET_API_PASSPHRASE", "").strip()
    if not key or not sec or not pw:
        raise RuntimeError("MISSING_API_CREDENTIALS")

    ex = ccxt.bitget(
        {
            "apiKey": key,
            "secret": sec,
            "password": pw,
            "enableRateLimit": True,
            "options": {"defaultType": "spot"},
        }
    )
    ex.load_markets()
    return ex


def live_balances(exchange: Any) -> tuple[float, float]:
    b = exchange.fetch_balance()
    usdt = float((b.get("free") or {}).get("USDT", 0.0))
    btc = float((b.get("free") or {}).get("BTC", 0.0))
    return usdt, btc


def main() -> int:
    c = cfg()
    ts = now_utc_iso()

    sig = load_latest_signal()
    if not sig:
        append_exec_log(
            {
                "ts_utc": ts,
                "status": "SAFE_STOP",
                "reason": "BTCSIGNAL_UNAVAILABLE",
                "dry_run": int(c.dry_run),
            }
        )
        print("SAFE_STOP: BTCSIGNAL_UNAVAILABLE")
        return 0

    signal_date, state = sig

    st = load_state()
    if st.get("signal_date") == signal_date and st.get("state") == state:
        append_exec_log(
            {
                "ts_utc": ts,
                "signal_date": signal_date,
                "state": state,
                "status": "SKIPPED",
                "reason": "ALREADY_RAN_TODAY",
                "dry_run": int(c.dry_run),
            }
        )
        print("SKIPPED: ALREADY_RAN_TODAY")
        return 0

    if not c.enabled:
        append_exec_log(
            {
                "ts_utc": ts,
                "signal_date": signal_date,
                "state": state,
                "status": "SKIPPED",
                "reason": "EXECUTION_DISABLED",
                "dry_run": int(c.dry_run),
            }
        )
        save_state(signal_date, state, "SKIPPED")
        print("SKIPPED: EXECUTION_DISABLED")
        return 0

    side = "BUY" if state == "HOLD" else "SELL"

    try:
        if c.dry_run:
            price = fetch_price_public(c.symbol)
            usdt_free, btc_free = load_snapshot_balances()
        else:
            ex = create_exchange()
            ticker = ex.fetch_ticker(c.symbol)
            price = float(ticker.get("last") or 0.0)
            if price <= 0:
                raise RuntimeError("UNKNOWN_PRICE_STALE")
            usdt_free, btc_free = live_balances(ex)
    except Exception as e:
        reason = f"PRICE_OR_BALANCE_FETCH_FAILED:{type(e).__name__}:{e}"
        append_exec_log(
            {
                "ts_utc": ts,
                "signal_date": signal_date,
                "state": state,
                "side": side,
                "price": "",
                "status": "SAFE_STOP",
                "reason": reason,
                "dry_run": int(c.dry_run),
            }
        )
        print(f"SAFE_STOP: {reason}")
        return 0

    if state == "HOLD":
        trade_usd = max(0.0, usdt_free * c.usdt_fraction)
        qty = trade_usd / price if price > 0 else 0.0
    else:
        qty = max(0.0, btc_free * c.btc_fraction)
        trade_usd = qty * price

    if trade_usd < c.min_trade_usd or qty < c.min_btc_qty:
        append_exec_log(
            {
                "ts_utc": ts,
                "signal_date": signal_date,
                "state": state,
                "side": side,
                "qty": f"{qty:.8f}",
                "price": f"{price:.2f}",
                "notional_usd": f"{trade_usd:.2f}",
                "status": "SKIPPED",
                "reason": "SKIP_FUNDS_INSUFFICIENT",
                "dry_run": int(c.dry_run),
            }
        )
        save_state(signal_date, state, "SKIPPED")
        print("SKIPPED: SKIP_FUNDS_INSUFFICIENT")
        return 0

    order_id = ""
    status = "SIMULATED" if c.dry_run else "EXECUTED"
    reason = "DRY_RUN_SYNC" if c.dry_run else "LIVE_SYNC"

    if not c.dry_run:
        try:
            ex = create_exchange()
            order = ex.create_order(c.symbol, "market", side.lower(), qty)
            order_id = str(order.get("id") or "")
        except Exception as e:
            reason = f"ORDER_SUBMIT_FAILED:{type(e).__name__}:{e}"
            status = "SAFE_STOP"
            append_exec_log(
                {
                    "ts_utc": ts,
                    "signal_date": signal_date,
                    "state": state,
                    "side": side,
                    "qty": f"{qty:.8f}",
                    "price": f"{price:.2f}",
                    "notional_usd": f"{trade_usd:.2f}",
                    "status": status,
                    "reason": reason,
                    "dry_run": int(c.dry_run),
                }
            )
            print(f"SAFE_STOP: {reason}")
            return 0

    append_exec_log(
        {
            "ts_utc": ts,
            "signal_date": signal_date,
            "state": state,
            "side": side,
            "qty": f"{qty:.8f}",
            "price": f"{price:.2f}",
            "notional_usd": f"{trade_usd:.2f}",
            "status": status,
            "reason": reason,
            "order_id": order_id,
            "dry_run": int(c.dry_run),
        }
    )
    save_state(signal_date, state, status)
    print(f"{status}: state={state} side={side} qty={qty:.8f} notional={trade_usd:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
