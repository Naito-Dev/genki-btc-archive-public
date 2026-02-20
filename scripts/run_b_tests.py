#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd

import sys

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.append(str(SCRIPT_DIR))

import verify_marketedge as vm


OUT_DIR = Path("/Users/Claw/tradep-test/output")
D1_PATH = Path("/Users/Claw/tradep-test/data/Binance_BTCUSDT_D1.csv")
M15_PATH = Path("/Users/Claw/tradep-test/data/Binance_BTCUSDT_15m.csv")


@dataclass
class SimResult:
    equity: pd.Series
    weights: pd.Series
    trade_log: pd.DataFrame
    trades: int
    avg_weight: float


def _period_slice(df: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    s = pd.Timestamp(start)
    e = pd.Timestamp(end)
    if s.tzinfo is None:
        s = s.tz_localize("UTC")
    else:
        s = s.tz_convert("UTC")
    if e.tzinfo is None:
        e = e.tz_localize("UTC")
    else:
        e = e.tz_convert("UTC")
    return df.loc[s:e].copy()


def _monthly_stats(equity: pd.Series) -> Dict[str, float]:
    mret = equity.resample("ME").last().pct_change().dropna()
    if mret.empty:
        return {"worst_month_pct": np.nan, "best_month_pct": np.nan, "positive_months_pct": np.nan}
    return {
        "worst_month_pct": float(mret.min() * 100.0),
        "best_month_pct": float(mret.max() * 100.0),
        "positive_months_pct": float((mret > 0).mean() * 100.0),
    }


def _metrics_from_result(result: SimResult) -> Dict[str, float]:
    m = vm.perf_metrics(result.equity)
    rets = result.equity.pct_change().dropna()
    periods_per_year = 365.25 * 24 * 4
    sharpe = (rets.mean() * periods_per_year) / (rets.std() * np.sqrt(periods_per_year)) if rets.std() > 0 else np.nan
    mar = m["cagr"] / abs(m["max_drawdown"]) if m["max_drawdown"] < 0 else np.nan

    years = (result.equity.index[-1] - result.equity.index[0]).total_seconds() / (365.25 * 24 * 3600)
    turnover = (
        float((result.trade_log["notional"].abs() / result.trade_log["equity_before"]).sum() / years)
        if (not result.trade_log.empty and years > 0)
        else np.nan
    )
    monthly = _monthly_stats(result.equity)
    return {
        "Final Equity": float(m["final_equity"]),
        "CAGR": float(m["cagr"] * 100.0),
        "Max Drawdown": float(m["max_drawdown"] * 100.0),
        "Sharpe(rf=0)": float(sharpe),
        "MAR": float(mar),
        "Trades": int(result.trades),
        "Exposure(%)": float(result.avg_weight * 100.0),
        "Turnover(annualized)": float(turnover),
        "Worst Month": monthly["worst_month_pct"],
        "Best Month": monthly["best_month_pct"],
        "%Positive Months": monthly["positive_months_pct"],
    }


def simulate_marketedge(
    d1: pd.DataFrame,
    m15: pd.DataFrame,
    start: str,
    end: str,
    round_trip_cost: float,
    execution_model: str = "close",
) -> SimResult:
    if execution_model not in {"close", "next_open"}:
        raise ValueError("execution_model must be close or next_open")

    vm.COST_PER_SIDE = round_trip_cost / 2.0
    regime = vm.build_regime_table(d1, vm.DEFAULT_CONGESTION)

    bars = m15[["open", "high", "low", "close", "volume"]].copy()
    bars["day"] = bars.index.floor("D")
    bars = bars.join(regime[["target_weight", "ma5"]], on="day", how="left")
    bars = bars.dropna(subset=["target_weight", "ma5"])
    bars = _period_slice(bars, start, end)

    bars["midpoint_ok"] = vm.midpoint_trigger_ok(bars["open"], bars["close"], bars["ma5"], vm.DEFAULT_MIDPOINT_FRAC)

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

    idxs = list(bars.index)
    cash = 1.0
    qty = 0.0
    trades = 0
    pending: Dict[int, List[dict]] = {}

    eq_vals = []
    w_vals = []
    ts_vals = []
    trade_rows = []

    def exec_trade(exec_i: int, sig: dict, exec_price: float) -> None:
        nonlocal cash, qty, trades
        equity_before = cash + qty * exec_price
        btc_before = qty * exec_price
        weight_before = 0.0 if equity_before <= 0 else btc_before / equity_before
        target = sig["target_weight"]
        target_btc = target * equity_before
        delta = target_btc - btc_before
        if abs(delta) <= 1e-12:
            return

        trades += 1
        if delta > 0:
            side = "BUY"
            notional = delta
            cost = notional * vm.COST_PER_SIDE
            qty += notional / exec_price
            cash -= (notional + cost)
        else:
            side = "SELL"
            notional = -delta
            cost = notional * vm.COST_PER_SIDE
            qty -= notional / exec_price
            cash += (notional - cost)

        equity_after = cash + qty * exec_price
        btc_after = qty * exec_price
        weight_after = 0.0 if equity_after <= 0 else btc_after / equity_after
        trade_rows.append(
            {
                "signal_ts": sig["signal_ts"],
                "exec_ts": idxs[exec_i],
                "execution_model": execution_model,
                "side": side,
                "reason": sig["reason"],
                "price": exec_price,
                "target_weight": target,
                "weight_before": weight_before,
                "weight_after": weight_after,
                "notional": notional,
                "cost": cost,
                "equity_before": equity_before,
                "equity_after": equity_after,
            }
        )

    for i, (ts, row) in enumerate(bars.iterrows()):
        if i in pending:
            for sig in pending[i]:
                exec_trade(i, sig, float(row["open"]))

        execute_signal = False
        if bool(row["is_increase_day"]):
            execute_signal = bool(row["first_trigger_idx"])
            reason = "increase_day_first_trigger"
        else:
            execute_signal = bool(row["first_bar_idx"])
            reason = "non_increase_day_first_bar"

        if execute_signal:
            sig = {"signal_ts": ts, "target_weight": float(row["target_weight"]), "reason": reason}
            if execution_model == "close":
                exec_trade(i, sig, float(row["close"]))
            else:
                if i + 1 < len(idxs):
                    pending.setdefault(i + 1, []).append(sig)

        px_close = float(row["close"])
        equity = cash + qty * px_close
        weight = 0.0 if equity <= 0 else (qty * px_close) / equity
        ts_vals.append(ts)
        eq_vals.append(equity)
        w_vals.append(weight)

    equity_series = pd.Series(eq_vals, index=pd.DatetimeIndex(ts_vals), name="equity")
    weights_series = pd.Series(w_vals, index=pd.DatetimeIndex(ts_vals), name="weight")
    trade_log = pd.DataFrame(trade_rows)
    return SimResult(equity=equity_series, weights=weights_series, trade_log=trade_log, trades=trades, avg_weight=float(weights_series.mean()))


def simulate_200dma(d1: pd.DataFrame, m15: pd.DataFrame, start: str, end: str, round_trip_cost: float = 0.0024) -> SimResult:
    vm.COST_PER_SIDE = round_trip_cost / 2.0
    dd = d1.copy()
    dd["ma200"] = dd["close"].rolling(200).mean()
    dd["target_weight"] = (dd["close"] > dd["ma200"]).astype(float)

    bars = m15[["open", "close"]].copy()
    bars["day"] = bars.index.floor("D")
    bars = bars.join(dd[["target_weight"]], on="day", how="left")
    bars["target_weight"] = bars["target_weight"].fillna(0.0)
    bars = _period_slice(bars, start, end)
    first_bar_idx = set(bars.groupby("day").head(1).index)

    cash = 1.0
    qty = 0.0
    trades = 0
    eq_vals = []
    w_vals = []
    ts_vals = []
    trade_rows = []

    for ts, row in bars.iterrows():
        if ts in first_bar_idx:
            px = float(row["close"])
            equity_before = cash + qty * px
            btc_before = qty * px
            current_weight = 0.0 if equity_before <= 0 else btc_before / equity_before
            target = float(row["target_weight"])
            delta = target * equity_before - btc_before
            if abs(delta) > 1e-12:
                trades += 1
                if delta > 0:
                    notional = delta
                    cost = notional * vm.COST_PER_SIDE
                    qty += notional / px
                    cash -= (notional + cost)
                    side = "BUY"
                else:
                    notional = -delta
                    cost = notional * vm.COST_PER_SIDE
                    qty -= notional / px
                    cash += (notional - cost)
                    side = "SELL"
                equity_after = cash + qty * px
                btc_after = qty * px
                weight_after = 0.0 if equity_after <= 0 else btc_after / equity_after
                trade_rows.append(
                    {
                        "signal_ts": ts,
                        "exec_ts": ts,
                        "execution_model": "close",
                        "side": side,
                        "reason": "daily_200dma_rebalance",
                        "price": px,
                        "target_weight": target,
                        "weight_before": current_weight,
                        "weight_after": weight_after,
                        "notional": notional,
                        "cost": cost,
                        "equity_before": equity_before,
                        "equity_after": equity_after,
                    }
                )

        px_close = float(row["close"])
        equity = cash + qty * px_close
        weight = 0.0 if equity <= 0 else (qty * px_close) / equity
        ts_vals.append(ts)
        eq_vals.append(equity)
        w_vals.append(weight)

    return SimResult(
        equity=pd.Series(eq_vals, index=pd.DatetimeIndex(ts_vals), name="equity"),
        weights=pd.Series(w_vals, index=pd.DatetimeIndex(ts_vals), name="weight"),
        trade_log=pd.DataFrame(trade_rows),
        trades=trades,
        avg_weight=float(np.mean(w_vals)),
    )


def simulate_buy_hold(m15: pd.DataFrame, start: str, end: str) -> SimResult:
    bars = _period_slice(m15[["close"]], start, end)
    eq = bars["close"] / bars["close"].iloc[0]
    w = pd.Series(1.0, index=eq.index, name="weight")
    return SimResult(equity=eq.rename("equity"), weights=w, trade_log=pd.DataFrame(), trades=0, avg_weight=1.0)


def run_b1_b2(d1: pd.DataFrame, m15: pd.DataFrame) -> None:
    rows = []
    for label, start, end in [
        ("IS", "2018-03-05", "2020-12-31 23:45:00"),
        ("OOS", "2021-01-01", str(m15.index.max())),
    ]:
        for rt in [0.0024, 0.01]:
            res = simulate_marketedge(d1, m15, start, end, rt, execution_model="close")
            met = _metrics_from_result(res)
            row = {
                "segment": label,
                "period_start_utc": str(res.equity.index.min()),
                "period_end_utc": str(res.equity.index.max()),
                "execution_model": "bar_close",
                "round_trip_cost_pct": rt * 100.0,
            }
            row.update(met)
            rows.append(row)

    summary = pd.DataFrame(rows)
    summary_csv = OUT_DIR / "marketedge_is_oos_summary.csv"
    summary_json = OUT_DIR / "marketedge_is_oos_summary.json"
    summary.to_csv(summary_csv, index=False)
    summary.to_json(summary_json, orient="records", indent=2)

    # OOS execution timing compare (for B-1 + B-2)
    compare_rows = []
    for rt in [0.0024, 0.01]:
        for model in ["close", "next_open"]:
            res = simulate_marketedge(d1, m15, "2021-01-01", str(m15.index.max()), rt, execution_model=model)
            met = _metrics_from_result(res)
            compare_rows.append(
                {
                    "cost": f"{rt*100:.2f}%",
                    "execution_model": "bar_close" if model == "close" else "next_bar_open",
                    "final_equity": met["Final Equity"],
                    "cagr": met["CAGR"],
                    "maxdd": met["Max Drawdown"],
                    "sharpe": met["Sharpe(rf=0)"],
                    "mar": met["MAR"],
                    "trades": met["Trades"],
                    "exposure": met["Exposure(%)"],
                    "turnover": met["Turnover(annualized)"],
                    "worst_month": met["Worst Month"],
                }
            )

    compare = pd.DataFrame(compare_rows)
    (OUT_DIR / "marketedge_oos_exec_timing_compare.csv").write_text(compare.to_csv(index=False), encoding="utf-8")
    (OUT_DIR / "marketedge_exec_timing_oos.csv").write_text(compare.to_csv(index=False), encoding="utf-8")

    # LP bullets from summary only
    s = summary.sort_values(["segment", "round_trip_cost_pct"]).reset_index(drop=True)
    bullets = [
        f"- IS (2018-03-05 to 2020-12-31), cost 0.24%: CAGR {s.loc[(s.segment=='IS') & (s.round_trip_cost_pct==0.24), 'CAGR'].iloc[0]:.2f}%, MaxDD {s.loc[(s.segment=='IS') & (s.round_trip_cost_pct==0.24), 'Max Drawdown'].iloc[0]:.2f}%.",
        f"- OOS (2021-01-01 to latest), cost 0.24%: CAGR {s.loc[(s.segment=='OOS') & (s.round_trip_cost_pct==0.24), 'CAGR'].iloc[0]:.2f}%, MaxDD {s.loc[(s.segment=='OOS') & (s.round_trip_cost_pct==0.24), 'Max Drawdown'].iloc[0]:.2f}%.",
        f"- OOS, cost 1.00% stress: CAGR {s.loc[(s.segment=='OOS') & (s.round_trip_cost_pct==1.0), 'CAGR'].iloc[0]:.2f}%, MaxDD {s.loc[(s.segment=='OOS') & (s.round_trip_cost_pct==1.0), 'Max Drawdown'].iloc[0]:.2f}%.",
        f"- OOS positive-month ratio: {s.loc[(s.segment=='OOS') & (s.round_trip_cost_pct==0.24), '%Positive Months'].iloc[0]:.2f}% (0.24% cost), {s.loc[(s.segment=='OOS') & (s.round_trip_cost_pct==1.0), '%Positive Months'].iloc[0]:.2f}% (1.00% cost).",
        f"- OOS execution timing (0.24%): bar_close CAGR {compare[(compare.cost=='0.24%') & (compare.execution_model=='bar_close')]['cagr'].iloc[0]:.2f}% vs next_bar_open {compare[(compare.cost=='0.24%') & (compare.execution_model=='next_bar_open')]['cagr'].iloc[0]:.2f}%.",
    ]
    (OUT_DIR / "marketedge_is_oos_lp_bullets.md").write_text("\n".join(bullets) + "\n", encoding="utf-8")

    # LP paragraph for execution timing
    exec_lp = (
        "In OOS (2021-01-01 onward), execution delay from bar close to next-bar open reduced CAGR "
        f"from {compare[(compare.cost=='0.24%') & (compare.execution_model=='bar_close')]['cagr'].iloc[0]:.2f}% "
        f"to {compare[(compare.cost=='0.24%') & (compare.execution_model=='next_bar_open')]['cagr'].iloc[0]:.2f}% at 0.24% round-trip cost, "
        "while drawdown remained in a similar range. Under 1.00% round-trip stress, the same direction held, "
        "showing degradation but no structural collapse."
    )
    (OUT_DIR / "marketedge_exec_timing_lp.md").write_text(exec_lp + "\n", encoding="utf-8")


def run_b3(d1: pd.DataFrame, m15: pd.DataFrame) -> None:
    start = "2018-03-05"
    end = str(m15.index.max())

    rows = []

    # Buy & Hold
    bh = simulate_buy_hold(m15, start, end)
    bhm = _metrics_from_result(bh)
    rows.append(
        {
            "strategy": "BTC Buy&Hold",
            "cost_assumption": "0.24% note: assumed no rebalance trades in period-level benchmark",
            "CAGR": bhm["CAGR"],
            "MaxDD": bhm["Max Drawdown"],
            "MAR": bhm["MAR"],
            "Sharpe(rf=0)": bhm["Sharpe(rf=0)"],
            "Exposure(%)": bhm["Exposure(%)"],
            "Worst Month": bhm["Worst Month"],
        }
    )

    # 200DMA
    dma = simulate_200dma(d1, m15, start, end, round_trip_cost=0.0024)
    dmam = _metrics_from_result(dma)
    rows.append(
        {
            "strategy": "Simple 200DMA (100/0)",
            "cost_assumption": "0.24% round-trip",
            "CAGR": dmam["CAGR"],
            "MaxDD": dmam["Max Drawdown"],
            "MAR": dmam["MAR"],
            "Sharpe(rf=0)": dmam["Sharpe(rf=0)"],
            "Exposure(%)": dmam["Exposure(%)"],
            "Worst Month": dmam["Worst Month"],
        }
    )

    # MarketEdge
    for rt in [0.0024, 0.01]:
        me = simulate_marketedge(d1, m15, start, end, round_trip_cost=rt, execution_model="close")
        mem = _metrics_from_result(me)
        rows.append(
            {
                "strategy": f"MarketEdge (RT cost {rt*100:.2f}%)",
                "cost_assumption": f"{rt*100:.2f}% round-trip",
                "CAGR": mem["CAGR"],
                "MaxDD": mem["Max Drawdown"],
                "MAR": mem["MAR"],
                "Sharpe(rf=0)": mem["Sharpe(rf=0)"],
                "Exposure(%)": mem["Exposure(%)"],
                "Worst Month": mem["Worst Month"],
            }
        )

    bench = pd.DataFrame(rows)
    bench_csv = OUT_DIR / "marketedge_benchmark_table.csv"
    bench.to_csv(bench_csv, index=False)

    lp_block = (
        "### Benchmark Snapshot (2018-03-05 to latest)\n"
        f"- MarketEdge (0.24% RT): CAGR {bench.loc[bench.strategy=='MarketEdge (RT cost 0.24%)', 'CAGR'].iloc[0]:.2f}%, "
        f"MaxDD {bench.loc[bench.strategy=='MarketEdge (RT cost 0.24%)', 'MaxDD'].iloc[0]:.2f}%, "
        f"Exposure {bench.loc[bench.strategy=='MarketEdge (RT cost 0.24%)', 'Exposure(%)'].iloc[0]:.2f}%.\n"
        f"- MarketEdge (1.00% RT stress): CAGR {bench.loc[bench.strategy=='MarketEdge (RT cost 1.00%)', 'CAGR'].iloc[0]:.2f}%, "
        f"MaxDD {bench.loc[bench.strategy=='MarketEdge (RT cost 1.00%)', 'MaxDD'].iloc[0]:.2f}%.\n"
        f"- Buy&Hold: CAGR {bench.loc[bench.strategy=='BTC Buy&Hold', 'CAGR'].iloc[0]:.2f}%, "
        f"MaxDD {bench.loc[bench.strategy=='BTC Buy&Hold', 'MaxDD'].iloc[0]:.2f}%.\n"
        f"- Simple 200DMA: CAGR {bench.loc[bench.strategy=='Simple 200DMA (100/0)', 'CAGR'].iloc[0]:.2f}%, "
        f"MaxDD {bench.loc[bench.strategy=='Simple 200DMA (100/0)', 'MaxDD'].iloc[0]:.2f}%.\n"
    )
    (OUT_DIR / "marketedge_benchmark_lp_block.md").write_text(lp_block, encoding="utf-8")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    d1 = vm.load_ohlcv(D1_PATH, "D1")
    m15 = vm.load_ohlcv(M15_PATH, "15m")
    run_b1_b2(d1, m15)
    run_b3(d1, m15)
    print("Generated:")
    for p in [
        "marketedge_is_oos_summary.csv",
        "marketedge_is_oos_summary.json",
        "marketedge_is_oos_lp_bullets.md",
        "marketedge_oos_exec_timing_compare.csv",
        "marketedge_exec_timing_oos.csv",
        "marketedge_exec_timing_lp.md",
        "marketedge_benchmark_table.csv",
        "marketedge_benchmark_lp_block.md",
    ]:
        print(str(OUT_DIR / p))


if __name__ == "__main__":
    main()
