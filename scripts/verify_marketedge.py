#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Tuple

import numpy as np
import pandas as pd


MA_PERIODS = [5, 10, 30, 50, 100, 200]
DEFAULT_CONGESTION = 0.015
DEFAULT_MIDPOINT_FRAC = 0.5
COST_PER_SIDE = 0.0012  # 0.10% fee + 0.02% slippage


@dataclass
class RunResult:
    equity: pd.Series
    weights: pd.Series
    trades: int
    total_cost: float
    avg_weight: float
    params: Dict[str, float]
    trade_log: pd.DataFrame


def load_ohlcv(path: Path, label: str) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Missing {label} CSV: {path}")

    df = pd.read_csv(path)
    expected = ["timestamp", "open", "high", "low", "close", "volume"]
    missing = [c for c in expected if c not in df.columns]
    if missing:
        raise ValueError(f"{label}: missing columns {missing}. Expected {expected}")

    ts = df["timestamp"]
    if np.issubdtype(ts.dtype, np.number):
        # Binance exports can contain mixed epoch units across months (ms/us).
        ts_num = pd.to_numeric(ts, errors="coerce")
        abs_ts = ts_num.abs()
        dt = pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns, UTC]")

        sec_mask = abs_ts <= 1e11
        ms_mask = (abs_ts > 1e11) & (abs_ts <= 1e14)
        us_mask = abs_ts > 1e14

        if sec_mask.any():
            dt.loc[sec_mask] = pd.to_datetime(ts_num.loc[sec_mask], unit="s", utc=True)
        if ms_mask.any():
            dt.loc[ms_mask] = pd.to_datetime(ts_num.loc[ms_mask], unit="ms", utc=True)
        if us_mask.any():
            dt.loc[us_mask] = pd.to_datetime(ts_num.loc[us_mask], unit="us", utc=True)
    else:
        dt = pd.to_datetime(ts, utc=True)

    out = df.copy()
    out["timestamp"] = dt
    for c in ["open", "high", "low", "close", "volume"]:
        out[c] = pd.to_numeric(out[c], errors="coerce")

    out = out.dropna(subset=["timestamp", "open", "high", "low", "close", "volume"])
    out = out.sort_values("timestamp").drop_duplicates("timestamp").set_index("timestamp")
    return out


def add_daily_indicators(d1: pd.DataFrame) -> pd.DataFrame:
    df = d1.copy()
    for p in MA_PERIODS:
        df[f"ma{p}"] = df["close"].rolling(p).mean()

    ma_cols = [f"ma{p}" for p in MA_PERIODS]
    df["ma_spread_ratio"] = (df[ma_cols].max(axis=1) - df[ma_cols].min(axis=1)) / df["close"]
    return df


def classify_target_weight(row: pd.Series, congestion_threshold: float) -> float:
    ma5, ma10, ma30, ma50, ma100, ma200 = (
        row["ma5"],
        row["ma10"],
        row["ma30"],
        row["ma50"],
        row["ma100"],
        row["ma200"],
    )
    price = row["close"]

    # Level 3: congestion takes precedence over trend distinction.
    if row["ma_spread_ratio"] < congestion_threshold:
        return 0.30

    bullish_structure = ma5 > ma10 > ma30 > ma50 > ma100 > ma200
    bearish_structure = ma5 < ma10 < ma30 < ma50 < ma100 < ma200

    # Level 5: bullish PPP.
    if bullish_structure and price >= ma5:
        return 1.00

    # Level 4: bullish structure but price below MA5.
    if bullish_structure and price < ma5:
        return 0.70

    # Level 1/2: bearish structure OR price below MA100.
    if bearish_structure or price < ma100:
        return 0.00

    # Any unclassified mixed state defaults to defensive allocation.
    return 0.00


def build_regime_table(d1: pd.DataFrame, congestion_threshold: float) -> pd.DataFrame:
    df = add_daily_indicators(d1)
    min_history = max(MA_PERIODS)
    df = df.iloc[min_history:].copy()
    df["target_weight"] = df.apply(classify_target_weight, axis=1, congestion_threshold=congestion_threshold)
    return df


def midpoint_trigger_ok(open_: pd.Series, close: pd.Series, ma5: pd.Series, frac: float) -> pd.Series:
    midpoint = open_ + frac * (close - open_)
    return midpoint > ma5


