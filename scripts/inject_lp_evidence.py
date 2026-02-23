#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

import pandas as pd


ROOT = Path("/Users/Claw/tradep-test")
OUT = ROOT / "output"
EN_PATH = Path("/Users/Claw/genki-btc-archive-public/sales-page-en.html")
JA_PATH = Path("/Users/Claw/genki-btc-archive-public/sales-page-ja.html")


def load_inputs():
    bench = pd.read_csv(OUT / "marketedge_benchmark_table.csv")
    exec_oos = pd.read_csv(OUT / "marketedge_exec_timing_oos.csv")
    is_oos = pd.read_csv(OUT / "marketedge_is_oos_summary.csv")
    wf = pd.read_csv(OUT / "marketedge_walkforward_composite_summary.csv")
    return bench, exec_oos, is_oos, wf


def pick_values(bench: pd.DataFrame, exec_oos: pd.DataFrame, is_oos: pd.DataFrame, wf: pd.DataFrame):
    comp = wf[wf["section"] == "composite_oos_equity"].iloc[0]
    dcagr = wf[wf["section"] == "distribution_cagr_pct"].iloc[0]
    dmaxdd = wf[wf["section"] == "distribution_maxdd_pct"].iloc[0]
    dsharpe = wf[wf["section"] == "distribution_sharpe_rf0"].iloc[0]
    dexpo = wf[wf["section"] == "distribution_exposure_pct"].iloc[0]

    oos024 = is_oos[(is_oos["segment"] == "OOS") & (is_oos["round_trip_cost_pct"] == 0.24)].iloc[0]
    oos100 = is_oos[(is_oos["segment"] == "OOS") & (is_oos["round_trip_cost_pct"] == 1.0)].iloc[0]

    e_close_024 = exec_oos[(exec_oos["cost"] == "0.24%") & (exec_oos["execution_model"] == "bar_close")].iloc[0]
    e_next_024 = exec_oos[(exec_oos["cost"] == "0.24%") & (exec_oos["execution_model"] == "next_bar_open")].iloc[0]

    bh = bench[bench["strategy"] == "BTC Buy&Hold"].iloc[0]
    dma = bench[bench["strategy"] == "Simple 200DMA (100/0)"].iloc[0]
    me024 = bench[bench["strategy"] == "MarketEdge (RT cost 0.24%)"].iloc[0]
    me100 = bench[bench["strategy"] == "MarketEdge (RT cost 1.00%)"].iloc[0]

    return {
        "comp": comp,
        "dcagr": dcagr,
        "dmaxdd": dmaxdd,
        "dsharpe": dsharpe,
        "dexpo": dexpo,
        "oos024": oos024,
        "oos100": oos100,
        "e_close_024": e_close_024,
        "e_next_024": e_next_024,
        "bh": bh,
        "dma": dma,
        "me024": me024,
        "me100": me100,
    }


