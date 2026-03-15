#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_IN = ROOT / '.runtime' / 'private_signal_result.json'
OUT_LOG = ROOT / 'data' / 'btcsignal_log.json'
OUT_LOG_ROOT = ROOT / 'btcsignal_log.json'


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description='Import private BTCSIGNAL daily result into public log format')
    p.add_argument('--in', dest='infile', default=str(DEFAULT_IN))
    p.add_argument('--out', dest='outfile', default=str(OUT_LOG))
    return p.parse_args()


def load_json(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def save_json(path: Path, obj) -> None:
    tmp = path.with_suffix(path.suffix + '.tmp')
    with tmp.open('w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write('\n')
    tmp.replace(path)


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


def valid_iso_utc_z(value: str) -> bool:
    s = str(value or '').strip()
    if not s or not s.endswith('Z'):
        return False
    try:
        datetime.fromisoformat(s.replace('Z', '+00:00'))
    except Exception:
        return False
    return True


def to_legacy_state(state: str) -> str:
    s = str(state or '').strip().upper()
    if s == 'BTC':
        return 'HOLD'
    if s == 'CASH':
        return 'CASH'
    raise ValueError(f'invalid_state:{s or "missing"}')


def validate_payload(payload: dict) -> dict:
    require(isinstance(payload, dict), 'invalid_payload:root_not_object')
    date = str(payload.get('date') or '').strip()[:10]
    require(len(date) == 10, 'invalid_payload:date')
    legacy_state = to_legacy_state(payload.get('state'))
    reason_public = str(payload.get('reason_public') or '').strip()
    require(reason_public != '', 'invalid_payload:reason_public')
    published_at_utc = str(payload.get('published_at_utc') or '').strip()
    require(valid_iso_utc_z(published_at_utc), 'invalid_payload:published_at_utc')
    btc_usd = payload.get('btc_usd')
    if btc_usd is None:
        close = None
    else:
        try:
            btc_usd = float(btc_usd)
        except Exception:
            raise ValueError('invalid_payload:btc_usd')
        close = round(btc_usd, 2)
    signal_source = str(payload.get('signal_source') or '').strip()
    require(signal_source == 'private_btcsignal', 'invalid_payload:signal_source')
    schema_version = str(payload.get('schema_version') or '').strip()
    require(schema_version == '1.0', 'invalid_payload:schema_version')
    proof = payload.get('proof')
    require(isinstance(proof, dict), 'invalid_payload:proof')
    logic_version = str(proof.get('logic_version') or '').strip()
    input_hash = str(proof.get('input_hash') or '').strip()
    output_hash = str(proof.get('output_hash') or '').strip()
    require(logic_version != '', 'invalid_payload:proof.logic_version')
    require(input_hash.startswith('sha256:'), 'invalid_payload:proof.input_hash')
    require(output_hash.startswith('sha256:'), 'invalid_payload:proof.output_hash')
    ops = payload.get('ops')
    require(isinstance(ops, dict), 'invalid_payload:ops')
    status = str(ops.get('status') or '').strip()
    require(status in {'ok', 'pipeline_failed', 'record_missing'}, 'invalid_payload:ops.status')
    delay_sec = ops.get('delay_sec')
    try:
        delay_sec = int(delay_sec)
    except Exception:
        raise ValueError('invalid_payload:ops.delay_sec')
    return {
        'date': date,
        'state': legacy_state,
        'close': close,
        'reason': reason_public,
        'published_at_utc': published_at_utc,
        'signal_source': signal_source,
        'schema_version': schema_version,
        'proof': {
            'logic_version': logic_version,
            'input_hash': input_hash,
            'output_hash': output_hash,
        },
        'ops': {
            'delay_sec': delay_sec,
            'status': status,
        },
    }


def upsert_entry(log: dict, entry: dict) -> dict:
    entries = log.get('entries')
    if not isinstance(entries, list):
        entries = []
    replaced = False
    for idx, item in enumerate(entries):
        if isinstance(item, dict) and str(item.get('date') or '').strip()[:10] == entry['date']:
            entries[idx] = entry
            replaced = True
            break
    if not replaced:
        entries.append(entry)
    entries.sort(key=lambda x: str((x or {}).get('date') or ''))
    log['entries'] = entries
    return log


def main() -> int:
    args = parse_args()
    src = Path(args.infile)
    dst = Path(args.outfile)
    dst_root = OUT_LOG_ROOT if dst == OUT_LOG else None
    if not src.exists():
        raise SystemExit(f'private_result_missing:{src}')
    payload = load_json(src)
    entry = validate_payload(payload)
    if dst.exists():
        log = load_json(dst)
        if not isinstance(log, dict):
            log = {'entries': []}
    else:
        log = {'entries': []}
    result = upsert_entry(log, entry)
    save_json(dst, result)
    if dst_root is not None:
        save_json(dst_root, result)
    print(json.dumps({'ok': True, 'date': entry['date'], 'state': entry['state'], 'out': str(dst), 'mirror': str(dst_root) if dst_root else ''}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
