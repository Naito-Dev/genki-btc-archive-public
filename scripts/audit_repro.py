#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

import verify_marketedge as vm


def utc_now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def get_commit_hash(path: Path) -> str:
    try:
        out = subprocess.check_output(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return out.strip()
    except Exception:
        return "NO_GIT_REPO"


def find_reference_inventory(path: Path, test_id: str) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("test_id") == test_id:
                return row
    return None


def as_float(v: Any) -> Optional[float]:
    try:
        return float(v)
    except Exception:
        return None


def build_provenance(root: Path) -> Dict[str, Any]:
    return {
        "kpi_candidates": [
            {
                "path": str(root / "output/tradetest_inventory_summary.csv"),
                "notes": "Primary KPI table including rt_0_24pct and stress runs.",
            },
            {
                "path": str(root / "output/tradetest_inventory_summary.json"),
                "notes": "JSON mirror of KPI inventory and report/trade file paths.",
            },
            {
                "path": str(root / "output/marketedge_report.md"),
                "notes": "Rendered report containing final equity and CAGR values.",
            },
            {
                "path": str(root / "scripts/verify_marketedge.py"),
                "notes": "Core baseline simulation logic and metrics calculation.",
            },
            {
                "path": str(root / "scripts/run_b_tests.py"),
                "notes": "IS/OOS and execution timing analysis used by LP evidence.",
            },
            {
                "path": str(root / "scripts/inject_lp_evidence.py"),
                "notes": "Injects KPI values into sales-page evidence sections.",
            },
        ]
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Reproducibility audit for sales KPI claims.")
    parser.add_argument("--asset", default="BTCUSDT", help="Asset tag for metadata only")
    parser.add_argument("--d1", type=Path, default=Path("data/Binance_BTCUSDT_D1.csv"))
    parser.add_argument("--m15", type=Path, default=Path("data/Binance_BTCUSDT_15m.csv"))
    parser.add_argument("--fee_bps", type=float, default=10.0, help="Per-side fee in bps")
    parser.add_argument("--slippage_bps", type=float, default=2.0, help="Per-side slippage in bps")
    parser.add_argument("--execution_mode", default="bar_close", choices=["bar_close"])
    parser.add_argument("--reference_test_id", default="rt_0_24pct")
    parser.add_argument("--outdir", type=Path, default=Path("evidence/repro"))
    args = parser.parse_args()

    root = Path.cwd()
    ts = utc_now_compact()
    run_dir = args.outdir / ts
    run_dir.mkdir(parents=True, exist_ok=True)

    total_per_side = (args.fee_bps + args.slippage_bps) / 10000.0
    vm.COST_PER_SIDE = total_per_side

    d1 = vm.load_ohlcv(args.d1, "D1")
    m15 = vm.load_ohlcv(args.m15, "15m")
    result = vm.simulate(d1, m15)
    met = vm.perf_metrics(result.equity)

    start_utc = str(result.equity.index.min()) if not result.equity.empty else None
    end_utc = str(result.equity.index.max()) if not result.equity.empty else None

    results = {
        "final_equity": met.get("final_equity"),
        "cagr_pct": (met.get("cagr") * 100.0) if met.get("cagr") is not None else None,
        "max_drawdown_pct": (met.get("max_drawdown") * 100.0) if met.get("max_drawdown") is not None else None,
        "trades": result.trades,
        "exposure_pct_avg": result.avg_weight * 100.0,
        "start_utc": start_utc,
        "end_utc": end_utc,
    }

    ref_path = root / "output/tradetest_inventory_summary.csv"
    ref = find_reference_inventory(ref_path, args.reference_test_id)

    comparison: Dict[str, Any] = {"status": "NO_EVIDENCE", "deltas": {}}
    if ref is not None:
        reference = {
            "final_equity": as_float(ref.get("final_equity")),
            "cagr_pct": as_float(ref.get("cagr_pct")),
            "max_drawdown_pct": as_float(ref.get("max_drawdown_pct")),
            "trades": as_float(ref.get("trades")),
            "exposure_pct_avg": as_float(ref.get("exposure_pct_avg")),
        }
        deltas = {}
        for k, rv in reference.items():
            pv = as_float(results.get(k))
            deltas[k] = None if (rv is None or pv is None) else (pv - rv)
        tolerances = {
            "final_equity": 1e-6,
            "cagr_pct": 1e-4,
            "max_drawdown_pct": 1e-4,
            "trades": 0.0,
            "exposure_pct_avg": 1e-4,
        }
        is_match = True
        for k, d in deltas.items():
            if d is None:
                is_match = False
                continue
            if abs(d) > tolerances[k]:
                is_match = False
        comparison = {
            "status": "MATCH" if is_match else "MISMATCH",
            "reference_source": str(ref_path),
            "reference_test_id": args.reference_test_id,
            "reference": reference,
            "deltas": deltas,
        }

    config = {
        "timestamp_utc": ts,
        "asset": args.asset,
        "d1": str(args.d1),
        "m15": str(args.m15),
        "execution_mode": args.execution_mode,
        "fee_bps_per_side": args.fee_bps,
        "slippage_bps_per_side": args.slippage_bps,
        "round_trip_cost_pct": (total_per_side * 2.0) * 100.0,
        "workspace_commit_hash": get_commit_hash(root),
        "public_repo_commit_hash": get_commit_hash(Path("/Users/Claw/genki-btc-archive-public")),
    }

    provenance = build_provenance(root)
    (run_dir / "config.json").write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    (run_dir / "results.json").write_text(
        json.dumps({"results": results, "comparison": comparison}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (run_dir / "provenance.json").write_text(json.dumps(provenance, indent=2, ensure_ascii=False), encoding="utf-8")

    curve = pd.DataFrame({"date": result.equity.index.astype(str), "equity": result.equity.values})
    curve.to_csv(run_dir / "curve.csv", index=False)

    print("audit_run_dir:", run_dir)
    print("comparison_status:", comparison["status"])
    print("final_equity:", results["final_equity"])
    print("cagr_pct:", results["cagr_pct"])
    print("max_drawdown_pct:", results["max_drawdown_pct"])
    print("trades:", results["trades"])


if __name__ == "__main__":
    main()
