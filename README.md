# Genki BTC Archive (Public)

This repository contains verified live execution logs.

Contents:
- log.json : Daily execution output (read-only archive)

No strategy code, no API keys, no infrastructure details are included.

This repository is a public performance archive only.

Hash-chain note:
- Each entry hash is derived from canonical entry JSON + `|` + `prev_hash` (or empty on first entry), using SHA-256.

Verify command (latest entry):
`python3 -c "import json,hashlib; j=json.load(open('log.json')); e=j['latest']; c={k:v for k,v in e.items() if k not in ('hash','prev_hash')}; s=json.dumps(c,ensure_ascii=False,sort_keys=True,separators=(',',':')); print(hashlib.sha256((s+'|'+(e.get('prev_hash') or '')).encode()).hexdigest()==e.get('hash'))"`

Schema lock (public log):
- Public log schema is strictly limited to: `date`, `timestamp_utc`, `allocation`, `btc_price`, `pnl_btc`, `position`, `regime`, `reason_summary`, `status`, `price_source`, `data_source`, `day`, `allocation_changed`, `notes`, `prev_hash`, `hash`, `updated_at_utc`, `logic_version`, `confidence_score`.
- No IDs, URLs, tokens, environment data, account data, order payloads, or raw exchange responses are ever stored.

## Daily Operation (must not stop)
1) Run daily log (normal)
- DRY_RUN=0 .venv/bin/python scripts/run_daily.py

2) Verify latest (local)
- public/log.json -> latest:
  btc_price, price_source, allocation, equity_usd, pnl_usd, position, hash, prev_hash

3) Publish (public repo)
- cp public/log.json ../genki-btc-archive-public/log.json
- git add log.json
- git commit -m "Daily log YYYY-MM-DD"
- git push

4) Verify public UI
- CHAIN INTEGRITY = VALID
- Equity / Daily PnL / Position visible

## Exception: same-day correction (rare)
- Only when a missing field or display fix is required.
- FORCE_TODAY=1 DRY_RUN=0 ... scripts/run_daily.py
- If chain validation fails, the script exits(2) and DOES NOT write log.json.