def build_en(v) -> str:
    return f"""
    <section>
      <div class="wrap card">
        <h2>Evidence &amp; Robustness</h2>
        <p>
          Composite OOS (stitched): CAGR <strong>{v['comp']['cagr_pct']:.2f}%</strong>, MaxDD <strong>{v['comp']['maxdd_pct']:.2f}%</strong>, MAR <strong>{v['comp']['mar']:.2f}</strong>.
          Median 6M OOS window: CAGR <strong>{v['dcagr']['median']:.2f}%</strong> (q25 {v['dcagr']['q25']:.2f} / q75 {v['dcagr']['q75']:.2f}),
          MaxDD <strong>{v['dmaxdd']['median']:.2f}%</strong> (q25 {v['dmaxdd']['q25']:.2f} / q75 {v['dmaxdd']['q75']:.2f}),
          Sharpe {v['dsharpe']['median']:.2f}, Exposure {v['dexpo']['median']:.2f}%.
        </p>
        <p>
          OOS execution delay check (0.24% RT): bar_close CAGR {v['e_close_024']['cagr']:.2f}% vs next_bar_open {v['e_next_024']['cagr']:.2f}% (near-identical).
        </p>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Strategy</th>
                <th>CAGR</th>
                <th>MaxDD</th>
                <th>Sharpe</th>
                <th>MAR</th>
                <th>Exposure</th>
                <th>Worst Month</th>
              </tr>
            </thead>
            <tbody>
              <tr><td>BTC Buy&amp;Hold</td><td>{v['bh']['CAGR']:.2f}%</td><td>{v['bh']['MaxDD']:.2f}%</td><td>{v['bh']['Sharpe(rf=0)']:.2f}</td><td>{v['bh']['MAR']:.2f}</td><td>{v['bh']['Exposure(%)']:.2f}%</td><td>{v['bh']['Worst Month']:.2f}%</td></tr>
              <tr><td>Simple 200DMA</td><td>{v['dma']['CAGR']:.2f}%</td><td>{v['dma']['MaxDD']:.2f}%</td><td>{v['dma']['Sharpe(rf=0)']:.2f}</td><td>{v['dma']['MAR']:.2f}</td><td>{v['dma']['Exposure(%)']:.2f}%</td><td>{v['dma']['Worst Month']:.2f}%</td></tr>
              <tr><td>MarketEdge (0.24% RT)</td><td>{v['me024']['CAGR']:.2f}%</td><td>{v['me024']['MaxDD']:.2f}%</td><td>{v['me024']['Sharpe(rf=0)']:.2f}</td><td>{v['me024']['MAR']:.2f}</td><td>{v['me024']['Exposure(%)']:.2f}%</td><td>{v['me024']['Worst Month']:.2f}%</td></tr>
              <tr><td>MarketEdge (1.00% RT)</td><td>{v['me100']['CAGR']:.2f}%</td><td>{v['me100']['MaxDD']:.2f}%</td><td>{v['me100']['Sharpe(rf=0)']:.2f}</td><td>{v['me100']['MAR']:.2f}</td><td>{v['me100']['Exposure(%)']:.2f}%</td><td>{v['me100']['Worst Month']:.2f}%</td></tr>
            </tbody>
          </table>
        </div>
        <p>
          <img src="output/marketedge_walkforward_composite.png" alt="Walk-forward composite OOS equity curve" style="width:100%;border:1px solid var(--line);border-radius:12px;margin-top:14px;">
        </p>
        <p class="small">
          Footnotes: UTC timestamps, half-open windows [start, end), execution = next_bar_open, round-trip cost = 0.24%, data = Binance BTCUSDT D1 + 15m.
        </p>
      </div>
    </section>
""".strip("\n")


