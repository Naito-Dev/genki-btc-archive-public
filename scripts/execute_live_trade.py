#!/usr/bin/env python3
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib import parse, request

import ccxt


ROOT = Path(__file__).resolve().parent.parent
LOG_JSON = ROOT / "public/log.json"
OUT_DIR = ROOT / "output"
TRADES_CSV = OUT_DIR / "trades_live.csv"
STATE_JSON = OUT_DIR / "state_live.json"
DAILY_INPUT_ENV = ROOT / "daily_input.env"
LOCKS_DIR = ROOT / "locks"
def sync_audit_path(ts_utc: str | None = None) -> Path:
    src = ts_utc or now_utc()
    try:
        dt = datetime.fromisoformat(src.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        ym = dt.astimezone(timezone.utc).strftime("%Y-%m")
    except Exception:
        ym = datetime.now(timezone.utc).strftime("%Y-%m")
    return OUT_DIR / f"execution_sync_audit_{ym}.ndjson"


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        key = k.strip()
        val = v.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = val


def env_truthy(key: str, default: bool = False) -> bool:
    v = os.getenv(key)
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on", "y", "t")


def parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip("'").strip('"')
    return out


def _to_float(v: str | None) -> float | None:
    if v is None or str(v).strip() == "":
        return None
    try:
        return float(v)
    except Exception:
        return None


def _is_fresh_utc(ts_raw: str | None, max_age_min: int) -> bool:
    if not ts_raw:
        return False
    try:
        ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age_sec = (datetime.now(timezone.utc) - ts.astimezone(timezone.utc)).total_seconds()
        return 0 <= age_sec <= (max_age_min * 60)
    except Exception:
        return False


def load_balance_snapshot() -> dict:
    env = parse_env_file(DAILY_INPUT_ENV)
    status = str(env.get("SNAPSHOT_STATUS", "")).upper()
    source = str(env.get("BALANCE_SOURCE", ""))
    ts = env.get("BALANCE_TS_UTC")
    btc = _to_float(env.get("BTC_UNITS"))
    usdt = _to_float(env.get("USDT_UNITS"))
    ok = (
        status == "SYNCED"
        and source == "BITGET_READONLY"
        and _is_fresh_utc(ts, BALANCE_MAX_AGE_MIN)
        and btc is not None and btc >= 0
        and usdt is not None and usdt >= 0
    )
    return {"ok": ok, "status": status or None, "source": source or None, "ts": ts, "btc": btc, "usdt": usdt}


# Security-first: use a single canonical secrets location, never print values.
load_env_file(ROOT / "secrets/.env.live")

SYMBOL = os.getenv("BITGET_SYMBOL", "BTC/USDT")
TRADE_FRACTION = float(os.getenv("TRADE_FRACTION", "0.99"))
FAIL_STOP_COUNT = int(os.getenv("FAIL_STOP_COUNT", "3"))
TRADING_ENABLED = env_truthy("TRADING_ENABLED", False)
DRY_RUN = env_truthy("DRY_RUN", True)
MAX_TRADE_USD = min(float(os.getenv("MAX_TRADE_USD", "10")), 10.0)
MIN_TRADE_USD = float(os.getenv("MIN_TRADE_USD", "5"))
USDT_RESERVE = float(os.getenv("USDT_RESERVE", "1"))
BTC_DUST_RESERVE = float(os.getenv("BTC_DUST_RESERVE", "0"))
PLACEHOLDER_PATTERNS = (
    r"^your[_-]?",
    r"^change[_-]?me$",
    r"^xxxx+$",
    r"^test$",
    r"^example$",
)
MAX_FORCE_USD = 10.0
BALANCE_MAX_AGE_MIN = int(os.getenv("BALANCE_MAX_AGE_MIN", "20"))
ARMED = os.getenv("ARMED", "").strip().upper() == "YES"
SYNC_THRESHOLD = float(os.getenv("SYNC_THRESHOLD", "0.01"))
SYNC_MAX_LOOPS = int(os.getenv("SYNC_MAX_LOOPS", "3"))
SYNC_MIN_ORDER_BTC = float(os.getenv("SYNC_MIN_ORDER_BTC", "0"))
DUST_CLEANUP_RATIO_THRESHOLD = float(os.getenv("DUST_CLEANUP_RATIO_THRESHOLD", "0.0005"))
PRICE_MAX_AGE_SEC = int(os.getenv("PRICE_MAX_AGE_SEC", "30"))
RETRY_BACKOFF_SEC = (2, 4, 8)


@dataclass
class Decision:
    signal: str
    side: str
    qty: float
    price: float
    btc: float
    usdt: float
    equity_usd: float
    target_btc_ratio: float
    current_btc_ratio: float
    trade_usd: float = 0.0
    delta_usd_raw: float = 0.0
    client_order_id: str = "NONE"
    post_btc_units: float | None = None
    post_usdt_units: float | None = None
    post_equity_usd: float | None = None
    reason_code: str = ""


@dataclass
class LogContext:
    allocation: int
    btc_price_ref: float
    trigger: str


@dataclass
class SyncResult:
    decision: Decision
    order_id: str
    order_status: str
    sync_status: str
    error_type: str = ""


def cli_flag(name: str) -> bool:
    return any(arg == name for arg in sys.argv[1:])


def cli_value(name: str) -> str | None:
    for i, arg in enumerate(sys.argv[1:]):
        if arg == name and (i + 2) <= len(sys.argv[1:]):
            return sys.argv[1:][i + 1]
    return None


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def now_jst() -> datetime:
    return datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=9)))


def run_date_jst() -> str:
    return now_jst().strftime("%Y-%m-%d")


def build_run_id() -> str:
    ts = now_jst().strftime("%Y%m%d-%H%M%S")
    short = hashlib.sha1(now_utc().encode("utf-8")).hexdigest()[:6]
    return f"{run_date_jst()}_{ts}_{short}"


def build_sync_run_id(parent_run_id: str) -> str:
    return f"{parent_run_id}-sync-{uuid.uuid4().hex[:8]}"


def force_sync_day_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def acquire_force_sync_day_lock() -> str:
    path = f"/tmp/force_sync_{force_sync_day_key()}.lock"
    if os.path.exists(path):
        raise RuntimeError("FORCE_SYNC_LOCK_EXISTS")
    with open(path, "w", encoding="utf-8") as f:
        f.write(now_utc() + "\n")
    return path


def precheck_force_sync_day_lock() -> None:
    path = f"/tmp/force_sync_{force_sync_day_key()}.lock"
    if os.path.exists(path):
        raise RuntimeError("FORCE_SYNC_LOCK_EXISTS")


def remove_force_sync_day_lock(path: str | None) -> None:
    if not path:
        return
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def acquire_daily_lock(*, allow_rerun: bool = False) -> tuple[str, str]:
    LOCKS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(LOCKS_DIR, 0o700)
    except OSError:
        pass
    day = run_date_jst()
    run_id = build_run_id()
    lock_path = LOCKS_DIR / f"exec_{day}.lock"
    if lock_path.exists() and not allow_rerun:
        raise RuntimeError("ALREADY_RAN_TODAY")
    lock_path.write_text(run_id + "\n", encoding="utf-8")
    try:
        os.chmod(lock_path, 0o600)
    except OSError:
        pass
    return run_id, str(lock_path)


