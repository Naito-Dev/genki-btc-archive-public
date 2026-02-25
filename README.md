# Genki BTC Signal — Public Verification Archive

> A rules-based BTC allocation signal built for holders who value capital protection over speculation.

Japanese version: [README.ja.md](./README.ja.md)

---

## What This Repository Is

This is the public proof archive for Genki BTC Signal — a daily risk-control service for BTC spot holders.

Every day, the system evaluates market structure and outputs the current candidate allocation levels:

```text
0% / 70% / 100% (strict70_to0_rule_v1 candidate; 30% path not adopted)
```

This repository does not contain strategy code, API credentials, or infrastructure details.
What it does contain is a tamper-evident daily log — verifiable by anyone, at any time.

## Why This Exists

Most BTC signal services offer opinions. This one offers evidence.

The log published here creates a public, immutable record of allocation decisions made by the system — before outcomes are known. Each entry is cryptographically linked to the previous one via a SHA-256 hash chain, and the full commit history is preserved on GitHub.

This means:

- No retroactive edits
- No cherry-picked results
- No "we called it" without proof

If you are evaluating this service, this archive is the starting point for due diligence.

## Who This Service Is For

### BTC holders who are tired of making emotional decisions.
You already own BTC. The problem is not conviction — it is knowing when to hold full exposure, when to reduce, and when to step aside. This signal handles that judgment so you do not have to.

### Busy professionals who cannot watch charts all day.
A single daily signal. No all-day monitoring required. Act only when the allocation changes.

### Experienced holders who have been burned by hype.
You have seen influencers call tops and bottoms. You know the game. What you want is a system with rules, logs, and reproducibility — not vibes.

This service is not for short-term traders, altcoin rotators, or anyone expecting guaranteed returns.

## What's in This Repository

| File | Description |
|---|---|
| `log.json` | Daily execution log — read-only public archive |

## Log Entry Fields (Public Schema)

Core verification fields (expected for integrity checks):

- `date`
- `timestamp_utc`
- `allocation`
- `prev_hash`
- `hash`
- `updated_at_utc`

Operational/context fields (may vary by logic version):

- `btc_price`
- `pnl_btc`
- `position`
- `regime`
- `reason_summary`
- `status`
- `price_source`
- `data_source`
- `day`
- `allocation_changed`
- `notes`
- `logic_version`
- `confidence_score`

No account data, order payloads, API tokens, or environment variables are stored in this repository.

## Reference Performance Metrics

The following metrics are derived from historical backtesting and are provided for reference only. They are not guarantees of future performance.

| Metric | Value |
|---|---|
| Final Equity | 15.17x |
| CAGR | 41.02% |
| Max Drawdown | -19.79% |
| Avg Exposure | 11.80% |

Live forward testing is currently in progress with real capital. Results are published in this archive as records accumulate.

## How to Verify the Log

Anyone can confirm that published records have not been altered.

Step 1. Open `log.json` and inspect the `entries` array.
Step 2. Confirm each entry's `prev_hash` matches the `hash` of the preceding entry.
Step 3. Cross-reference `timestamp_utc` values with this repository's Git commit history.

One-line verification command (latest entry):

```bash
python3 -c "
import json, hashlib
j = json.load(open('log.json'))
e = j['latest']
c = {k: v for k, v in e.items() if k not in ('hash', 'prev_hash')}
s = json.dumps(c, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
print(hashlib.sha256((s + '|' + (e.get('prev_hash') or '')).encode()).hexdigest() == e.get('hash'))
"
```

Returns `True` if the latest entry hash is intact.

### Hash Construction

Each entry hash is computed as:

`SHA-256(canonical_entry_json + "|" + prev_hash)`

The first entry uses an empty string in place of `prev_hash`.

## Current Status

- Phase: Live verification in progress
- Capital deployed: Real funds, limited scale (verification phase)
- Paid access: Opens after verification period — join the waitlist
- Public dashboard: View live signal status
- Verification center: Technical Verification & Reproducibility Center

Verification artifacts are updated publicly as records accumulate.

## Strategy & Transparency

The allocation logic is proprietary. The evidence that it runs as described is public.

This separation is intentional: protecting the implementation does not require hiding the proof. Allocation decisions are recorded before market outcomes are resolved. The hash chain ensures that no entry can be modified without detection.

This is the standard we hold ourselves to. You can verify it.

Not financial advice. Past performance does not guarantee future results. Live outcomes can vary with fees, slippage, taxes, and market conditions. All allocation decisions remain the responsibility of the individual.
