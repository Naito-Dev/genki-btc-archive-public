# Revenue Strategy Single Source (BTCSIGNAL)

## Purpose
- This file is the single source of truth for revenue strategy.
- Engineering (Codex/OpenClaw) must NOT change strategy without updating this file.
- Strategy (Antigravity) must NOT propose code changes here; only revenue decisions.

## North Star
- Stable revenue: 600,000 JPY/month (≈ $4,000/month)

## Phase0 Policy (Fixed, non-negotiable)
- Record-only (BTC/CASH). No prediction. No reasoning. No advice.
- Simultaneous public release.
- Fairness: published_at_utc is enforced for live phase (contract_start=2026-02-26, bootstrap exception recorded).
- Paid is OFF during the 6-month validation period.

## Phase 0 KPI Progress (as of 2026-03-03)
- Audit PASS streak rule: `chain_integrity == "VALID"` (from `logs/*.json`)
- Current Audit PASS streak: `8 days`

## Pricing & Packaging (Target)
| Tier | Price | What the customer gets | Notes |
|---|---:|---|---|
| Free | $0 | Weekly summary (email) | Primary list building |
| Daily Log | $9/mo | Daily push email of the confirmed record (same info, push convenience) | Paid toggle remains OFF until validation complete |
| Developer Notes | $29/mo | Monthly developer/ops note (technical + stats, no advice) | Optional upsell |

### Revenue model to reach ~$4,000/mo
- $9 × 200 = $1,800
- $29 × 75 = $2,175
- Total = $3,975 (~600k JPY)

## Conversion Funnel (Fixed)
1) X daily/weekly posts
2) Reply with Substack Free link
3) Substack Free: weekly email for 6 months
4) Each email ends with: “Upgrade to Daily Log ->” (future)
5) btcsignal.org = trust/verification hub (NOT checkout)
6) Final conversion happens on Substack (Paid)

## Messaging (Copy)
### Headline
One state. Every day. On time.

### 3 bullets
- The algorithm runs daily at 12:12 UTC. The record is published the moment it confirms.
- Two outputs only: BTC or CASH. No reasoning. No prediction. No noise.
- Every entry is immutable and timestamped. Verify the full log at btcsignal.org.

### Disclaimer (must be included)
This is an automated record-keeping service. It does not constitute investment advice or solicitation of any kind.
The state published (BTC / CASH) reflects the output of a rules-based algorithm and is provided for informational purposes only.
Past records do not guarantee future results. You are solely responsible for any decisions made based on this information.

## 30-Day Plan (Max 2 tasks/week)
### Week 1
- Task 1: Update X bio + pinned to Substack Free link
- Task 2: Change LP CTA to “Subscribe (Free) ->” (direct to Substack)
- KPI: +20 free subscribers

### Week 2
- Task 1: Ensure X daily bot runs 7 consecutive days
- Task 2: Publish 1 weekly Substack post manually
- KPI: 7-day streak / open rate > 40%

### Week 3
- Task 1: Rewrite Substack “About” using Messaging copy above
- Task 2: Fix weekly ops routine for checking error logs (weekly)
- KPI: 50 total free subscribers

### Week 4
- Task 1: Test automation for X weekly post
- Task 2: Ensure btcsignal.org top page error indicators are clean
- KPI: auto post success / zero confusing UI errors

## Guardrails
- No strategy changes without updating this file.
- No engineering changes that affect pricing/funnel/copy without updating this file.
