# MarketEdge Output Restore TODO (Hold)

## Status (fixed)
- Core implementation line is intact (`86097d5`, `scripts/verify_marketedge.py`).
- Current priority is strict70_to0 development/verification, not output artifact recovery.
- The files below are marked as `NEEDS_RESTORE` and intentionally deferred.

## Missing files (`NEEDS_RESTORE`)
1. `output/marketedge_trades_rt_0_24pct.csv`
2. `output/marketedge_trades_rt_0_50pct.csv`
3. `output/marketedge_trades_rt_0_60pct.csv`
4. `output/marketedge_trades_rt_0_80pct.csv`
5. `output/marketedge_trades_rt_1_00pct.csv`
6. `output/marketedge_walkforward_composite.png`
7. `output/marketedge_walkforward_composite_summary.csv`
8. `output/marketedge_walkforward_lp_block.md`
9. `output/marketedge_walkforward_note.md`
10. `output/marketedge_walkforward_oos_windows.csv`

## Policy
- Do not restore now.
- Restore only when required for LP/verification deliverables.
- Prefer deterministic regeneration over manual recreation when regeneration path is available.

## Trigger for restore
- A deliverable explicitly requires one or more `NEEDS_RESTORE` files.
- Regeneration script/path is confirmed and reproducible.
