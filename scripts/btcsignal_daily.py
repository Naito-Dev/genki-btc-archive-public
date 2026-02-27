#!/usr/bin/env python3
"""
btcsignal_daily.py — Model D
==============================
v1 Core + Crash Breaker + Re-entry Probation

See docs/model_d_spec.md for full specification.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parent.parent
SRC_LOG = ROOT / "log.json"
OUT_LOG = ROOT / "btcsignal_log.json"

# ──── Model D Parameters (FROZEN) ──────────────────
K_MA100 = 0.985
DROP_THRESHOLD = -0.10
Q_ATR = 0.04
N_CONSEC = 7
COOLDOWN = 3
MIN_HOLD = 14


# ──── JSON I/O ──────────────────────────────────────
def load_json(path: Path, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, obj) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


# ──── Source Parsing ────────────────────────────────
def _entry_to_point(e: dict) -> tuple[str, float] | None:
    date = e.get("date")
    price = e.get("btc_price")
    if not isinstance(date, str) or len(date) < 10:
        return None
    try:
        close = float(price)
    except Exception:
        return None
    return (date[:10], close)


def source_points(src: dict) -> list[tuple[str, float]]:
    points: list[tuple[str, float]] = []

    for e in src.get("entries", []) if isinstance(src.get("entries"), list) else []:
        pt = _entry_to_point(e)
        if pt is not None:
            points.append(pt)

    latest = src.get("latest") if isinstance(src.get("latest"), dict) else None
    if latest:
        pt = _entry_to_point(latest)
        if pt is not None:
            points.append(pt)

    dedup: dict[str, float] = {}
    for d, c in points:
        dedup[d] = c

    return sorted(dedup.items(), key=lambda x: x[0])


# ──── Indicator Helpers ─────────────────────────────
def _calc_ema(closes: list[float], period: int) -> list[float]:
    """Compute EMA for the full closes array."""
    ema = [closes[0]]
    k = 2 / (period + 1)
    for i in range(1, len(closes)):
        ema.append(closes[i] * k + ema[-1] * (1 - k))
    return ema


def _calc_atr(closes: list[float], period: int = 20) -> list[float]:
    """ATR as mean of absolute daily changes over rolling window."""
    n = len(closes)
    atr = [0.0] * n
    for i in range(1, n):
        w = min(i, period)
        atr[i] = mean(abs(closes[j] - closes[j - 1]) for j in range(i - w + 1, i + 1))
    return atr


def _weekly_bull(dates: list[str], closes: list[float]) -> list[bool]:
    """
    For each day, determine if previous week was bullish:
    prev_week_close > prev_week_SMA10.
    Uses ISO week; only references completed previous week (no lookahead).
    """
    from datetime import date as dt_date
    from collections import OrderedDict

    n = len(closes)
    parsed_dates = [dt_date.fromisoformat(d) for d in dates]

    # Build weekly data
    weekly: OrderedDict = OrderedDict()
    for i, d in enumerate(parsed_dates):
        wk = d.isocalendar()[:2]
        weekly[wk] = (i, closes[i])  # last occurrence = week close

    wk_keys = list(weekly.keys())
    wk_closes = [weekly[k][1] for k in wk_keys]
    wk_sma10: dict = {}
    for wi, wk in enumerate(wk_keys):
        if wi >= 9:
            wk_sma10[wk] = mean(wk_closes[wi - 9 : wi + 1])
        else:
            wk_sma10[wk] = None

    # Map each day to weekly bull status (using PREVIOUS completed week)
    result = [True] * n
    for i, d in enumerate(parsed_dates):
        wk = d.isocalendar()[:2]
        try:
            wi = wk_keys.index(wk)
        except ValueError:
            continue
        if wi <= 0:
            continue
        prev_wk = wk_keys[wi - 1]
        sma_val = wk_sma10.get(prev_wk)
        if sma_val is None:
            continue
        prev_close = weekly[prev_wk][1]
        result[i] = prev_close > sma_val

    return result


# ──── v1 Core Signal ────────────────────────────────
def _v1_signal(closes: list[float]) -> str:
    """Original v1 SMA50 + 2-day confirmation. Stateless."""
    if len(closes) < 51:
        return "CASH"
    sma50_today = mean(closes[-50:])
    sma50_yday = mean(closes[-51:-1])
    close_today = closes[-1]
    close_yday = closes[-2]
    if close_today > sma50_today and close_yday > sma50_yday and sma50_today >= sma50_yday:
        return "HOLD"
    return "CASH"


# ──── Model D Full Computation ──────────────────────
def compute_all_states(
    dates: list[str], closes: list[float]
) -> list[dict]:
    """
    Compute Model D states for the entire history.
    Returns list of dicts with state + metadata for each day.
    """
    n = len(closes)
    if n == 0:
        return []

    # Pre-compute indicators
    ema20 = _calc_ema(closes, 20)
    atr20 = _calc_atr(closes, 20)
    ma100 = [None] * n
    for i in range(99, n):
        ma100[i] = mean(closes[i - 99 : i + 1])
    sma50 = [None] * n
    for i in range(49, n):
        sma50[i] = mean(closes[i - 49 : i + 1])
    ret3 = [0.0] * n
    for i in range(3, n):
        ret3[i] = closes[i] / closes[i - 3] - 1
    weekly_bull = _weekly_bull(dates, closes)

    # Phase 1: v1 signals + crash breaker + probation
    mode = "NORMAL"
    consec = 0
    raw_signals = []

    for i in range(n):
        c = closes[i]
        ema = ema20[i]
        ma = ma100[i]

        # Crash breaker check (continuous)
        breaker = False
        if i >= 100 and ma is not None:
            if c < ma * K_MA100:
                breaker = True
            if ret3[i] <= DROP_THRESHOLD:
                breaker = True
            atr_ratio = atr20[i] / c if c > 0 else 0
            if c < ema and atr_ratio > Q_ATR:
                breaker = True

        if breaker and mode == "NORMAL":
            mode = "PROBATION"
            consec = 0

        # Re-entry from probation
        # Freeze re-entry while breaker remains true.
        elif mode == "PROBATION":
            if breaker:
                consec = 0
            else:
                if c > ema:
                    consec += 1
                else:
                    consec = 0
                if weekly_bull[i] and consec >= N_CONSEC:
                    mode = "NORMAL"

        # Signal
        v1 = _v1_signal(closes[: i + 1])
        if mode == "PROBATION":
            signal = "CASH"
        else:
            signal = v1

        raw_signals.append(
            {
                "signal": signal,
                "v1": v1,
                "mode": mode,
                "consec": consec,
                "sma50": sma50[i],
                "ema20": round(ema, 2),
                "ma100": round(ma, 2) if ma is not None else None,
                "atr20_ratio": round(atr20[i] / c, 4) if c > 0 else 0,
                "breaker": breaker,
            }
        )

    # Phase 2: Apply guards (cooldown + min_hold)
    last_sw = -COOLDOWN - 1
    hold_start = -1
    final_states = []

    for i in range(n):
        sig = raw_signals[i]["signal"]
        raw = raw_signals[i]
        prev = final_states[-1]["state"] if final_states else "CASH"
        force_exit = (
            prev == "HOLD"
            and sig == "CASH"
            and (
                bool(raw.get("breaker"))
                or raw.get("mode") == "PROBATION"
                or bool(raw.get("force_exit"))
            )
        )

        if sig != prev:
            if force_exit:
                # Breaker/probation exits are never blocked by cooldown/min_hold.
                last_sw = i
                if sig == "CASH":
                    hold_start = -1
            else:
                if (i - last_sw) < COOLDOWN:
                    sig = prev  # cooldown reject
                elif sig == "CASH" and prev == "HOLD" and hold_start >= 0 and (i - hold_start) < MIN_HOLD:
                    sig = "HOLD"  # min_hold reject (normal exits only)
                else:
                    last_sw = i
                    if sig == "HOLD":
                        hold_start = i
                    elif sig == "CASH":
                        hold_start = -1
        elif sig == "HOLD" and hold_start < 0:
            hold_start = i

        reason = _build_reason(sig, raw, prev)

        final_states.append(
            {
                "date": dates[i],
                "state": sig,
                "close": round(closes[i], 2),
                "sma50": round(raw["sma50"], 2) if raw["sma50"] else None,
                "reason": reason,
                "model": "D",
                "meta": {
                    "mode": raw["mode"],
                    "consec_above_ema": raw["consec"],
                    "last_switch_idx": last_sw,
                    "hold_start_idx": hold_start,
                    "ema20": raw["ema20"],
                    "ma100": raw["ma100"],
                    "atr20_ratio": raw["atr20_ratio"],
                },
            }
        )

    return final_states


def _build_reason(final_sig: str, raw: dict, prev_sig: str) -> str:
    """Build human-readable reason string."""
    if raw["sma50"] is None:
        return "data_warmup"
    if raw["mode"] == "PROBATION":
        if raw["breaker"]:
            return "crash_breaker_fired"
        return "probation_cash"
    if final_sig == "HOLD" and raw["v1"] == "HOLD":
        return "v1_hold_normal"
    if final_sig == "CASH" and raw["v1"] == "CASH":
        return "v1_cash_normal"
    if final_sig != raw["signal"]:
        return f"guard_reject_{raw['signal'].lower()}_to_{final_sig.lower()}"
    return "v1_normal"


# ──── Main ──────────────────────────────────────────
def main() -> int:
    dry_run = "--dry-run" in sys.argv or os.environ.get("DRY_RUN", "0") == "1"

    try:
        # ── Source is the ONLY input. No state read-back from output. ──
        src = load_json(SRC_LOG, None)
        if not isinstance(src, dict):
            print("UNAVAILABLE: log.json missing or invalid")
            return 0

        points = source_points(src)
        if not points:
            print("UNAVAILABLE: no usable (date, btc_price) points in log.json")
            return 0

        # Staleness check: idempotent re-run
        out_existing = load_json(OUT_LOG, {"entries": []})
        if isinstance(out_existing, dict):
            existing_entries = out_existing.get("entries", [])
            if isinstance(existing_entries, list) and existing_entries:
                last_out = str(existing_entries[-1].get("date") or "")
                last_src = points[-1][0]
                if last_out >= last_src:
                    print("ALREADY_RAN_TODAY: btcsignal_log already has latest date")
                    return 0

        # ── Full recompute from source data only ──
        all_dates = [d for d, _ in points]
        all_closes = [c for _, c in points]
        all_states = compute_all_states(all_dates, all_closes)

        if not all_states:
            print("UNAVAILABLE: compute_all_states returned empty")
            return 0

        last = all_states[-1]

        if dry_run:
            print(
                f"DRY_RUN: {last['date']} state={last['state']} "
                f"close={last['close']} reason={last['reason']} "
                f"mode={last['meta']['mode']}"
            )
            print(f"\n  Total entries: {len(all_states)}")
            print("  Last 5 entries:")
            for e in all_states[-5:]:
                print(
                    f"    {e['date']}  {e['state']:4s}  "
                    f"close={e['close']:>10.2f}  {e['reason']}"
                )
            return 0

        # ── Normal run: always write ──
        save_json(OUT_LOG, {"entries": all_states})
        print(
            f"OK_UPDATE: computed {len(all_states)} entries, "
            f"latest={last['date']} state={last['state']} "
            f"close={last['close']} reason={last['reason']} "
            f"mode={last['meta']['mode']}"
        )
        return 0

    except Exception as exc:
        print(f"SAFE_STOP: unhandled error — {type(exc).__name__}: {exc}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