def simulate(
    d1: pd.DataFrame,
    m15: pd.DataFrame,
    congestion_threshold: float = DEFAULT_CONGESTION,
    midpoint_frac: float = DEFAULT_MIDPOINT_FRAC,
) -> RunResult:
    regime = build_regime_table(d1, congestion_threshold)

    bars = m15[["open", "high", "low", "close", "volume"]].copy()
    bars["day"] = bars.index.floor("D")
    regime_cols = ["target_weight", "ma5"]
    bars = bars.join(regime[regime_cols], on="day", how="left")
    bars = bars.dropna(subset=["target_weight", "ma5"])

    bars["midpoint_ok"] = midpoint_trigger_ok(bars["open"], bars["close"], bars["ma5"], midpoint_frac)

    day_target = regime["target_weight"].to_dict()
    day_target_prev: Dict[pd.Timestamp, float] = {}
    prev = 0.0
    for d, w in day_target.items():
        day_target_prev[d] = prev
        prev = w

    bars["prev_target"] = bars["day"].map(day_target_prev)
    bars["is_increase_day"] = bars["target_weight"] > bars["prev_target"]

    eligible = bars[bars["midpoint_ok"]].groupby("day").head(1).index
    bars["first_trigger_idx"] = False
    bars.loc[eligible, "first_trigger_idx"] = True

    first_bar_idx = bars.groupby("day").head(1).index
    bars["first_bar_idx"] = False
    bars.loc[first_bar_idx, "first_bar_idx"] = True

    cash = 1.0
    qty = 0.0
    trades = 0
    total_cost = 0.0

    eq_vals = []
    w_vals = []
    idx_vals = []
    trade_rows = []

    for idx, row in bars.iterrows():
        price = float(row["close"])
        equity = cash + qty * price
        current_btc_value = qty * price
        current_weight = 0.0 if equity <= 0 else current_btc_value / equity

        execute = False
        if bool(row["is_increase_day"]):
            if bool(row["first_trigger_idx"]):
                execute = True
        else:
            if bool(row["first_bar_idx"]):
                execute = True

        if execute:
            target = float(row["target_weight"])
            target_btc_value = target * equity
            delta = target_btc_value - current_btc_value

            if abs(delta) > 1e-12:
                equity_before = equity
                weight_before = current_weight
                qty_before = qty
                cash_before = cash
                reason = "increase_day_first_trigger" if bool(row["is_increase_day"]) else "non_increase_day_first_bar"

                trades += 1
                if delta > 0:
                    # Buy BTC.
                    side = "BUY"
                    notional = delta
                    cost = notional * COST_PER_SIDE
                    qty += notional / price
                    cash -= (notional + cost)
                else:
                    # Sell BTC.
                    side = "SELL"
                    notional = -delta
                    cost = notional * COST_PER_SIDE
                    qty -= notional / price
                    cash += (notional - cost)
                total_cost += cost

                equity = cash + qty * price
                current_btc_value = qty * price
                current_weight = 0.0 if equity <= 0 else current_btc_value / equity

                trade_rows.append(
                    {
                        "ts_utc": idx,
                        "day_utc": row["day"],
                        "side": side,
                        "reason": reason,
                        "price": price,
                        "target_weight": target,
                        "weight_before": weight_before,
                        "weight_after": current_weight,
                        "notional": notional,
                        "cost": cost,
                        "equity_before": equity_before,
                        "equity_after": equity,
                        "qty_before": qty_before,
                        "qty_after": qty,
                        "cash_before": cash_before,
                        "cash_after": cash,
                    }
                )

        idx_vals.append(idx)
        eq_vals.append(equity)
        w_vals.append(current_weight)

    equity_series = pd.Series(eq_vals, index=pd.DatetimeIndex(idx_vals), name="equity")
    weights_series = pd.Series(w_vals, index=pd.DatetimeIndex(idx_vals), name="weight")

    trade_log = pd.DataFrame(trade_rows)
    return RunResult(
        equity=equity_series,
        weights=weights_series,
        trades=trades,
        total_cost=total_cost,
        avg_weight=float(weights_series.mean()),
        params={"congestion_threshold": congestion_threshold, "midpoint_frac": midpoint_frac},
        trade_log=trade_log,
    )


def perf_metrics(equity: pd.Series) -> Dict[str, float]:
    if equity.empty:
        return {"final_equity": np.nan, "cagr": np.nan, "max_drawdown": np.nan}

    start = equity.index[0]
    end = equity.index[-1]
    years = (end - start).total_seconds() / (365.25 * 24 * 3600)
    final_equity = float(equity.iloc[-1])
    cagr = (final_equity ** (1 / years) - 1) if years > 0 and final_equity > 0 else np.nan

    running_max = equity.cummax()
    dd = equity / running_max - 1.0
    mdd = float(dd.min()) if not dd.empty else np.nan

    return {
        "final_equity": final_equity,
        "cagr": float(cagr),
        "max_drawdown": mdd,
    }


def period_summary(weights: pd.Series, equity: pd.Series, start: str, end: str) -> Dict[str, float]:
    ws = weights.loc[start:end]
    es = equity.loc[start:end]
    if ws.empty or es.empty:
        return {"min_weight": np.nan, "max_weight": np.nan, "period_return": np.nan, "max_drawdown": np.nan}

    running_max = es.cummax()
    dd = es / running_max - 1.0
    return {
        "min_weight": float(ws.min()),
        "max_weight": float(ws.max()),
        "period_return": float(es.iloc[-1] / es.iloc[0] - 1),
        "max_drawdown": float(dd.min()),
    }


