# BTCSIGNAL Shared Memo (Phase 0)

Last updated: 2026-03-02 (JST)

## Fixed purpose
- Phase 0 is record-only.
- No prediction, no rationale, no trade recommendation, no advice.
- Public state is BTC/CASH only.
- Publish daily at 21:10 JST (12:10 UTC) with deterministic publish gate.

## Daily ops (fixed)
- Main branch protection remains ON (PR required).
- Daily update workflow uses PR creation + auto-merge(squash), not direct push.
- Key log line for audit:
  - `publish_gate ... published_at_utc=... delay_sec=... SLO=...`
- Public source of truth:
  - `https://www.btcsignal.org/btcsignal_log_live.json`

## UI / copy constraints
- Dashboard state display: BTC/CASH/unavailable only.
- No WAIT exposure in public UI.
- FAQ includes no-rationale policy (EN/JA).

## X posting policy
- Use "Yesterday's confirmed record" only.
- Keep thread format:
  - 1/2 text (record-only, no advice)
  - 2/2 link only (`https://btcsignal.org`)

## Dev account policy (@naito_xyz)
- English only.
- No engagement threads.
- Wednesday-only Dev Log relay.
- Record-only framing in bio/pinned.

## Analytics policy
- Cloudflare Web Analytics only.
- Weekly review only (not daily).
- Track only: Referrer / Path / Unique.

## Substack Daily generation spec (genki-ops-bot)
- Goal:
  - Generate one daily Substack-ready block after Daily Archive Update completes.
  - Record-only style. BTC/CASH binary state only.
- Trigger:
  - Run only after latest_date and state are confirmed in public record.
  - No fixed clock requirement.
  - Must include: "Published when the public record updates."
- Required inputs from public log:
  - `latest_date` (YYYY-MM-DD)
  - `recorded_state` (BTC|CASH)
  - `last3_states` (latest 3-day sequence)
- Missing input rule:
  - If any required input is missing, do not output draft; return error reason only.

### Required output format (strict)
```text
[SUBSTACK_DAILY]
Subject: {STATE} • Daily Record • {YYYY-MM-DD}

Body:
Confirmed record (BTC / CASH): {STATE}
3-day record: {S1} → {S2} → {S3}
Published when the public record updates.
Record-only. No prediction. No reasoning. No advice. Not investment advice.
Public: https://btcsignal.org
[/SUBSTACK_DAILY]
```

## Substack Weekly Ops generation spec (genki-ops-bot)
- Goal:
  - Generate one weekly Substack-ready block focused on operational health only.
  - No market commentary. Record-only style.
  - Keep metrics minimal and instantly readable.
- Trigger:
  - Run every Wednesday at 20:00 JST.
  - Window is last 7 days including run date.
- Required inputs from public record:
  - `days_published` = number of days with a record in the 7-day window
  - `missing_days` = `7 - days_published`
  - `max_delay_sec` = maximum `delay_sec` in the 7-day window (if available)
- Missing delay data rule:
  - If no `delay_sec` values are available in the window, output `Delay: data unavailable`.
- Missing day rule:
  - If `missing_days > 0`, print one line listing missing dates in `YYYY-MM-DD`.
- Hard constraints:
  - No prediction, no reasoning, no recommendations, no improvement proposals.
  - Facts only.

### Required output format (strict)
```text
[SUBSTACK_WEEKLY]
Subject: Weekly Ops • week ending {YYYY-MM-DD}

Body:
Window: last 7 days (ending {YYYY-MM-DD})
Days published: {X}/7
Missing days: {Y}
{IF Y>0: Missing: YYYY-MM-DD, ...}
{Delay line one of the following:}
- Max delay: {N} sec
or
- Delay: data unavailable

Record-only. No prediction. No reasoning. No advice. Not investment advice.
Public: https://btcsignal.org
[/SUBSTACK_WEEKLY]
```