def build_ja(v) -> str:
    return f"""
    <section>
      <div class="wrap card">
        <h2>Evidence &amp; Robustness（検証根拠）</h2>
        <p>
          OOS合成（窓連結・複利）: CAGR <strong>{v['comp']['cagr_pct']:.2f}%</strong>、MaxDD <strong>{v['comp']['maxdd_pct']:.2f}%</strong>、MAR <strong>{v['comp']['mar']:.2f}</strong>。
          6か月OOS窓の中央値: CAGR <strong>{v['dcagr']['median']:.2f}%</strong>（q25 {v['dcagr']['q25']:.2f} / q75 {v['dcagr']['q75']:.2f}）、
          MaxDD <strong>{v['dmaxdd']['median']:.2f}%</strong>（q25 {v['dmaxdd']['q25']:.2f} / q75 {v['dmaxdd']['q75']:.2f}）、
          Sharpe {v['dsharpe']['median']:.2f}、Exposure {v['dexpo']['median']:.2f}%。
        </p>
        <p>
          OOS執行遅延比較（往復0.24%）: bar_close のCAGR {v['e_close_024']['cagr']:.2f}% と next_bar_open のCAGR {v['e_next_024']['cagr']:.2f}% はほぼ同水準です。
        </p>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>戦略</th>
                <th>CAGR</th>
                <th>MaxDD</th>
                <th>Sharpe</th>
                <th>MAR</th>
                <th>Exposure</th>
                <th>Worst Month</th>
              </tr>
            </thead>
            <tbody>
              <tr><td>BTC Buy&amp;Hold</td><td>{v['bh']['CAGR']:.2f}%</td><td>{v['bh']['MaxDD']:.2f}%</td><td>{v['bh']['Sharpe(rf=0)']:.2f}</td><td>{v['bh']['MAR']:.2f}</td><td>{v['bh']['Exposure(%)']:.2f}%</td><td>{v['bh']['Worst Month']:.2f}%</td></tr>
              <tr><td>Simple 200DMA</td><td>{v['dma']['CAGR']:.2f}%</td><td>{v['dma']['MaxDD']:.2f}%</td><td>{v['dma']['Sharpe(rf=0)']:.2f}</td><td>{v['dma']['MAR']:.2f}</td><td>{v['dma']['Exposure(%)']:.2f}%</td><td>{v['dma']['Worst Month']:.2f}%</td></tr>
              <tr><td>MarketEdge（往復0.24%）</td><td>{v['me024']['CAGR']:.2f}%</td><td>{v['me024']['MaxDD']:.2f}%</td><td>{v['me024']['Sharpe(rf=0)']:.2f}</td><td>{v['me024']['MAR']:.2f}</td><td>{v['me024']['Exposure(%)']:.2f}%</td><td>{v['me024']['Worst Month']:.2f}%</td></tr>
              <tr><td>MarketEdge（往復1.00%）</td><td>{v['me100']['CAGR']:.2f}%</td><td>{v['me100']['MaxDD']:.2f}%</td><td>{v['me100']['Sharpe(rf=0)']:.2f}</td><td>{v['me100']['MAR']:.2f}</td><td>{v['me100']['Exposure(%)']:.2f}%</td><td>{v['me100']['Worst Month']:.2f}%</td></tr>
            </tbody>
          </table>
        </div>
        <p>
          <img src="output/marketedge_walkforward_composite.png" alt="Walk-forward OOS合成エクイティ" style="width:100%;border:1px solid var(--line);border-radius:12px;margin-top:14px;">
        </p>
        <p class="small">
          注釈: UTC固定、半開区間 [start, end)、execution = next_bar_open、往復コスト = 0.24%、データ = Binance BTCUSDT D1 + 15m。
        </p>
      </div>
    </section>
""".strip("\n")


def insert_after_first_section(html: str, block: str) -> str:
    marker_start = "<!-- EVIDENCE_SECTION_START -->"
    marker_end = "<!-- EVIDENCE_SECTION_END -->"
    if marker_start in html and marker_end in html:
        pre = html.split(marker_start)[0]
        post = html.split(marker_end)[1]
        return pre + marker_start + "\n" + block + "\n" + marker_end + post

    target = "</section>"
    i = html.find(target)
    if i == -1:
        raise ValueError("No section closing tag found for insertion")
    j = i + len(target)
    return html[:j] + "\n\n    " + marker_start + "\n" + block + "\n    " + marker_end + html[j:]


def write_walkforward_note(v) -> None:
    note = (
        f"Composite OOS CAGR {v['comp']['cagr_pct']:.2f}% is calculated from stitched compounding across sequential 6M OOS windows, "
        "so it reflects path-dependent chaining across windows.\n"
        f"For a conservative cross-window view, median 6M OOS window CAGR is {v['dcagr']['median']:.2f}% "
        f"(q25 {v['dcagr']['q25']:.2f} / q75 {v['dcagr']['q75']:.2f}).\n"
    )
    (OUT / "marketedge_walkforward_note.md").write_text(note, encoding="utf-8")


def main() -> None:
    bench, exec_oos, is_oos, wf = load_inputs()
    v = pick_values(bench, exec_oos, is_oos, wf)

    en_html = EN_PATH.read_text(encoding="utf-8")
    ja_html = JA_PATH.read_text(encoding="utf-8")

    en_block = build_en(v)
    ja_block = build_ja(v)

    EN_PATH.write_text(insert_after_first_section(en_html, en_block), encoding="utf-8")
    JA_PATH.write_text(insert_after_first_section(ja_html, ja_block), encoding="utf-8")
    write_walkforward_note(v)

    print(str(EN_PATH))
    print(str(JA_PATH))
    print(str(OUT / "marketedge_walkforward_note.md"))


if __name__ == "__main__":
    main()