def sensitivity_grid(d1: pd.DataFrame, m15: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for c in [0.012, 0.015, 0.018]:
        for f in [0.40, 0.50, 0.60]:
            result = simulate(d1, m15, congestion_threshold=c, midpoint_frac=f)
            met = perf_metrics(result.equity)
            rows.append(
                {
                    "congestion_threshold": c,
                    "midpoint_frac": f,
                    "final_equity": met["final_equity"],
                    "cagr": met["cagr"],
                    "max_drawdown": met["max_drawdown"],
                    "trades": result.trades,
                    "avg_weight": result.avg_weight,
                }
            )
    return pd.DataFrame(rows).sort_values(["congestion_threshold", "midpoint_frac"])


def render_report(
    result: RunResult,
    base_metrics: Dict[str, float],
    crash2018: Dict[str, float],
    crash2022: Dict[str, float],
    sensitivity: pd.DataFrame,
    d1_path: Path,
    m15_path: Path,
) -> str:
    lines = []
    lines.append("# MarketEdge Verification Report")
    lines.append("")
    lines.append("## Inputs")
    lines.append(f"- D1 CSV: `{d1_path}`")
    lines.append(f"- 15m CSV: `{m15_path}`")
    lines.append("- Baseline MAs: [5, 10, 30, 50, 100, 200]")
    lines.append(f"- Congestion threshold: {result.params['congestion_threshold']:.4f}")
    lines.append(f"- Lower-body midpoint fraction: {result.params['midpoint_frac']:.2f}")
    lines.append("- Costs per side: 0.12% (0.10% fee + 0.02% slippage)")
    lines.append("- Round-trip friction: 0.24%")
    lines.append("")

    lines.append("## Core Result")
    lines.append(f"- Final equity multiple: {base_metrics['final_equity']:.4f}x")
    lines.append(f"- CAGR: {base_metrics['cagr']:.2%}")
    lines.append(f"- Max drawdown: {base_metrics['max_drawdown']:.2%}")
    lines.append(f"- Trade count: {result.trades}")
    lines.append(f"- Total transaction cost paid (equity units): {result.total_cost:.6f}")
    lines.append(f"- Average BTC allocation: {result.avg_weight:.2%}")
    lines.append("")

    lines.append("## Red-Team Crash Check")
    lines.append("### 2018")
    lines.append(f"- Min allocation: {crash2018['min_weight']:.2%}")
    lines.append(f"- Max allocation: {crash2018['max_weight']:.2%}")
    lines.append(f"- Period return: {crash2018['period_return']:.2%}")
    lines.append(f"- Period max drawdown: {crash2018['max_drawdown']:.2%}")
    lines.append("### 2022")
    lines.append(f"- Min allocation: {crash2022['min_weight']:.2%}")
    lines.append(f"- Max allocation: {crash2022['max_weight']:.2%}")
    lines.append(f"- Period return: {crash2022['period_return']:.2%}")
    lines.append(f"- Period max drawdown: {crash2022['max_drawdown']:.2%}")
    lines.append("")

    lines.append("## Sensitivity (±20% around thresholds)")
    lines.append("`congestion_threshold` in [0.012, 0.015, 0.018], `midpoint_frac` in [0.40, 0.50, 0.60]")
    lines.append("")
    lines.append(sensitivity.to_markdown(index=False, floatfmt=".6f"))
    lines.append("")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify MarketEdge strict-baseline backtest")
    parser.add_argument(
        "--d1",
        type=Path,
        default=Path("data/Binance_BTCUSDT_D1.csv"),
        help="Path to daily OHLCV CSV",
    )
    parser.add_argument(
        "--m15",
        type=Path,
        default=Path("data/Binance_BTCUSDT_15m.csv"),
        help="Path to 15m OHLCV CSV",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("output/marketedge_report.md"),
        help="Output markdown report path",
    )
    parser.add_argument(
        "--trades-out",
        type=Path,
        default=Path("output/marketedge_trades.csv"),
        help="Output CSV path for trade log",
    )
    args = parser.parse_args()

    d1 = load_ohlcv(args.d1, "D1")
    m15 = load_ohlcv(args.m15, "15m")

    result = simulate(d1, m15)
    base_metrics = perf_metrics(result.equity)

    crash2018 = period_summary(result.weights, result.equity, "2018-01-01", "2018-12-31")
    crash2022 = period_summary(result.weights, result.equity, "2022-01-01", "2022-12-31")
    sens = sensitivity_grid(d1, m15)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    report = render_report(result, base_metrics, crash2018, crash2022, sens, args.d1, args.m15)
    args.out.write_text(report, encoding="utf-8")
    if not result.trade_log.empty:
        args.trades_out.parent.mkdir(parents=True, exist_ok=True)
        result.trade_log.to_csv(args.trades_out, index=False)

    print(f"Report written: {args.out}")
    if not result.trade_log.empty:
        print(f"Trade log written: {args.trades_out} ({len(result.trade_log)} rows)")


if __name__ == "__main__":
    main()
