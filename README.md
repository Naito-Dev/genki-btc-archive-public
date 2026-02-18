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
