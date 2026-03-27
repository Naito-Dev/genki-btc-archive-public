#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.append(str(SCRIPT_DIR))

import run_daily  # type: ignore

ROOT = Path(__file__).resolve().parent.parent
LOG_JSON = ROOT / 'data' / 'log.json'
DEFAULT_OUT = ROOT / '.runtime' / 'private_signal_result.json'


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description='Build current private BTCSIGNAL result from current-day daily state')
    p.add_argument('--log-json', default=str(LOG_JSON))
    p.add_argument('--out', default=str(DEFAULT_OUT))
    p.add_argument('--logic-version', default='current')
    return p.parse_args()


def now_utc_z() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def sha256_bytes(blob: bytes) -> str:
    return 'sha256:' + hashlib.sha256(blob).hexdigest()


def parse_optional_float(value: object) -> float | None:
    try:
        num = float(value)
    except Exception:
        return None
    if not math.isfinite(num):
        return None
    return num


def require(cond: bool, msg: str) -> None:
    if not cond:
        raise ValueError(msg)


def resolve_public_state(state: object) -> str:
    target_btc_ratio = parse_optional_float(getattr(state, 'target_btc_ratio', None))
    if target_btc_ratio is not None:
        return 'CASH' if target_btc_ratio <= 0.0 else 'BTC'

    allocation = parse_optional_float(getattr(state, 'allocation', None))
    if allocation is not None:
        return 'CASH' if allocation <= 0.0 else 'BTC'

    raise ValueError('invalid_daily_state:state_missing')


def resolve_verified_btc_usd(state: object, expected_date: str) -> float:
    record_date = str(getattr(state, 'date_utc', '') or '').strip()[:10]
    require(record_date == expected_date, 'stale_record_date')

    price_source = str(getattr(state, 'price_source', '') or '').strip().lower()
    require(
        price_source == 'live' or price_source.startswith('consensus:'),
        f'unverified_price_source:{price_source or "missing"}',
    )

    price_ts = str(getattr(state, 'price_ts', '') or '').strip()
    require(price_ts[:10] == expected_date, 'stale_price_ts')

    price = parse_optional_float(getattr(state, 'btc_price_ref', None))
    require(price is not None, 'missing_btc_price')
    return round(price, 2)


def main() -> int:
    args = parse_args()
    log_json_path = Path(args.log_json)
    out_path = Path(args.out)

    log = run_daily.load_log()
    start_date = str(log.get('start_date_utc') or '2026-02-17')
    state = run_daily.build_daily_state(start_date)

    record_date = str(getattr(state, 'date_utc', '') or '').strip()[:10]
    today_utc = datetime.now(timezone.utc).date().isoformat()
    require(record_date == today_utc, 'stale_record_date')

    result = {
        'date': record_date,
        'record_date': record_date,
        'state': resolve_public_state(state),
        'reason_public': 'private_current_signal',
        'published_at_utc': now_utc_z(),
        'btc_usd': resolve_verified_btc_usd(state, record_date),
        'signal_source': 'private_btcsignal',
        'schema_version': '1.0',
        'proof': {
            'logic_version': str(args.logic_version),
            'input_hash': sha256_bytes(log_json_path.read_bytes()),
            'output_hash': '',
        },
        'ops': {
            'delay_sec': 0,
            'status': 'ok',
        },
    }

    canonical = json.dumps(result, ensure_ascii=False, sort_keys=True).encode('utf-8')
    result['proof']['output_hash'] = sha256_bytes(canonical)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))
    print(f'OK: wrote {out_path}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
