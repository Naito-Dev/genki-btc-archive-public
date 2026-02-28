# Ops Incident Policy (Phase 0)

Purpose
- When the daily record is delayed or missing, we respond in a deterministic way.
- Goal: keep the record credible even when failures happen.
- This policy is fixed for Phase 0 and must be followed exactly.

Definitions
- Publish time target: 21:12 JST / 12:12 UTC
- SLO window: ±5 minutes (operational) / delay_sec <= 120 (workflow log)
- Incident (SLO miss): any of the following:
  - daily record not published within 21:12 JST ±5 minutes
  - workflow logs show SLO=VIOLATION
  - dashboard record date not updated for the day

Detection (single source of truth)
- Check: btcsignal_log_live.json entries[-1].date == today_jst
- Check: workflow log line contains "publish_gate ... delay_sec=... SLO=..."

Response Rules (no improvisation)

1) Same-day response (must do before end of day JST)
- Create an incident entry in ops log (or incident section) with:
  - incident_id: YYYY-MM-DD
  - detected_at_jst:
  - impact: delayed / missing
  - scope: dashboard / logs / email waitlist only
  - suspected_cause: unknown / upstream / infra / workflow
  - next_update_eta_jst:
- Keep X posting rule unchanged:
  - Post the daily "yesterday confirmed record" as usual.
  - If WAIT_FALLBACK occurs, post it as a record (do not hide).

2) 24-hour rule (if not recovered)
- If the daily record is not recovered within 24 hours:
  - Post a one-line Ops note on X:
    "Ops note: today’s dashboard record is delayed. We will publish as soon as verification completes. (Record-only, not advice.)"

3) 72-hour rule (after recovery)
- Within 72 hours after recovery:
  - Publish an incident summary (short) including:
    - root cause (if known)
    - what was affected
    - fix / mitigation
    - prevention (action item)
    - related commit(s) / run_id(s)

Auditability
- This file must be version-controlled (Git).
- Any change to this policy requires:
  - a commit message explaining why
  - an entry in tasks/lessons.md

Do NOT
- Do not backfill silently without noting an incident.
- Do not publish "buy/sell" language or performance claims during incidents.
- Do not create paid/free timing differences for "today’s state".
