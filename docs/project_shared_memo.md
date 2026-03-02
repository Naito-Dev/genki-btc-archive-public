# BTCSIGNAL Shared Ops Memo

Updated: 2026-03-02

## Daily pipeline fail-closed behavior
- Daily workflow writes `substack/status.json` on every run (heartbeat).
- `substack/status.json` always includes:
  - `run_id`
  - `run_utc`
  - `workflow`
  - `last_known_record_date`
  - `last_known_state`
  - `status` (`ok` / `record_missing` / `pipeline_failed`)
- No state is fabricated. If record data is missing, status is `record_missing`.

## PR-required compatibility
- Daily updates do not push to `main` directly.
- Workflow creates/updates a PR and enables squash auto-merge.
- If PR creation/merge fails, workflow fails and sends Discord alert with run context.

## Silent-stall prevention
- `Substack Status Monitor` runs every 6 hours.
- If `substack/status.json` is older than 24 hours, it fails and sends Discord alert.
- Auto-heal: when stale is detected and cooldown allows, monitor dispatches one `Daily Archive Update` run automatically.
- Loop guard: `substack/autoheal.json` stores `last_autoheal_utc`; monitor will not auto-heal again within 24 hours.
