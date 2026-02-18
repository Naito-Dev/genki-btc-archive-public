#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import hashlib
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Callable
from urllib import parse, request

try:
    import pandas as pd  # type: ignore
except Exception:
    pd = None  # type: ignore

import sys

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
    status: str
    pnl_btc: Optional[float] = None


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
        try:
            v = fn(symbol)
            if v > 0:
                prices.append((name, float(v)))
        except Exception as e:
            errors.append(f"{name}:{type(e).__name__}")

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
    today = _now_utc().date()
    now_iso = _now_utc().replace(microsecond=0).isoformat().replace("+00:00", "Z")

    allocation = 0
    trigger = "MINIMAL_MODE: regime unavailable"
    price: Optional[float] = None
    data_source = "unavailable"
    price_source = "unavailable"
    status = "API_ERROR"

    symbol = os.getenv("BITGET_SYMBOL", "BTC/USDT")
    try:
        price, price_source = _fetch_live_consensus_price(symbol)
        status = "NO_TRADE"
    except Exception as e:
        print(f"[Genki BTC Archive] live price consensus failed: {type(e).__name__}: {e}")
        price = None
        price_source = "unavailable"
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
        trigger = _regime_reason(latest)
        data_source = "csv"

        # Do not substitute stale/local prices for current market price.
        # btc_price remains null unless live API succeeds.
    except Exception:
        pass

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
        status=status,
        pnl_btc=None,
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
    LOG_JSON.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


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
        "timestamp_utc": state.timestamp_utc,
        "data_source": state.data_source,
        "price_source": state.price_source,
        "btc_price": _round_or_none(state.btc_price_ref, 2),
        "regime": "unknown",
        "allocation": state.allocation,
        "logic_version": os.getenv("LOGIC_VERSION", "v1.0"),
        "position": "unknown",
        "pnl_btc": _round_or_none(state.pnl_btc, 8),
        "reason_summary": state.trigger,
        "confidence_score": None,
        "status": state.status,
        "allocation_changed": bool(changed),
        "day": state.day,
        "notes": state.notes,
        "updated_at_utc": state.updated_at_utc,
    }

    if not is_new_day:
        cur_idx = next((i for i, e in enumerate(entries) if e.get("date") == state.date_utc), None)
        prev_hash = entries[cur_idx].get("hash") if cur_idx is not None else None
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
    log = load_log()
    start_date = str(log.get("start_date_utc") or "2026-02-17")
    state = build_daily_state(start_date)
    state.notes = "Daily DRY_RUN update." if dry_run else "Daily live update (Actions)."
    log = upsert_today(log, state)
    save_log(log)

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
