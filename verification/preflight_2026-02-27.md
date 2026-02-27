# Preflight Checklist (2026-02-27)

## 1) Pine distribution
- TradingView script: Invite-only / Private: YES
- Public repo contains no .pine: YES (commit caa020d)

## 2) Constants parity (Python ↔ Pine)
- K_MA100: OK
- DROP_THRESHOLD: OK
- Q_ATR: OK
- N_CONSEC: OK
- COOLDOWN: OK
- MIN_HOLD: OK

## 3) Guard priority & crash handling
- Breaker > MIN_HOLD > COOLDOWN: YES (merged)
- PROBATION freeze while breaker active: YES
- Test: scripts/test_btcsignal_force_exit.py PASS

## 4) Audit proof
- last30 python-vs-tradingview match: PASS (commit ec278bc)
- public proof URL reachable: YES
