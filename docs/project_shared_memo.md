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
