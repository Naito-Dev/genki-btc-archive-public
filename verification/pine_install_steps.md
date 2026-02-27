# Pine Distribution Policy (Frozen)

## Policy
- TradingView script distribution is **Invite-only** or **Private** only.
- Pine source code is **not stored** in this public repository.
- This repository contains only audit procedures and proof artifacts.

## Delivery Flow
1. Maintain Pine source only in a private owner-managed location.
2. Publish to TradingView as Invite-only (or keep Private).
3. Grant access only to approved users.
4. Revoke access when needed from TradingView access control.

## Last30 Match Audit Procedure
Use this repository to verify parity between production Python state and TradingView state:

1. Export/prepare TradingView last 30 daily states as CSV (`date,state`) from the approved Invite-only script.
2. Run the local comparator:
   - `python3 /Users/Claw/rulu/tradingview/compare_last30_state.py`
3. Confirm audit output:
   - `compare_dates=30`
   - `mismatches=0`
   - `result=PASS`
4. Save report snapshot under `verification/` for evidence.

## Notes
- Public users should never receive Pine source from this repository.
- Any `.pine` file added here is a policy violation and must be removed immediately.