def send_telegram(msg: str) -> None:
    token = os.getenv("TELEGRAM_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip() or os.getenv("TELEGRAM_CHATID", "").strip()
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    body = parse.urlencode({"chat_id": chat_id, "text": msg}).encode("utf-8")
    req = request.Request(url, data=body, method="POST")
    with request.urlopen(req, timeout=15):
        pass


def send_discord(msg: str) -> None:
    webhook = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook:
        return
    data = json.dumps({"content": msg}, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        webhook,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=15):
        pass


def load_log_latest_allocation() -> int:
    if not LOG_JSON.exists():
        raise RuntimeError(f"missing log file: {LOG_JSON}")
    data = json.loads(LOG_JSON.read_text(encoding="utf-8"))
    latest = data.get("latest") or {}
    alloc = int(latest.get("allocation", 0))
    return alloc


def _target_ratio_from_allocation(alloc: int) -> float:
    a = int(alloc or 0)
    if a >= 100:
        return 1.0
    if a >= 70:
        return 0.7
    if a >= 30:
        return 0.3
    return 0.0


def _rebalance_plan(price: float, btc: float, usdt: float, target_btc_ratio: float) -> tuple[str, str, float, float, float]:
    """
    Returns (signal, side, qty, trade_usd, delta_usd_raw).
    qty is unrounded BTC quantity.
    """
    equity = usdt + btc * max(price, 0.0)
    if price <= 0 or equity <= 0:
        return ("HOLD", "sell", 0.0, 0.0, 0.0)

    current_ratio = (btc * price / equity) if equity > 0 else 0.0
    delta_usd_raw = (target_btc_ratio - current_ratio) * equity
    trade_usd = min(abs(delta_usd_raw) * TRADE_FRACTION, MAX_TRADE_USD)
    if trade_usd < MIN_TRADE_USD:
        side = "buy" if delta_usd_raw > 0 else "sell"
        return ("HOLD", side, 0.0, 0.0, delta_usd_raw)

    if delta_usd_raw > 0:
        side = "buy"
        affordable_usd = max(0.0, usdt - USDT_RESERVE)
        trade_usd = min(trade_usd, affordable_usd)
        if trade_usd < MIN_TRADE_USD:
            return ("HOLD", side, 0.0, 0.0, delta_usd_raw)
        qty = trade_usd / price
        return ("BUY", side, qty, trade_usd, delta_usd_raw)

    side = "sell"
    sellable_btc = max(0.0, btc - BTC_DUST_RESERVE)
    max_sell_usd = sellable_btc * price
    trade_usd = min(trade_usd, max_sell_usd)
    if trade_usd < MIN_TRADE_USD:
        return ("HOLD", side, 0.0, 0.0, delta_usd_raw)
    qty = trade_usd / price
    return ("SELL", side, qty, trade_usd, delta_usd_raw)


def load_log_context() -> LogContext:
    if not LOG_JSON.exists():
        raise RuntimeError(f"missing log file: {LOG_JSON}")
    data = json.loads(LOG_JSON.read_text(encoding="utf-8"))
    latest = data.get("latest") or {}
    alloc = int(latest.get("allocation", 0))
    # Prefer current schema field (`btc_price`), then legacy fallback (`btc_price_ref`).
    price_raw = latest.get("btc_price")
    if price_raw in (None, "", 0, 0.0):
        price_raw = latest.get("btc_price_ref")
    price = float(price_raw or 0.0)
    trigger = str(latest.get("trigger") or "")
    return LogContext(allocation=alloc, btc_price_ref=price, trigger=trigger)


def _forced_allocation_from_trigger(trigger: str) -> str:
    t = (trigger or "").strip()
    if t.startswith("FORCED_ALLOCATION:"):
        return t.split(":", 1)[1].strip()
    return ""


def load_state() -> dict:
    if not STATE_JSON.exists():
        return {
            "failure_count": 0,
            "halted": False,
            "last_signal": None,
            "last_order_id": None,
            "last_equity": None,
            "balances": {},
            "open_orders": [],
            "updated_at_utc": None,
        }
    state = json.loads(STATE_JSON.read_text(encoding="utf-8"))
    if "open_orders" not in state or not isinstance(state.get("open_orders"), list):
        state["open_orders"] = []
    return state


def save_state(state: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    STATE_JSON.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(STATE_JSON, 0o600)
    except OSError:
        pass


def append_trade_row(row: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    exists = TRADES_CSV.exists()
    with TRADES_CSV.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "ts_utc",
                "action",
                "symbol",
                "side",
                "qty",
                "price",
                "order_id",
                "status",
                "equity_usd",
                "btc",
                "usdt",
                "signal",
                "target_btc_ratio",
                "current_btc_ratio",
                "trade_usd",
                "delta_usd_raw",
                "forced_allocation",
                "forced",
                "client_order_id",
                "post_btc_units",
                "post_usdt_units",
                "post_equity_usd",
                "reason_code",
            ],
        )
        if not exists:
            writer.writeheader()
        writer.writerow(row)
    try:
        os.chmod(TRADES_CSV, 0o600)
    except OSError:
        pass


def append_sync_audit(row: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    audit_path = sync_audit_path(str(row.get("timestamp_utc") or now_utc()))
    with audit_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    try:
        os.chmod(audit_path, 0o600)
    except OSError:
        pass


def append_force_sync_stop_audit(*, run_id: str, mode: str, reason_code: str, open_orders_count: int | None = None) -> None:
    append_sync_audit(
        {
            "run_id": run_id or "N/A",
            "timestamp_utc": now_utc(),
            "target_ratio": None,
            "actual_ratio_before": None,
            "actual_ratio_after": None,
            "order_id": "",
            "order_status": "not_submitted",
            "sync_status": "STOPPED",
            "error_type": reason_code,
            "mode": mode,
            "stop_reason": reason_code,
            "open_orders_count": open_orders_count,
        }
    )


def count_symbol_open_orders(ex: ccxt.bitget, symbol: str) -> int:
    orders = call_exchange_with_retry("fetch_open_orders", ex.fetch_open_orders, symbol)
    return len(orders or [])


def make_exchange() -> ccxt.bitget:
    api_key = os.getenv("BITGET_API_KEY", "").strip()
    secret = os.getenv("BITGET_API_SECRET", "").strip()
    passphrase = os.getenv("BITGET_PASSPHRASE", "").strip()
    placeholder = any(
        re.search(p, v, flags=re.IGNORECASE)
        for p in PLACEHOLDER_PATTERNS
        for v in (api_key, secret, passphrase)
    )
    if placeholder:
        raise RuntimeError("placeholder API credentials detected")
    if not api_key or not secret or not passphrase:
        raise RuntimeError("missing BITGET_API_KEY / BITGET_API_SECRET / BITGET_PASSPHRASE")
    ex = ccxt.bitget(
        {
            "apiKey": api_key,
            "secret": secret,
            "password": passphrase,
            "enableRateLimit": True,
            "options": {"defaultType": "spot"},
        }
    )
    return ex


def fetch_balance_snapshot_ro() -> tuple[float, float, float | None]:
    proc = subprocess.run(
        [str(ROOT / ".venv/bin/python"), str(ROOT / "scripts/fetch_bitget_balance.py")],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError("POST_SNAPSHOT_FETCH_FAILED")
    try:
        data = json.loads(proc.stdout.strip())
        btc = float(data.get("btc_free"))
        usdt = float(data.get("usdt_free"))
        total = data.get("equity_total_usd")
        total_f = float(total) if total is not None else None
        return btc, usdt, total_f
    except Exception:
        raise RuntimeError("POST_SNAPSHOT_PARSE_FAILED")


def build_decision(ex: ccxt.bitget) -> Decision:
    alloc = load_log_latest_allocation()
    target_btc_ratio = _target_ratio_from_allocation(alloc)

    market = ex.market(SYMBOL)
    ticker = call_exchange_with_retry("fetch_ticker", ex.fetch_ticker, SYMBOL)
    price = float(ticker["last"])
    bal = call_exchange_with_retry("fetch_balance", ex.fetch_balance)

    btc_ccy = SYMBOL.split("/")[0]
    usdt_ccy = SYMBOL.split("/")[1]
    btc = float((bal.get(btc_ccy) or {}).get("free") or 0.0)
    usdt = float((bal.get(usdt_ccy) or {}).get("free") or 0.0)
    equity = usdt + btc * price
    current_ratio = (btc * price / equity) if equity > 0 else 0.0

    signal, side, raw_qty, trade_usd, delta_usd_raw = _rebalance_plan(price, btc, usdt, target_btc_ratio)
    qty = float(ex.amount_to_precision(SYMBOL, raw_qty)) if raw_qty > 0 else 0.0
    if signal in ("BUY", "SELL") and qty <= 0:
        signal = "HOLD"
        trade_usd = 0.0

    # Extra exchange min-cost guard after amount precision.
    market = ex.market(SYMBOL)
    min_cost = float((((market.get("limits") or {}).get("cost") or {}).get("min")) or 0.0)
    if signal in ("BUY", "SELL") and min_cost > 0 and (qty * price) < min_cost:
        signal = "HOLD"
        qty = 0.0
        trade_usd = 0.0

    return Decision(signal, side, qty, price, btc, usdt, equity, target_btc_ratio, current_ratio, trade_usd, delta_usd_raw)


def build_forced_decision(ex: ccxt.bitget, force_signal: str, force_qty_usd: float) -> Decision:
    alloc = load_log_latest_allocation()
    target_btc_ratio = _target_ratio_from_allocation(alloc)

    ticker = call_exchange_with_retry("fetch_ticker", ex.fetch_ticker, SYMBOL)
    last = float(ticker.get("last") or 0.0)
    bid = float(ticker.get("bid") or 0.0)
    ask = float(ticker.get("ask") or 0.0)
    if force_signal == "BUY":
        raw_price = ask if ask > 0 else last
    else:
        raw_price = bid if bid > 0 else last
    if raw_price <= 0:
        raise RuntimeError("unable to resolve valid price for forced order")
    price = float(ex.price_to_precision(SYMBOL, raw_price))
    bal = call_exchange_with_retry("fetch_balance", ex.fetch_balance)
    market = ex.market(SYMBOL)

    btc_ccy = SYMBOL.split("/")[0]
    usdt_ccy = SYMBOL.split("/")[1]
    btc = float((bal.get(btc_ccy) or {}).get("free") or 0.0)
    usdt = float((bal.get(usdt_ccy) or {}).get("free") or 0.0)
    equity = usdt + btc * price
    current_ratio = (btc * price / equity) if equity > 0 else 0.0

    min_cost = float((((market.get("limits") or {}).get("cost") or {}).get("min")) or 0.0)
    if min_cost > 0 and force_qty_usd < min_cost:
        raise RuntimeError(f"FORCE_QTY_USD below exchange minimum cost ({min_cost} USDT)")

    requested_qty = force_qty_usd / price
    if force_signal == "BUY":
        max_qty = (usdt * TRADE_FRACTION) / price
        qty = min(requested_qty, max_qty)
        qty = float(ex.amount_to_precision(SYMBOL, max(qty, 0.0)))
        signal = "BUY" if qty > 0 else "HOLD"
        side = "buy"
    else:
        # Spot safety: never sell more than current free BTC.
        max_qty = btc * TRADE_FRACTION
        qty = min(requested_qty, max_qty)
        qty = float(ex.amount_to_precision(SYMBOL, max(qty, 0.0)))
        signal = "SELL" if qty > 0 else "HOLD"
        side = "sell"

    return Decision(signal, side, qty, price, btc, usdt, equity, target_btc_ratio, current_ratio, force_qty_usd if qty > 0 else 0.0, 0.0)


def normalize_status(order: dict) -> str:
    raw = str(order.get("status") or "").strip().lower()
    filled = float(order.get("filled") or 0.0)
    remaining = order.get("remaining")
    remaining_f = float(remaining) if remaining is not None else None

    if raw in ("closed", "filled"):
        return "filled"
    if raw in ("canceled", "cancelled"):
        return "canceled"
    if raw in ("rejected",):
        return "rejected"
    if raw in ("expired",):
        return "expired"
    if raw in ("open", "new"):
        if filled > 0 and (remaining_f is None or remaining_f > 0):
            return "partially_filled"
        return "open"
    if filled > 0 and (remaining_f is None or remaining_f > 0):
        return "partially_filled"
    return "unknown"


def _is_allowed_target_ratio(target_ratio: float) -> bool:
    allowed = (0.0, 0.3, 0.7, 1.0)
    return any(abs(target_ratio - a) < 1e-9 for a in allowed)


def should_execute_sync(target_ratio: float, actual_ratio: float, force_sync: bool = False, threshold: float = 0.01) -> tuple[bool, str]:
    if not force_sync:
        return False, "FORCE_SYNC_OFF"
    if not _is_allowed_target_ratio(target_ratio):
        return False, "INVALID_FORCE_SYNC_TARGET"
    if abs(actual_ratio - target_ratio) <= threshold:
        return False, "SKIP_THRESHOLD"
    return True, "SYNC_REQUIRED"


def ratio_from_balances(price: float, btc: float, usdt: float) -> tuple[float, float]:
    equity = usdt + btc * max(price, 0.0)
    ratio = (btc * price / equity) if equity > 0 and price > 0 else 0.0
    return ratio, equity


def is_unknown_order_error(exc: Exception) -> bool:
    msg = f"{type(exc).__name__}:{exc}".lower()
    keys = ("timeout", "timed out", "network", "connection", "temporarily", "unavailable", "request")
    return any(k in msg for k in keys)


def is_network_retryable_error(exc: Exception) -> bool:
    retryable = (
        ccxt.NetworkError,
        ccxt.RequestTimeout,
        ccxt.ExchangeNotAvailable,
        ccxt.DDoSProtection,
    )
    return isinstance(exc, retryable)


def call_exchange_with_retry(label: str, fn, *args, **kwargs):
    last_exc: Exception | None = None
    max_retries = len(RETRY_BACKOFF_SEC)
    for attempt in range(max_retries + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            if not is_network_retryable_error(exc):
                raise
            last_exc = exc
            if attempt >= max_retries:
                raise
            delay = RETRY_BACKOFF_SEC[attempt]
            print(
                f"[MarketEdge Exec RETRY] label={label} retry_count={attempt+1}/{max_retries} "
                f"error_type={type(exc).__name__} wait_sec={delay}"
            )
            time.sleep(delay)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"{label}_retry_failed")


def ensure_ticker_fresh_or_raise(ex: ccxt.bitget, ticker: dict, max_age_sec: int) -> None:
    ts_ms = ticker.get("timestamp")
    if ts_ms in (None, ""):
        raise RuntimeError("UNKNOWN_PRICE_STALE")
    try:
        ts = int(ts_ms)
    except Exception:
        raise RuntimeError("UNKNOWN_PRICE_STALE")
    try:
        server_ms = call_exchange_with_retry("fetch_time", ex.fetch_time)
    except Exception:
        raise RuntimeError("UNKNOWN_PRICE_STALE")
    if server_ms is None:
        raise RuntimeError("UNKNOWN_PRICE_STALE")
    age_ms = int(server_ms) - ts
    if age_ms < 0 or age_ms > (max_age_sec * 1000):
        raise RuntimeError("UNKNOWN_PRICE_STALE")


def execute_target_sync(
    ex: ccxt.bitget,
    *,
    run_id: str,
    target_ratio: float,
    threshold: float,
    min_trade_usd: float,
    max_loops: int,
    dry_run: bool = False,
) -> SyncResult:
    btc_ccy, usdt_ccy = SYMBOL.split("/")
    sync_run_id = build_sync_run_id(run_id)
    order_ids: list[str] = []
    order_status = "not_submitted"
    error_type = ""
    reason = "SYNC_REQUIRED"
    dust_cleanup_executed = False
    would_force_dust_sell = False
    dust_btc_before = None
    dust_btc_after = None

    try:
        ticker = call_exchange_with_retry("fetch_ticker", ex.fetch_ticker, SYMBOL)
        ensure_ticker_fresh_or_raise(ex, ticker, PRICE_MAX_AGE_SEC)
        price = float(ticker["last"])
        bal = call_exchange_with_retry("fetch_balance", ex.fetch_balance)
        btc = float((bal.get(btc_ccy) or {}).get("free") or 0.0)
        usdt = float((bal.get(usdt_ccy) or {}).get("free") or 0.0)
        actual_before, equity_before = ratio_from_balances(price, btc, usdt)
        before_btc, before_usdt = btc, usdt

        append_sync_audit(
            {
                "run_id": sync_run_id,
                "timestamp_utc": now_utc(),
                "target_ratio": target_ratio,
                "actual_ratio_before": round(actual_before, 8),
                "actual_ratio_after": None,
                "order_id": "",
                "order_status": "start",
                "sync_status": "START",
                "error_type": "",
            }
        )

        should, reason = should_execute_sync(target_ratio, actual_before, force_sync=True, threshold=threshold)
        if not should:
            actual_after, equity_after = actual_before, equity_before
            signal = "HOLD"
            side = "buy" if target_ratio > actual_before else "sell"
            qty = 0.0
            trade_usd = 0.0
        else:
            sim_qty = 0.0
            sim_trade_usd = 0.0
            sim_side = "buy"
            for _ in range(max(1, max_loops)):
                ticker = call_exchange_with_retry("fetch_ticker", ex.fetch_ticker, SYMBOL)
                ensure_ticker_fresh_or_raise(ex, ticker, PRICE_MAX_AGE_SEC)
                price = float(ticker["last"])
                bal = call_exchange_with_retry("fetch_balance", ex.fetch_balance)
                btc = float((bal.get(btc_ccy) or {}).get("free") or 0.0)
                usdt = float((bal.get(usdt_ccy) or {}).get("free") or 0.0)
                actual_ratio, equity = ratio_from_balances(price, btc, usdt)

                if abs(actual_ratio - target_ratio) <= threshold:
                    reason = "COMPLETE"
                    break

                delta_ratio = target_ratio - actual_ratio
                side = "buy" if delta_ratio > 0 else "sell"
                required_usd = abs(delta_ratio) * equity

                if side == "buy":
                    available_usdt = max(0.0, usdt - USDT_RESERVE)
                    if available_usdt < min_trade_usd:
                        reason = "SKIP_FUNDS_INSUFFICIENT"
                        break
                    order_usd = min(required_usd, available_usdt)
                    if order_usd < min_trade_usd:
                        reason = "SKIP_FUNDS_INSUFFICIENT"
                        break
                    qty_raw = order_usd / price
                else:
                    sellable_btc = max(0.0, btc - BTC_DUST_RESERVE)
                    if sellable_btc <= 0 or sellable_btc < SYNC_MIN_ORDER_BTC:
                        reason = "SKIP_DUST"
                        break
                    max_sell_usd = sellable_btc * price
                    order_usd = min(required_usd, max_sell_usd)
                    if order_usd < min_trade_usd:
                        reason = "SKIP_DUST"
                        break
                    qty_raw = order_usd / price

                qty_ord = float(ex.amount_to_precision(SYMBOL, max(qty_raw, 0.0)))
                if qty_ord <= 0:
                    reason = "SKIP_DUST"
                    break

                market = ex.market(SYMBOL)
                min_cost = float((((market.get("limits") or {}).get("cost") or {}).get("min")) or 0.0)
                if min_cost > 0 and (qty_ord * price) < min_cost:
                    reason = "SKIP_DUST"
                    break

                if dry_run:
                    sim_qty = qty_ord
                    sim_trade_usd = order_usd
                    sim_side = side
                    order_status = "simulated"
                    reason = "DRY_RUN_SYNC"
                    break

                client_order_id = f"GENKI-SYNC-{run_date_jst()}-{now_jst().strftime('%H%M%S')}-{run_id[-6:]}"
                try:
                    # Bitget market-buy param quirks: use limit order to ensure BASE currency (BTC) amount works for buy
                    order = call_exchange_with_retry(
                        "create_order",
                        ex.create_order,
                        SYMBOL,
                        "limit",
                        side,
                        qty_ord,
                        price,
                        {"clientOid": client_order_id}
                    )
                except Exception as exc:
                    if is_unknown_order_error(exc):
                        reason = "UNKNOWN_ORDER_STATE"
                        error_type = type(exc).__name__
                        break
                    raise

                order_id = str(order.get("id") or "")
                if not order_id:
                    reason = "UNKNOWN_ORDER_STATE"
                    error_type = "missing_order_id"
                    break
                order_ids.append(order_id)
                order_status = normalize_status(order)
                if order_status in ("open", "new", "partially_filled", "unknown", "submitted"):
                    try:
                        fetched = call_exchange_with_retry("fetch_order", ex.fetch_order, order_id, SYMBOL)
                        order_status = normalize_status(fetched)
                    except Exception:
                        reason = "UNKNOWN_ORDER_STATE"
                        error_type = "fetch_order_failed"
                        break

                if order_status in ("filled", "closed"):
                    reason = "COMPLETE"
                    continue
                if order_status in ("canceled", "rejected", "expired"):
                    reason = "ERROR"
                    break

            ticker = call_exchange_with_retry("fetch_ticker", ex.fetch_ticker, SYMBOL)
            ensure_ticker_fresh_or_raise(ex, ticker, PRICE_MAX_AGE_SEC)
            price = float(ticker["last"])
            bal = call_exchange_with_retry("fetch_balance", ex.fetch_balance)
            btc = float((bal.get(btc_ccy) or {}).get("free") or 0.0)
            usdt = float((bal.get(usdt_ccy) or {}).get("free") or 0.0)
            actual_after, equity_after = ratio_from_balances(price, btc, usdt)
            side = "buy" if target_ratio > actual_before else "sell"
            if side == "buy":
                qty = max(0.0, btc - before_btc)
                trade_usd = max(0.0, before_usdt - usdt)
                signal = "BUY" if qty > 0 else "HOLD"
            else:
                qty = max(0.0, before_btc - btc)
                trade_usd = max(0.0, usdt - before_usdt)
                signal = "SELL" if qty > 0 else "HOLD"
            if dry_run and reason == "DRY_RUN_SYNC":
                side = sim_side
                qty = sim_qty
                trade_usd = sim_trade_usd
                signal = "BUY" if side == "buy" and qty > 0 else ("SELL" if side == "sell" and qty > 0 else "HOLD")

            # One-shot post-sell dust cleanup to avoid long-term residual accumulation.
            dust_ratio_after = (btc * price / equity_after) if equity_after > 0 and price > 0 else 0.0
            qty_threshold_exceeded = (SYNC_MIN_ORDER_BTC > 0 and btc > SYNC_MIN_ORDER_BTC)
            needs_dust_cleanup = side == "sell" and (dust_ratio_after > DUST_CLEANUP_RATIO_THRESHOLD or qty_threshold_exceeded)
            if needs_dust_cleanup:
                dust_btc_before = btc
                if dry_run:
                    would_force_dust_sell = True
                else:
                    cleanup_qty_raw = max(0.0, btc - BTC_DUST_RESERVE)
                    cleanup_qty = float(ex.amount_to_precision(SYMBOL, cleanup_qty_raw))
                    market = ex.market(SYMBOL)
                    min_cost = float((((market.get("limits") or {}).get("cost") or {}).get("min")) or 0.0)
                    if cleanup_qty > 0 and (min_cost <= 0 or (cleanup_qty * price) >= min_cost):
                        client_order_id = f"GENKI-DUST-{run_date_jst()}-{now_jst().strftime('%H%M%S')}-{run_id[-6:]}"
                        try:
                            cleanup_order = call_exchange_with_retry(
                                "create_order",
                                ex.create_order,
                                SYMBOL,
                                "market",
                                "sell",
                                cleanup_qty,
                                None,
                                {"clientOid": client_order_id},
                            )
                        except Exception as exc:
                            if is_unknown_order_error(exc):
                                reason = "UNKNOWN_ORDER_STATE"
                                error_type = type(exc).__name__
                            else:
                                raise
                        else:
                            cleanup_order_id = str(cleanup_order.get("id") or "")
                            if cleanup_order_id:
                                order_ids.append(cleanup_order_id)
                            order_status = normalize_status(cleanup_order)
                            dust_cleanup_executed = True
                            ticker = call_exchange_with_retry("fetch_ticker", ex.fetch_ticker, SYMBOL)
                            ensure_ticker_fresh_or_raise(ex, ticker, PRICE_MAX_AGE_SEC)
                            price = float(ticker["last"])
                            bal = call_exchange_with_retry("fetch_balance", ex.fetch_balance)
                            btc = float((bal.get(btc_ccy) or {}).get("free") or 0.0)
                            usdt = float((bal.get(usdt_ccy) or {}).get("free") or 0.0)
                            actual_after, equity_after = ratio_from_balances(price, btc, usdt)
                            qty = max(0.0, before_btc - btc)
                            trade_usd = max(0.0, usdt - before_usdt)
                            signal = "SELL" if qty > 0 else "HOLD"
                            if reason == "SKIP_DUST":
                                reason = "COMPLETE"
                dust_btc_after = btc

        sync_status = reason
        if reason == "SYNC_REQUIRED":
            sync_status = "ERROR"
        append_sync_audit(
            {
                "run_id": sync_run_id,
                "timestamp_utc": now_utc(),
                "target_ratio": target_ratio,
                "actual_ratio_before": round(actual_before, 8),
                "actual_ratio_after": round(actual_after, 8),
                "order_id": ",".join(order_ids),
                "order_status": order_status,
                "sync_status": sync_status,
                "error_type": error_type,
                "dust_cleanup_executed": dust_cleanup_executed,
                "would_force_dust_sell": would_force_dust_sell,
                "dust_btc_before": round(dust_btc_before, 12) if isinstance(dust_btc_before, (int, float)) else None,
                "dust_btc_after": round(dust_btc_after, 12) if isinstance(dust_btc_after, (int, float)) else None,
            }
        )

        d = Decision(
            signal=signal,
            side=side,
            qty=float(ex.amount_to_precision(SYMBOL, qty)),
            price=price,
            btc=btc,
            usdt=usdt,
            equity_usd=equity_after,
            target_btc_ratio=target_ratio,
            current_btc_ratio=actual_after,
            trade_usd=trade_usd,
            delta_usd_raw=(target_ratio - actual_after) * equity_after,
            client_order_id="",
            reason_code=sync_status,
        )
        return SyncResult(d, ",".join(order_ids), order_status, sync_status, error_type)

    except Exception as exc:
        reason = str(exc) if str(exc) else "ERROR"
        if reason not in ("UNKNOWN_PRICE_STALE", "UNKNOWN_ORDER_STATE", "INVALID_FORCE_SYNC_TARGET"):
            reason = "ERROR"
        error_type = type(exc).__name__
        append_sync_audit(
            {
                "run_id": sync_run_id,
                "timestamp_utc": now_utc(),
                "target_ratio": target_ratio,
                "actual_ratio_before": None,
                "actual_ratio_after": None,
                "order_id": ",".join(order_ids),
                "order_status": order_status,
                "sync_status": reason,
                "error_type": error_type,
            }
        )
        d = Decision(
            signal="HOLD",
            side="sell",
            qty=0.0,
            price=0.0,
            btc=0.0,
            usdt=0.0,
            equity_usd=0.0,
            target_btc_ratio=target_ratio,
            current_btc_ratio=0.0,
            trade_usd=0.0,
            delta_usd_raw=0.0,
            reason_code=reason,
        )
        return SyncResult(d, ",".join(order_ids), order_status, reason, error_type)


def execute_target_sync_dryrun_offline(
    *,
    run_id: str,
    target_ratio: float,
    threshold: float,
    min_trade_usd: float,
    btc: float,
    usdt: float,
    price: float,
) -> SyncResult:
    sync_run_id = build_sync_run_id(run_id)
    dust_cleanup_executed = False
    would_force_dust_sell = False
    dust_btc_before = None
    dust_btc_after = None
    if price <= 0:
        append_sync_audit(
            {
                "run_id": sync_run_id,
                "timestamp_utc": now_utc(),
                "target_ratio": target_ratio,
                "actual_ratio_before": None,
                "actual_ratio_after": None,
                "order_id": "",
                "order_status": "not_submitted",
                "sync_status": "STOPPED",
                "error_type": "DRYRUN_PRICE_UNAVAILABLE",
            }
        )
        d = Decision(
            signal="HOLD",
            side="sell",
            qty=0.0,
            price=price,
            btc=btc,
            usdt=usdt,
            equity_usd=usdt + btc * max(price, 0.0),
            target_btc_ratio=target_ratio,
            current_btc_ratio=0.0,
            trade_usd=0.0,
            delta_usd_raw=0.0,
            reason_code="DRYRUN_PRICE_UNAVAILABLE",
        )
        return SyncResult(d, "", "not_submitted", "STOPPED", "DRYRUN_PRICE_UNAVAILABLE")

    actual_before, equity_before = ratio_from_balances(price, btc, usdt)
    append_sync_audit(
        {
            "run_id": sync_run_id,
            "timestamp_utc": now_utc(),
            "target_ratio": target_ratio,
            "actual_ratio_before": round(actual_before, 8),
            "actual_ratio_after": None,
            "order_id": "",
            "order_status": "start",
            "sync_status": "START",
            "error_type": "",
        }
    )

    should, reason = should_execute_sync(target_ratio, actual_before, force_sync=True, threshold=threshold)
    side = "buy" if target_ratio > actual_before else "sell"
    qty = 0.0
    trade_usd = 0.0
    btc_after = btc
    usdt_after = usdt
    order_status = "not_submitted"

    if should:
        delta_ratio = target_ratio - actual_before
        required_usd = abs(delta_ratio) * equity_before
        if side == "buy":
            available_usdt = max(0.0, usdt - USDT_RESERVE)
            if available_usdt < min_trade_usd:
                reason = "SKIP_FUNDS_INSUFFICIENT"
            else:
                order_usd = min(required_usd, available_usdt)
                if order_usd < min_trade_usd:
                    reason = "SKIP_FUNDS_INSUFFICIENT"
                else:
                    qty = max(0.0, order_usd / price)
                    trade_usd = order_usd
                    usdt_after = max(0.0, usdt - order_usd)
                    btc_after = btc + qty
                    reason = "DRY_RUN_SYNC"
                    order_status = "simulated"
        else:
            sellable_btc = max(0.0, btc - BTC_DUST_RESERVE)
            if sellable_btc <= 0 or sellable_btc < SYNC_MIN_ORDER_BTC:
                reason = "SKIP_DUST"
            else:
                max_sell_usd = sellable_btc * price
                order_usd = min(required_usd, max_sell_usd)
                if order_usd < min_trade_usd:
                    reason = "SKIP_DUST"
                else:
                    qty = max(0.0, order_usd / price)
                    trade_usd = order_usd
                    btc_after = max(0.0, btc - qty)
                    usdt_after = usdt + order_usd
                    reason = "DRY_RUN_SYNC"
                    order_status = "simulated"

    actual_after, equity_after = ratio_from_balances(price, btc_after, usdt_after)
    dust_ratio_after = (btc_after * price / equity_after) if equity_after > 0 and price > 0 else 0.0
    qty_threshold_exceeded = (SYNC_MIN_ORDER_BTC > 0 and btc_after > SYNC_MIN_ORDER_BTC)
    if side == "sell" and (dust_ratio_after > DUST_CLEANUP_RATIO_THRESHOLD or qty_threshold_exceeded):
        would_force_dust_sell = True
        dust_btc_before = btc_after
        dust_btc_after = 0.0

    signal = "BUY" if (side == "buy" and qty > 0) else ("SELL" if (side == "sell" and qty > 0) else "HOLD")
    append_sync_audit(
        {
            "run_id": sync_run_id,
            "timestamp_utc": now_utc(),
            "target_ratio": target_ratio,
            "actual_ratio_before": round(actual_before, 8),
            "actual_ratio_after": round(actual_after, 8),
            "order_id": "",
            "order_status": order_status,
            "sync_status": reason,
            "error_type": "",
            "dust_cleanup_executed": dust_cleanup_executed,
            "would_force_dust_sell": would_force_dust_sell,
            "dust_btc_before": round(dust_btc_before, 12) if isinstance(dust_btc_before, (int, float)) else None,
            "dust_btc_after": round(dust_btc_after, 12) if isinstance(dust_btc_after, (int, float)) else None,
        }
    )
    d = Decision(
        signal=signal,
        side=side,
        qty=qty,
        price=price,
        btc=btc_after,
        usdt=usdt_after,
        equity_usd=equity_after,
        target_btc_ratio=target_ratio,
        current_btc_ratio=actual_after,
        trade_usd=trade_usd,
        delta_usd_raw=(target_ratio - actual_after) * equity_after,
        reason_code=reason,
    )
    return SyncResult(d, "", order_status, reason, "")


def execute_zero_sync(
    ex: ccxt.bitget,
    *,
    run_id: str,
    target_ratio: float,
    threshold: float,
    min_trade_usd: float,
    max_loops: int,
) -> tuple[Decision, str, str]:
    res = execute_target_sync(
        ex,
        run_id=run_id,
        target_ratio=0.0,
        threshold=threshold,
        min_trade_usd=min_trade_usd,
        max_loops=max_loops,
    )
    return res.decision, res.order_id, res.order_status


def poll_open_orders(ex: ccxt.bitget, state: dict) -> None:
    open_orders = list(state.get("open_orders", []))
    if not open_orders:
        return

    still_open = []
    for item in open_orders:
        order_id = str(item.get("order_id") or "")
        symbol = str(item.get("symbol") or SYMBOL)
        side = str(item.get("side") or "")
        if not order_id:
            continue
        try:
            o = call_exchange_with_retry("fetch_order", ex.fetch_order, order_id, symbol)
        except Exception:
            # Don't fail the main run for status polling issues.
            still_open.append(item)
            continue

        normalized = normalize_status(o)
        prev_status = str(item.get("last_status") or "submitted")
        if normalized != prev_status:
            append_trade_row(
                {
                    "ts_utc": now_utc(),
                    "action": "ORDER_STATUS_UPDATE",
                    "symbol": symbol,
                    "side": side,
                    "qty": float(o.get("filled") or 0.0),
                    "price": float(o.get("average") or o.get("price") or 0.0),
                    "order_id": order_id,
                    "status": normalized,
                    "equity_usd": "",
                    "btc": "",
                    "usdt": "",
                    "signal": "",
                    "target_btc_ratio": "",
                    "current_btc_ratio": "",
                    "trade_usd": "",
                    "delta_usd_raw": "",
                    "forced_allocation": "",
                    "forced": item.get("forced", False),
                    "client_order_id": "",
                    "post_btc_units": "",
                    "post_usdt_units": "",
                    "post_equity_usd": "",
                    "reason_code": "",
                }
            )
            item["last_status"] = normalized

        if normalized not in ("filled", "canceled", "rejected", "expired", "closed"):
            still_open.append(item)

    state["open_orders"] = still_open


def main() -> None:
    os.umask(0o077)
    run_id = "NONE"
    lock_state = "none"
    state = load_state()
    snapshot = load_balance_snapshot()
    state["snapshot_status"] = snapshot.get("status")
    state["balance_source"] = snapshot.get("source")
    state["balances_ts_utc"] = snapshot.get("ts")

    if os.getenv("HALT_RESET", "0").strip() == "1":
        state["failure_count"] = 0
        state["halted"] = False
        state["updated_at_utc"] = now_utc()
        save_state(state)

    force_sync = env_truthy("FORCE_SYNC", False) or cli_flag("--force-sync")
    should_connect_exchange = TRADING_ENABLED and ((not DRY_RUN) or force_sync)
    forced = False
    force_sync_lock_path: str | None = None
    force_sync_target_ratio: float | None = None
    open_orders_count: int | None = None
    threshold_raw = cli_value("--sync-threshold")
    sync_threshold = SYNC_THRESHOLD
    if threshold_raw is not None:
        try:
            sync_threshold = float(threshold_raw)
        except Exception:
            raise RuntimeError("SYNC_THRESHOLD_INVALID")
    forced_allocation = ""
    guard_reason = ""

    if state.get("halted") and should_connect_exchange:
        print("HALTED: failure threshold reached; set HALT_RESET=1 to resume.")
        return

    try:
        if os.getenv("EXEC_FORCE_RERUN", "0").strip() == "1" and not DRY_RUN and not force_sync:
            raise RuntimeError("EXEC_FORCE_RERUN_LIVE_FORBIDDEN")

        allow_rerun = force_sync and (os.getenv("EXEC_FORCE_RERUN", "0").strip() == "1")

        if env_truthy("EXEC_SKIP_LOCK", False):
            run_id = build_run_id()
            lock_state = "skipped"
        else:
            run_id, _ = acquire_daily_lock(allow_rerun=allow_rerun)
            lock_state = "acquired"

        ctx_latest = load_log_context()
        if should_connect_exchange and not snapshot.get("ok"):
            guard_reason = "UNSYNCED_SNAPSHOT"
        if should_connect_exchange and not guard_reason:
            forced_allocation = _forced_allocation_from_trigger(ctx_latest.trigger)
            force_mode = os.getenv("FORCE_MODE", "0").strip() == "1"
            force_signal = os.getenv("FORCE_SIGNAL", "").strip().upper()
            force_qty_raw = os.getenv("FORCE_QTY_USD", "0").strip()
            try:
                force_qty_usd = float(force_qty_raw)
            except ValueError:
                raise RuntimeError("FORCE_QTY_USD must be numeric")

            if force_mode:
                if not (
                    ARMED
                    and (not DRY_RUN)
                    and TRADING_ENABLED
                ):
                    raise RuntimeError("FORCE_MODE requires ARMED=YES && DRY_RUN=0 && TRADING_ENABLED=true")
                if force_signal not in ("BUY", "SELL"):
                    raise RuntimeError("FORCE_SIGNAL must be BUY or SELL")
                if force_qty_usd <= 0 or force_qty_usd > MAX_FORCE_USD:
                    raise RuntimeError("FORCE_QTY_USD invalid or exceeds MAX_FORCE_USD")
                forced = True
            if force_sync:
                forced_target_raw = os.getenv("FORCE_SYNC_TARGET_RATIO", "").strip()
                if forced_target_raw != "":
                    if not DRY_RUN:
                        raise RuntimeError("INVALID_STATE")
                    try:
                        forced_target = float(forced_target_raw)
                    except Exception:
                        raise RuntimeError("INVALID_FORCE_SYNC_TARGET")
                    if not _is_allowed_target_ratio(forced_target):
                        raise RuntimeError("INVALID_FORCE_SYNC_TARGET")
                    force_sync_target_ratio = forced_target

            required = {
                "ARMED": ARMED,
                "TRADING_ENABLED": TRADING_ENABLED,
                "DRY_RUN": ((not DRY_RUN) or force_sync),
            }
            if not all(required.values()):
                guard_reason = "GUARD_BLOCKED"

        if should_connect_exchange and guard_reason:
            snap_btc = float(snapshot.get("btc") or 0.0)
            snap_usdt = float(snapshot.get("usdt") or 0.0)
            price = float(ctx_latest.btc_price_ref or 0.0)
            equity = snap_usdt + snap_btc * max(price, 0.0)
            current_ratio = (snap_btc * price / equity) if equity > 0 and price > 0 else 0.0
            d = Decision(
                signal="NO_TRADE",
                side="sell",
                qty=0.0,
                price=price,
                btc=snap_btc,
                usdt=snap_usdt,
                equity_usd=equity,
                target_btc_ratio=_target_ratio_from_allocation(int(ctx_latest.allocation or 0)),
                current_btc_ratio=current_ratio,
                trade_usd=0.0,
                delta_usd_raw=0.0,
                reason_code=guard_reason,
            )
            order_id = ""
            status = "not_submitted"
        elif should_connect_exchange and force_sync and DRY_RUN:
            sync_target_ratio = force_sync_target_ratio if force_sync_target_ratio is not None else _target_ratio_from_allocation(int(ctx_latest.allocation or 0))
            snap_btc = float(snapshot.get("btc") or 0.0)
            snap_usdt = float(snapshot.get("usdt") or 0.0)
            price = float(ctx_latest.btc_price_ref or 0.0)
            sync_result = execute_target_sync_dryrun_offline(
                run_id=run_id,
                target_ratio=sync_target_ratio,
                threshold=sync_threshold,
                min_trade_usd=MIN_TRADE_USD,
                btc=snap_btc,
                usdt=snap_usdt,
                price=price,
            )
            d = sync_result.decision
            order_id = sync_result.order_id
            status = sync_result.order_status
        elif should_connect_exchange:
            if force_sync:
                precheck_force_sync_day_lock()
                if PRICE_MAX_AGE_SEC <= 0:
                    raise RuntimeError("UNKNOWN_PRICE_STALE")
            ex = make_exchange()
            call_exchange_with_retry("load_markets", ex.load_markets)
            poll_open_orders(ex, state)
            if force_sync and not (ARMED and TRADING_ENABLED):
                raise RuntimeError("FORCE_SYNC requires ARMED=YES && TRADING_ENABLED=true")
            if not DRY_RUN:
                try:
                    open_orders_count = count_symbol_open_orders(ex, SYMBOL)
                except Exception as exc:
                    raise RuntimeError("OPEN_ORDERS_CHECK_FAILED") from exc
                if int(open_orders_count or 0) > 0:
                    raise RuntimeError("OPEN_ORDERS_EXIST")
            if force_sync:
                force_sync_lock_path = acquire_force_sync_day_lock()
            d = build_forced_decision(ex, force_signal, force_qty_usd) if forced else build_decision(ex)
            order_id = ""
            status = "not_submitted"

            if force_sync:
                try:
                    sync_target_ratio = force_sync_target_ratio if force_sync_target_ratio is not None else d.target_btc_ratio
                    sync_result = execute_target_sync(
                        ex,
                        run_id=run_id,
                        target_ratio=sync_target_ratio,
                        threshold=sync_threshold,
                        min_trade_usd=MIN_TRADE_USD,
                        max_loops=SYNC_MAX_LOOPS,
                        dry_run=DRY_RUN,
                    )
                    d = sync_result.decision
                    order_id = sync_result.order_id
                    status = sync_result.order_status
                    if sync_result.sync_status in ("UNKNOWN_ORDER_STATE", "UNKNOWN_PRICE_STALE", "ERROR"):
                        alert = (
                            f"[MarketEdge Exec ERROR] mode=LIVE run_id={run_id} reason_code={sync_result.sync_status} "
                            f"target_ratio={d.target_btc_ratio:.4f} actual_ratio={d.current_btc_ratio:.4f} "
                            f"order_id={order_id or 'NONE'}"
                        )
                        send_discord(alert)
                finally:
                    remove_force_sync_day_lock(force_sync_lock_path)
                    force_sync_lock_path = None
            elif d.signal in ("BUY", "SELL") and d.qty > 0:
                client_order_id = f"GENKI-{run_date_jst()}-{now_jst().strftime('%H%M%S')}-{run_id[-6:]}"
                if forced:
                    if d.side == "buy":
                        order = call_exchange_with_retry(
                            "create_limit_buy_order",
                            ex.create_limit_buy_order,
                            SYMBOL,
                            d.qty,
                            d.price,
                            {"clientOid": client_order_id},
                        )
                    else:
                        order = call_exchange_with_retry(
                            "create_limit_sell_order",
                            ex.create_limit_sell_order,
                            SYMBOL,
                            d.qty,
                            d.price,
                            {"clientOid": client_order_id},
                        )
                else:
                    # Keep non-forced live orders on limit too (Bitget market-buy param quirks).
                    if d.side == "buy":
                        order = call_exchange_with_retry(
                            "create_limit_buy_order",
                            ex.create_limit_buy_order,
                            SYMBOL,
                            d.qty,
                            d.price,
                            {"clientOid": client_order_id},
                        )
                    else:
                        order = call_exchange_with_retry(
                            "create_limit_sell_order",
                            ex.create_limit_sell_order,
                            SYMBOL,
                            d.qty,
                            d.price,
                            {"clientOid": client_order_id},
                        )
                order_id = str(order.get("id") or "")
                status = str(order.get("status") or "submitted")
                d.client_order_id = client_order_id
                post_btc, post_usdt, post_equity_total = fetch_balance_snapshot_ro()
                d.post_btc_units = post_btc
                d.post_usdt_units = post_usdt
                d.post_equity_usd = post_equity_total if post_equity_total is not None else (post_usdt + post_btc * d.price)
                if order_id and status in ("submitted", "open", "new"):
                    existing = {str(x.get("order_id") or "") for x in state.get("open_orders", [])}
                    if order_id not in existing:
                        state.setdefault("open_orders", []).append(
                            {
                                "order_id": order_id,
                                "symbol": SYMBOL,
                                "side": d.side,
                                "created_at_utc": now_utc(),
                                "last_status": "submitted",
                                "forced": forced,
                            }
                        )
            if not force_sync:
                d.reason_code = ""
        else:
            ctx = ctx_latest
            forced_allocation = _forced_allocation_from_trigger(ctx.trigger)
            target_btc_ratio = _target_ratio_from_allocation(int(ctx.allocation or 0))
            price = float(ctx.btc_price_ref)
            # Safety path: allow explicit simulation price injection when no live trading.
            if price <= 0 and (DRY_RUN or not TRADING_ENABLED):
                injected = os.getenv("BTC_PRICE_USD", "").strip()
                if injected:
                    try:
                        price = float(injected)
                    except ValueError:
                        raise RuntimeError("invalid BTC_PRICE_USD; must be numeric")
            btc = float(snapshot.get("btc"))
            usdt = float(snapshot.get("usdt"))
            equity_before = usdt + btc * max(price, 0.0)
            current_ratio = (btc * price / equity_before) if (equity_before > 0 and price > 0) else 0.0

            signal, side, qty, trade_usd, delta_usd_raw = _rebalance_plan(price, btc, usdt, target_btc_ratio)

            if signal == "BUY" and qty > 0:
                cost = qty * price
                usdt_after = max(0.0, usdt - cost)
                btc_after = btc + qty
            elif signal == "SELL" and qty > 0:
                proceeds = qty * price
                usdt_after = usdt + proceeds
                btc_after = max(0.0, btc - qty)
            else:
                usdt_after = usdt
                btc_after = btc

            equity_after = usdt_after + btc_after * price
            d = Decision(
                signal=signal,
                side=side,
                qty=qty,
                price=price,
                btc=btc_after,
                usdt=usdt_after,
                equity_usd=equity_after,
                target_btc_ratio=target_btc_ratio,
                current_btc_ratio=current_ratio,
                trade_usd=trade_usd,
                delta_usd_raw=delta_usd_raw,
                reason_code="",
            )
            order_id = ""
            status = "simulated" if DRY_RUN else "trading_disabled"

        append_trade_row(
            {
                "ts_utc": now_utc(),
                "action": d.signal,
                "symbol": SYMBOL,
                "side": d.side,
                "qty": d.qty,
                "price": round(d.price, 2),
                "order_id": order_id,
                "status": status,
                "equity_usd": round(d.equity_usd, 2),
                "btc": d.btc,
                "usdt": round(d.usdt, 2),
                "signal": d.signal,
                "target_btc_ratio": d.target_btc_ratio,
                "current_btc_ratio": round(d.current_btc_ratio, 4),
                "trade_usd": round(d.trade_usd, 2),
                "delta_usd_raw": round(d.delta_usd_raw, 2),
                "forced_allocation": forced_allocation,
                "forced": forced,
                "client_order_id": d.client_order_id,
                "post_btc_units": d.post_btc_units if d.post_btc_units is not None else "",
                "post_usdt_units": d.post_usdt_units if d.post_usdt_units is not None else "",
                "post_equity_usd": round(d.post_equity_usd, 2) if d.post_equity_usd is not None else "",
                "reason_code": d.reason_code,
            }
        )

        state["failure_count"] = 0 if should_connect_exchange else int(state.get("failure_count", 0))
        state["last_signal"] = d.signal
        state["last_order_id"] = order_id
        if should_connect_exchange:
            # Keep runtime state tied to real exchange values only.
            state["last_equity"] = round(d.equity_usd, 2)
            state["balances"] = {"btc": d.btc, "usdt": round(d.usdt, 2)}
        else:
            # DRY_RUN view must reflect synchronized read-only snapshot.
            state["last_equity"] = round(d.equity_usd, 2)
            state["balances"] = {"btc": float(snapshot.get("btc")), "usdt": round(float(snapshot.get("usdt")), 2)}
        state["updated_at_utc"] = now_utc()
        save_state(state)

        mode = "DRY_RUN" if DRY_RUN else "LIVE"
        write_state = 1 if should_connect_exchange else 0
        msg = (
            f"[MarketEdge Exec] mode={mode} "
            f"run_id={run_id} lock_status={lock_state} "
            f"force_sync={1 if force_sync else 0} sync_threshold={sync_threshold:.4f} "
            f"snapshot_status={state.get('snapshot_status') or 'UNKNOWN'} "
            f"balance_source={state.get('balance_source') or 'UNKNOWN'} "
            f"balance_ts_utc={state.get('balances_ts_utc') or 'UNKNOWN'} "
            f"signal={d.signal} status={status} "
            f"price={d.price:.2f} equity={d.equity_usd:.2f} "
            f"current={d.current_btc_ratio:.1%} target={d.target_btc_ratio:.0%} "
            f"delta_usd={d.delta_usd_raw:.2f} trade_usd={d.trade_usd:.2f} qty={d.qty} "
            f"forced_alloc={forced_allocation or 'NONE'} "
            f"client_order_id={d.client_order_id} "
            f"post_btc={d.post_btc_units if d.post_btc_units is not None else 'NONE'} "
            f"post_usdt={d.post_usdt_units if d.post_usdt_units is not None else 'NONE'} "
            f"post_equity={round(d.post_equity_usd,2) if d.post_equity_usd is not None else 'NONE'} "
            f"reason_code={d.reason_code or 'NONE'} "
            f"TRADING_ENABLED={1 if TRADING_ENABLED else 0} write_state={write_state}"
        )
        print(msg)
        send_telegram(msg)

    except Exception as e:
        # Treat daily uniqueness block as normal (no FAIL). No side effects.
        if str(e) == "ALREADY_RAN_TODAY":
            mode = "DRY_RUN" if DRY_RUN else "LIVE"
            print(f"[MarketEdge Exec] mode={mode} run_id={run_id} lock_status={lock_state} reason_code=ALREADY_RAN_TODAY")
            sys.exit(0)

        # Only live-exchange failures count toward circuit-breaker halt.
        if should_connect_exchange:
            state["failure_count"] = int(state.get("failure_count", 0)) + 1
            if state["failure_count"] >= FAIL_STOP_COUNT:
                state["halted"] = True
        state["updated_at_utc"] = now_utc()
        save_state(state)

        reason_code = str(e) if str(e) in (
            "POST_SNAPSHOT_FETCH_FAILED",
            "POST_SNAPSHOT_PARSE_FAILED",
            "EXEC_FORCE_RERUN_LIVE_FORBIDDEN",
            "FORCE_SYNC_LOCK_EXISTS",
            "OPEN_ORDERS_EXIST",
            "OPEN_ORDERS_CHECK_FAILED",
            "UNKNOWN_PRICE_STALE",
            "INVALID_FORCE_SYNC_TARGET",
            "INVALID_STATE",
        ) else type(e).__name__
        if reason_code in ("FORCE_SYNC_LOCK_EXISTS", "OPEN_ORDERS_EXIST", "OPEN_ORDERS_CHECK_FAILED", "UNKNOWN_PRICE_STALE", "NetworkError", "INVALID_STATE"):
            append_force_sync_stop_audit(
                run_id=run_id,
                mode=("DRY_RUN" if DRY_RUN else "LIVE"),
                reason_code=reason_code,
                open_orders_count=open_orders_count,
            )
        err = (
            f"[MarketEdge Exec ERROR] mode={'DRY_RUN' if DRY_RUN else 'LIVE'} "
            f"run_id={run_id} lock_status={lock_state} reason_code={reason_code} "
            f"snapshot_status={state.get('snapshot_status') or 'UNKNOWN'} "
            f"balance_source={state.get('balance_source') or 'UNKNOWN'} "
            f"open_orders_count={open_orders_count if open_orders_count is not None else 'NONE'} "
            f"failure_count={state['failure_count']} halted={state.get('halted', False)}"
        )
        print(err)
        send_telegram(err)
        sys.exit(2)


if __name__ == "__main__":
    main()
