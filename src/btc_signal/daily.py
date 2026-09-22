"""One daily fetch, validation, computation and immutable commit to disk."""
from __future__ import annotations

import json
import re
import subprocess
import time
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from urllib import error, parse, request

from .core import (INTERVAL, PERIOD, PRODUCT, RULE_ID, SOURCE, UTC, SignalError,
                   calculate, canonical, digest, expected_dates, iso_utc,
                   load_verified_record, normalize_candles, read_json, target_day,
                   validate_input, validate_record)
from .storage import atomic_write, lock

ENDPOINT = "https://api.exchange.coinbase.com/products/BTC-USD/candles"


def fetch_candles(target, *, opener=request.urlopen, sleeper=time.sleep):
    start = expected_dates(target)[0].isoformat() + "T00:00:00Z"
    # End inside the completed final bucket, excluding today's open bucket.
    end = target.isoformat() + "T23:59:59Z"
    url = ENDPOINT + "?" + parse.urlencode({"granularity": INTERVAL, "start": start, "end": end})
    for attempt in range(3):
        try:
            req = request.Request(url, headers={"Accept": "application/json", "User-Agent": "BTC-SIGNAL-Archive/1.0"})
            with opener(req, timeout=15) as response:
                raw = response.read(1_000_001)
            if len(raw) > 1_000_000:
                raise SignalError("market_response_too_large")
            return json.loads(raw, parse_float=Decimal)
        except error.HTTPError as exc:
            retryable = exc.code == 429 or 500 <= exc.code <= 599
            if not retryable or attempt == 2:
                raise SignalError(f"market_http_{exc.code}") from None
        except (error.URLError, TimeoutError, OSError):
            if attempt == 2:
                raise SignalError("market_unavailable") from None
        except (ValueError, UnicodeError):
            raise SignalError("invalid_market_json") from None
        sleeper(attempt + 1)
    raise SignalError("market_unavailable")


def implementation_identity() -> dict:
    root = Path(__file__).resolve().parents[2]
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True,
                                capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise SignalError("implementation_unavailable") from exc
    paths = ["src/btc_signal/core.py", "src/btc_signal/daily.py", "src/btc_signal/storage.py"]
    return {"git_commit": commit, "files_sha256": {p: digest((root / p).read_bytes()) for p in paths}}


def write_status(root: Path, event: dict) -> None:
    # Event identities are unique even when an operator reuses a workflow run id.
    event_id = event["run_id"] + "-" + uuid.uuid4().hex[:12]
    event = dict(event, event_id=event_id)
    atomic_write(root / "status" / "events" / f"{event_id}.json", event, immutable=True)
    atomic_write(root / "status" / "latest.json", event)


def record_daily(root: Path, *, now=None, fetcher=fetch_candles,
                 implementation=None, run_id: str | None = None) -> tuple[dict, bool]:
    root = Path(root).resolve()
    clock = now or (lambda: datetime.now(UTC))
    started = clock()
    target = target_day(started)
    run_id = run_id or uuid.uuid4().hex
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", run_id):
        raise SignalError("invalid_run_id")
    record_ref = f"records/{RULE_ID}/{target.isoformat()}.json"
    input_ref = f"inputs/{RULE_ID}/{target.isoformat()}.json"
    event = {"schema_version": "1.0", "run_id": run_id, "target_date": target.isoformat(),
             "started_at_utc": iso_utc(started)}
    with lock(root, "daily"):
        try:
            if (root / record_ref).exists():
                result = load_verified_record(root, record_ref)
                created = False
            else:
                # Recover an interrupted input save without refetching different inputs.
                if (root / input_ref).exists():
                    source_input = read_json(root / input_ref)
                    candles = validate_input(source_input, target)
                else:
                    raw = fetcher(target)
                    fetched = clock()
                    if target_day(fetched) != target:
                        raise SignalError("utc_day_changed")
                    candles = normalize_candles(raw, target, fetched)
                    source_input = {"schema_version": "1.0", "source": SOURCE, "product": PRODUCT,
                                    "interval_seconds": INTERVAL, "target_date": target.isoformat(),
                                    "fetched_at_utc": iso_utc(fetched), "candles": candles}
                    atomic_write(root / input_ref, source_input, immutable=True)
                generated = clock()
                if target_day(generated) != target:
                    raise SignalError("utc_day_changed")
                result = {"schema_version": "1.0", "record_id": f"{RULE_ID}-{target.isoformat()}",
                          "target_date": target.isoformat(), "rule_id": RULE_ID, "source": SOURCE,
                          "product": PRODUCT, "interval_seconds": INTERVAL,
                          "generated_at_utc": iso_utc(generated),
                          "implementation": implementation or implementation_identity(),
                          "window": {"start_date": expected_dates(target)[0].isoformat(),
                                     "end_date": target.isoformat(), "count": PERIOD},
                          "input_ref": input_ref, "input_sha256": digest((root / input_ref).read_bytes()),
                          **calculate(candles)}
                result["result_sha256"] = digest(canonical(result))
                validate_record(root, record_ref, result)
                atomic_write(root / record_ref, result, immutable=True)
                result = load_verified_record(root, record_ref)
                created = True
            write_status(root, {**event, "state": "recorded", "label": "記録済み",
                                "finished_at_utc": iso_utc(clock()), "record_id": result["record_id"],
                                "result_file": record_ref, "result_sha256": result["result_sha256"],
                                "created": created})
            return result, created
        except Exception as exc:
            code = exc.code if isinstance(exc, SignalError) else "internal_error"
            write_status(root, {**event, "state": "failed", "label": "判定不能",
                                "finished_at_utc": iso_utc(clock()), "error_code": code})
            raise SignalError(code) from None
