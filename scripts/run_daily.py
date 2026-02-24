#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import hashlib
import statistics
import sys
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional, Callable
from urllib import parse, request

try:
    import pandas as pd  # type: ignore
except Exception:
    pd = None  # type: ignore

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.append(str(SCRIPT_DIR))

try:
    import verify_marketedge as vm  # type: ignore
except Exception:
    vm = None  # type: ignore


ROOT = Path(__file__).resolve().parent.parent
D1_PATH = ROOT / "data/Binance_BTCUSDT_D1.csv"
LOG_JSON = ROOT / "public/log.json"
LOGS_DIR = ROOT / "public/logs"
STATE_JSON = ROOT / "output/state_live.json"
DAILY_INPUT_ENV = ROOT / "daily_input.env"


@dataclass
class DailyState:
    date_utc: str
    timestamp_utc: str
    day: str
    allocation: int
    btc_price_ref: Optional[float]
    allocation_changed: bool
    trigger: str
    notes: str
    updated_at_utc: str
    data_source: str
    price_source: str
    price_ts: Optional[str]
    regime_source: str
    status: str
    position: str = "unknown"
    equity_usd: Optional[float] = None
    pnl_usd: Optional[float] = None
    pnl_btc: Optional[float] = None
    snapshot_status: str = "SYNC_PENDING"
    balance_source: Optional[str] = None
    balance_ts_utc: Optional[str] = None
    target_btc_ratio: float = 0.0
    actual_btc_ratio: Optional[float] = None
    actual_position: str = "unknown"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _allocation_from_weight(w: float) -> int:
    if w >= 0.999:
        return 100
    if w >= 0.699:
        return 70
    if w >= 0.299:
        return 30
    return 0


def _round_or_none(value: Optional[float], digits: int) -> Optional[float]:
    if value is None:
        return None
    return round(float(value), digits)


def _env_float(name: str) -> Optional[float]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except Exception:
        return None


def _load_env_file(path: Path) -> None:
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


def _is_recent_file(path: Path, max_age_hours: int = 30) -> bool:
    if not path.exists():
        return False
    try:
        age = _now_utc().timestamp() - path.stat().st_mtime
        return age <= (max_age_hours * 3600)
    except Exception:
        return False


def _is_recent_env_timestamp(name: str, max_age_hours: int = 30) -> bool:
    raw = os.getenv(name, "").strip()
    if not raw:
        return False
    try:
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age = (_now_utc() - ts.astimezone(timezone.utc)).total_seconds()
        return 0 <= age <= (max_age_hours * 3600)
    except Exception:
        return False


def _position_from_btc_ratio(btc_ratio: Optional[float]) -> Optional[str]:
    if btc_ratio is None:
        return None
    return "CASH" if btc_ratio < 0.01 else "LONG"


def _date_jst_from_utc_date(date_utc: str) -> str:
    dt = datetime.fromisoformat(date_utc).replace(tzinfo=timezone.utc)
    return (dt + timedelta(hours=9)).date().isoformat()


def _regime_from_allocation(allocation: int) -> str:
    if allocation <= 0:
        return "bear"
    if allocation == 30:
        return "neutral"
    return "bull"


def _sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _canonical_entry_for_hash(entry: dict) -> str:
    clean = {k: v for k, v in entry.items() if k not in ("hash", "prev_hash")}
    return json.dumps(clean, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _regime_reason(row: Any) -> str:
    def get(k: str, default=None):
        try:
            return row[k]
        except Exception:
            return getattr(row, k, default)

    ma5, ma10, ma30, ma50, ma100, ma200 = (
        get("ma5"),
        get("ma10"),
        get("ma30"),
        get("ma50"),
        get("ma100"),
        get("ma200"),
    )
    price = get("close")
    spread = get("ma_spread_ratio")

    if spread is None or price is None or ma5 is None or ma10 is None or ma30 is None or ma50 is None or ma100 is None or ma200 is None:
        tw = get("target_weight", None)
        return f"target_weight={tw}"

    if spread < vm.DEFAULT_CONGESTION:
        return "MA spread in congestion threshold"
    bullish = ma5 > ma10 > ma30 > ma50 > ma100 > ma200
    bearish = ma5 < ma10 < ma30 < ma50 < ma100 < ma200
    if bullish and price >= ma5:
        return "Bullish structure, price >= MA5"
    if bullish and price < ma5:
        return "Bullish structure, price < MA5"
    if bearish or price < ma100:
        return "Bearish structure or price < MA100"
    return "Defensive default state"


def _http_get_json(url: str, timeout: int = 10) -> dict:
    req = request.Request(url, headers={"User-Agent": "genki-btc-archive/1.0"})
    with request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _load_runtime_state() -> dict:
    if not STATE_JSON.exists():
        return {}
    try:
        return json.loads(STATE_JSON.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_runtime_state(state: dict) -> None:
    STATE_JSON.parent.mkdir(parents=True, exist_ok=True)
    STATE_JSON.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _to_compact_symbol(symbol: str) -> str:
    return symbol.replace("/", "").replace("-", "").replace("_", "").upper()


def _fetch_bitget_price(symbol: str = "BTC/USDT") -> float:
    compact = _to_compact_symbol(symbol)
    url = f"https://api.bitget.com/api/v2/spot/market/tickers?symbol={compact}"
    data = _http_get_json(url)
    items = data.get("data")
    item = items[0] if isinstance(items, list) and items else (items if isinstance(items, dict) else None)
    if not item:
        raise RuntimeError(f"bitget empty data for {compact}")
    last = item.get("lastPr") or item.get("last") or item.get("close")
    if last is None:
        raise RuntimeError(f"bitget missing last price for {compact}")
    return float(last)


def _fetch_binance_price(symbol: str = "BTC/USDT") -> float:
    compact = _to_compact_symbol(symbol)
    url = f"https://api.binance.com/api/v3/ticker/price?symbol={compact}"
    data = _http_get_json(url)
    price = data.get("price")
    if price is None:
        raise RuntimeError(f"binance missing price for {compact}")
    return float(price)


def _fetch_coinbase_price(symbol: str = "BTC/USDT") -> float:
    compact = _to_compact_symbol(symbol)
    if compact.endswith("USDT"):
        compact = compact[:-4] + "USD"
    pair = f"{compact[:-3]}-{compact[-3:]}"  # BTC-USD
    url = f"https://api.exchange.coinbase.com/products/{pair}/ticker"
    data = _http_get_json(url)
    price = data.get("price")
    if price is None:
        raise RuntimeError(f"coinbase missing price for {pair}")
    return float(price)


def _fetch_kraken_price(symbol: str = "BTC/USDT") -> float:
    compact = _to_compact_symbol(symbol)
    if compact.endswith("USDT"):
        compact = compact[:-4] + "USD"
    pair = f"{compact[:-3]}{compact[-3:]}"  # BTCUSD
    url = f"https://api.kraken.com/0/public/Ticker?pair={pair}"
    data = _http_get_json(url)
    result = data.get("result") or {}
    if not isinstance(result, dict) or not result:
        raise RuntimeError(f"kraken empty result for {pair}")
    first = next(iter(result.values()))
    c = first.get("c") if isinstance(first, dict) else None
    if not c or not isinstance(c, list) or not c[0]:
        raise RuntimeError(f"kraken missing close for {pair}")
    return float(c[0])


def _fetch_bitstamp_price(symbol: str = "BTC/USDT") -> float:
    compact = _to_compact_symbol(symbol)
    if compact.endswith("USDT"):
        compact = compact[:-4] + "USD"
    pair = f"{compact[:-3].lower()}{compact[-3:].lower()}"  # btcusd
    url = f"https://www.bitstamp.net/api/v2/ticker/{pair}/"
    data = _http_get_json(url)
    last = data.get("last")
    if last is None:
        raise RuntimeError(f"bitstamp missing last for {pair}")
    return float(last)


def _fetch_coingecko_price(symbol: str = "BTC/USDT") -> float:
    compact = _to_compact_symbol(symbol)
    if compact != "BTCUSDT":
        raise RuntimeError(f"coingecko unsupported symbol {compact}")
    url = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd"
    data = _http_get_json(url)
    btc = data.get("bitcoin") or {}
    price = btc.get("usd")
    if price is None:
        raise RuntimeError("coingecko missing bitcoin.usd")
    return float(price)


def _fetch_live_consensus_price(symbol: str = "BTC/USDT") -> tuple[float, str]:
    sources: list[tuple[str, Callable[[str], float]]] = [
        ("bitget_spot", _fetch_bitget_price),
        ("binance_spot", _fetch_binance_price),
        ("coinbase_spot", _fetch_coinbase_price),
        ("kraken_spot", _fetch_kraken_price),
        ("bitstamp_spot", _fetch_bitstamp_price),
        ("coingecko_index", _fetch_coingecko_price),
    ]

    prices: list[tuple[str, float]] = []
    errors: list[str] = []
    for name, fn in sources:
        ok = False
        last_err: Optional[Exception] = None
        for _ in range(2):  # retries=1
            try:
                v = fn(symbol)
                if v > 0:
                    prices.append((name, float(v)))
                    ok = True
                    break
            except Exception as e:
                last_err = e
        if not ok:
            err_name = type(last_err).__name__ if last_err is not None else "UnknownError"
            errors.append(f"{name}:{err_name}")

    if len(prices) < 3:
        raise RuntimeError(f"consensus insufficient sources (<3). errors={','.join(errors)}")

    raw_vals = [p for _, p in prices]
    med = statistics.median(raw_vals)
    tol = 0.008
    inliers = [(n, p) for n, p in prices if abs(p - med) / med <= tol]
    if len(inliers) < 3:
        raise RuntimeError("consensus rejected: fewer than 3 inliers within ±0.8%")

    final_med = statistics.median([p for _, p in inliers])
    src = "consensus:" + ",".join(n for n, _ in inliers)
    return final_med, src


def _send_telegram(msg: str) -> None:
    token = os.getenv("TELEGRAM_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip() or os.getenv("TELEGRAM_CHATID", "").strip()
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = parse.urlencode({"chat_id": chat_id, "text": msg}).encode("utf-8")
    req = request.Request(url, data=data, method="POST")
    with request.urlopen(req, timeout=15):
        pass


def build_daily_state(start_date_utc: str) -> DailyState:
    # Load daily operator inputs (BTC/USDT snapshot) if present.
    _load_env_file(DAILY_INPUT_ENV)

    today = _now_utc().date()
    now_iso = _now_utc().replace(microsecond=0).isoformat().replace("+00:00", "Z")

    allocation = 0
    trigger = "MINIMAL_MODE: regime unavailable"
    position = "CASH"
    price: Optional[float] = None
    data_source = "unavailable"
    price_source = "unavailable"
    price_ts: Optional[str] = None
    regime_source = "unavailable"
    status = "API_ERROR"
    runtime_state = _load_runtime_state()

    symbol = os.getenv("BITGET_SYMBOL", "BTC/USDT")
    try:
        price, price_source = _fetch_live_consensus_price(symbol)
        price_ts = now_iso
        runtime_state["last_good_price"] = float(price)
        runtime_state["last_good_price_ts"] = now_iso
        _save_runtime_state(runtime_state)
        regime_source = "live"
        status = "NO_TRADE"
    except Exception as e:
        print(f"[Genki BTC Archive] live price consensus failed: {type(e).__name__}: {e}")
        cached_price = runtime_state.get("last_good_price")
        cached_ts = runtime_state.get("last_good_price_ts")
        if cached_price is None:
            try:
                log = load_log()
                entries = list(log.get("entries", []))
                entries.sort(key=lambda x: x.get("date", ""))
                for e in reversed(entries):
                    lp = e.get("btc_price")
                    if lp is not None:
                        cached_price = lp
                        cached_ts = e.get("price_ts") or e.get("updated_at_utc")
                        break
            except Exception:
                pass
        if cached_price is not None:
            try:
                price = float(cached_price)
                price_source = "cache_stale"
                price_ts = str(cached_ts) if cached_ts else now_iso
                regime_source = "cache_stale"
                status = "DATA_STALE"
            except Exception:
                price = None
                price_source = "unavailable"
                price_ts = None
                regime_source = "unavailable"
                status = "API_ERROR"
        else:
            price = None
            price_source = "unavailable"
            price_ts = None
            regime_source = "unavailable"
            status = "API_ERROR"

    try:
        if vm is None:
            raise RuntimeError("verify_marketedge unavailable")
        d1 = vm.load_ohlcv(D1_PATH, "D1")
        regime = vm.build_regime_table(d1, vm.DEFAULT_CONGESTION)
        if getattr(regime, "empty", False):
            raise RuntimeError("Regime table is empty")
        latest = regime.iloc[-1]
        allocation = _allocation_from_weight(float(latest["target_weight"]))
        position = "CASH" if allocation == 0 else "LONG"
        trigger = _regime_reason(latest)
        data_source = "csv"

        # Do not substitute stale/local prices for current market price.
        # btc_price remains null unless live API succeeds.
    except Exception:
        pass

    # TEST ONLY: force allocation bucket (DRY_RUN only)
    forced = os.getenv("FORCE_ALLOCATION", "").strip()
    is_dry_run = os.getenv("DRY_RUN", "1").strip() != "0"
    if forced and is_dry_run:
        try:
            f = int(forced)
            if f not in (0, 30, 70, 100):
                raise ValueError
        except ValueError:
            raise RuntimeError("FORCE_ALLOCATION must be one of 0,30,70,100")
        allocation = f
        position = "CASH" if allocation == 0 else "LONG"
        trigger = f"FORCED_ALLOCATION:{allocation}"
        regime_source = "forced"

    # Optional reconciliation using live balance snapshot inputs.
    # Snapshot must come from a fresh daily_input.env; stale values are ignored.
    # Snapshot is accepted only when both file mtime and embedded timestamp are fresh.
    balance_status = os.getenv("SNAPSHOT_STATUS", "").strip().upper()
    balance_source = os.getenv("BALANCE_SOURCE", "").strip() or None
    balance_ts = os.getenv("BALANCE_TS_UTC", "").strip() or None
    input_fresh = _is_recent_file(DAILY_INPUT_ENV) and _is_recent_env_timestamp("BALANCE_TS_UTC")
    snapshot_ok = (
        balance_status == "SYNCED"
        and balance_source == "BITGET_READONLY"
        and input_fresh
    )
    btc_units = _env_float("BTC_UNITS") if input_fresh else None
    usdt_units = _env_float("USDT_UNITS") if input_fresh else None
    if price is not None and btc_units is not None and usdt_units is not None:
        total = btc_units * price + usdt_units
        if total > 0:
            ratio_position = _position_from_btc_ratio((btc_units * price) / total)
            if ratio_position is not None:
                position = ratio_position

    target_btc_ratio = float(allocation) / 100.0
    actual_btc_ratio = None
    actual_position = "unknown"
    derived_equity = None
    if snapshot_ok and price is not None and btc_units is not None and usdt_units is not None:
        derived_equity = btc_units * price + usdt_units
        if derived_equity > 0:
            actual_btc_ratio = (btc_units * price) / derived_equity
            actual_position = "LONG" if actual_btc_ratio >= 0.01 else "CASH"

    return DailyState(
        date_utc=today.isoformat(),
        timestamp_utc=now_iso,
        day="Day 1/30",
        allocation=allocation,
        btc_price_ref=price,
        allocation_changed=False,  # set after comparing with previous entry
        trigger=trigger,
        notes="Daily DRY_RUN update.",
        updated_at_utc=now_iso,
        data_source=data_source,
        price_source=price_source,
        price_ts=price_ts,
        regime_source=regime_source,
        status=status,
        position=position,
        # Equity must come from balance snapshot formula only.
        # If unavailable, UI should show SYNC_PENDING (not an estimate).
        equity_usd=_round_or_none(derived_equity, 2) if derived_equity is not None else None,
        pnl_usd=_env_float("PNL_USD"),
        pnl_btc=None,
        snapshot_status="SYNCED" if derived_equity is not None else "SYNC_PENDING",
        balance_source=balance_source,
        balance_ts_utc=balance_ts,
        target_btc_ratio=target_btc_ratio,
        actual_btc_ratio=actual_btc_ratio,
        actual_position=actual_position,
    )


def load_log() -> dict:
    if not LOG_JSON.exists():
        return {
            "start_date_utc": "2026-02-17",
            "last_updated_utc": None,
            "entries": [],
        }
    return json.loads(LOG_JSON.read_text(encoding="utf-8"))


def save_log(data: dict) -> None:
    LOG_JSON.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = LOG_JSON.with_suffix(LOG_JSON.suffix + ".tmp")
    tmp_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp_path, LOG_JSON)


def save_daily_file(entry: dict, date_utc: str) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    out = LOGS_DIR / f"{date_utc}.json"
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(entry, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, out)


def _validate_chain(entries: list[dict]) -> tuple[bool, str]:
    ordered = sorted(entries, key=lambda x: x.get("date", ""))
    prev = None
    for i, e in enumerate(ordered):
        prev_hash = e.get("prev_hash")
        if i == 0:
            if prev_hash not in (None, ""):
                return False, "genesis prev_hash must be null"
        else:
            if prev_hash != prev:
                return False, "prev_hash mismatch"

        clean = {k: v for k, v in e.items() if k not in ("hash", "prev_hash")}
        body = json.dumps(clean, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        expected = _sha256_hex(body + "|" + (prev_hash or ""))
        if e.get("hash") != expected:
            return False, "hash mismatch"
        prev = e.get("hash")
    return True, ""


def upsert_today(log: dict, state: DailyState) -> dict:
    entries = list(log.get("entries", []))
    entries.sort(key=lambda x: x.get("date", ""))
    existing_dates = {e.get("date") for e in entries if isinstance(e, dict)}
    is_new_day = state.date_utc not in existing_dates
    day_num = len(entries) + (1 if is_new_day else 0)
    day_num = max(1, day_num)
    state.day = f"Day {day_num}/30"

    prev_alloc = entries[-1].get("allocation") if entries else None
    changed = prev_alloc is not None and int(prev_alloc) != int(state.allocation)

    payload = {
        "date": state.date_utc,
        "date_jst": _date_jst_from_utc_date(state.date_utc),
        "timestamp_utc": state.timestamp_utc,
        "data_source": state.data_source,
        "price_source": state.price_source,
        "price_ts": state.price_ts,
        "btc_price": _round_or_none(state.btc_price_ref, 2),
        "regime": _regime_from_allocation(state.allocation),
        "regime_source": state.regime_source,
        "allocation": state.allocation,
        "logic_version": os.getenv("LOGIC_VERSION", "v1.0"),
        "position": state.position,
        "target_btc_ratio": _round_or_none(state.target_btc_ratio, 4),
        "actual_btc_ratio": _round_or_none(state.actual_btc_ratio, 4),
        "actual_position": state.actual_position,
        "equity_usd": _round_or_none(state.equity_usd, 2),
        "pnl_usd": _round_or_none(state.pnl_usd, 2),
        "pnl_btc": _round_or_none(state.pnl_btc, 8),
        "reason_summary": state.trigger,
        "trigger": state.trigger,
        "confidence_score": None,
        "status": state.status,
        "allocation_changed": bool(changed),
        "day": state.day,
        "notes": state.notes,
        "snapshot_status": state.snapshot_status,
        "balance_source": state.balance_source,
        "balance_ts_utc": state.balance_ts_utc,
        "portfolio_snapshot": (
            {
                "equity_usd": _round_or_none(state.equity_usd, 2),
                "position": state.position,
                "target_btc_ratio": _round_or_none(state.target_btc_ratio, 4),
                "actual_btc_ratio": _round_or_none(state.actual_btc_ratio, 4),
                "actual_position": state.actual_position,
                "status": state.snapshot_status,
                "source": state.balance_source,
                "ts_utc": state.balance_ts_utc,
            }
            if state.equity_usd is not None
            else {"status": "SYNC_PENDING", "source": state.balance_source, "ts_utc": state.balance_ts_utc}
        ),
        "pnl": _round_or_none(state.pnl_usd, 2),
        "updated_at_utc": state.updated_at_utc,
        "chain_integrity": "VALID",
        "chain_reason": "",
    }

    if not is_new_day:
        cur_idx = next((i for i, e in enumerate(entries) if e.get("date") == state.date_utc), None)
        if cur_idx is None or cur_idx == 0:
            prev_hash = None
        else:
            prev_hash = entries[cur_idx - 1].get("hash")
    else:
        prev_hash = entries[-1].get("hash") if entries else None
    payload["prev_hash"] = prev_hash
    payload["hash"] = _sha256_hex(_canonical_entry_for_hash(payload) + "|" + (prev_hash or ""))

    found = False
    for i, e in enumerate(entries):
        if e.get("date") == state.date_utc:
            entries[i] = payload
            found = True
            break
    if not found:
        entries.append(payload)
    entries.sort(key=lambda x: x.get("date", ""))

    log["entries"] = entries
    log["last_updated_utc"] = state.updated_at_utc
    log["latest"] = payload
    return log


def main() -> None:
    dry_run = os.getenv("DRY_RUN", "1").strip() != "0"
    on_actions = os.getenv("GITHUB_ACTIONS", "").strip().lower() == "true"
    force_today = os.getenv("FORCE_TODAY", "0").strip() == "1"
    log = load_log()
    start_date = str(log.get("start_date_utc") or "2026-02-17")
    today_utc = _now_utc().date().isoformat()
    latest = log.get("latest") if isinstance(log.get("latest"), dict) else {}
    if latest.get("date") == today_utc and not force_today:
        print("No changes: today's UTC entry already exists.")
        return
    state = build_daily_state(start_date)
    if dry_run:
        state.notes = "Daily DRY_RUN update."
    else:
        state.notes = "Daily live update (Actions)." if on_actions else "Daily live update (manual)."
    log = upsert_today(log, state)

    ok, reason = _validate_chain(log.get("entries", []))
    if not ok:
        print(f"[Genki BTC Archive] chain validation failed: {reason}", file=sys.stderr)
        raise SystemExit(2)

    save_log(log)
    if isinstance(log.get("latest"), dict):
        save_daily_file(log["latest"], state.date_utc)

    msg = (
        f"[Genki BTC Archive] {state.date_utc} | {state.day} | allocation={state.allocation}% | "
        f"price={(f'{state.btc_price_ref:.2f}' if state.btc_price_ref is not None else 'null')} | "
        f"trigger={state.trigger} | DRY_RUN={1 if dry_run else 0}"
    )
    if dry_run:
        try:
            _send_telegram(msg)
        except Exception:
            pass

    print("Updated: public/log.json")
    print(msg)


if __name__ == "__main__":
    main()
