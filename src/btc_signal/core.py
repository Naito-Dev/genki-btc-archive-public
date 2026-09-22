"""Pure SMA100 rule and strict validation of immutable public artifacts."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path

RULE_ID = "SMA100_CLOSE_V1"
SOURCE = "coinbase_exchange"
PRODUCT = "BTC-USD"
PERIOD = 100
INTERVAL = 86400
UTC = timezone.utc


class SignalError(Exception):
    """An intentionally sanitized, machine-readable error."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        raise SignalError("naive_clock")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_utc(value: object) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise SignalError("invalid_utc_timestamp")
    try:
        result = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise SignalError("invalid_utc_timestamp") from exc
    return result


def target_day(now: datetime) -> date:
    if now.tzinfo is None:
        raise SignalError("naive_clock")
    return now.astimezone(UTC).date() - timedelta(days=1)


def decimal_value(value: object, *, volume: bool = False) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise SignalError("invalid_number")
    text = str(value)
    if len(text) > 80:
        raise SignalError("invalid_number")
    try:
        result = Decimal(text)
    except InvalidOperation as exc:
        raise SignalError("invalid_number") from exc
    if not result.is_finite() or result < 0 or (not volume and result == 0):
        raise SignalError("invalid_number")
    # Bound precision/exponents so exact decimal operations cannot exhaust resources.
    if len(result.as_tuple().digits) > 40 or abs(result.as_tuple().exponent) > 40:
        raise SignalError("invalid_number")
    return result


def decimal_text(value: Decimal) -> str:
    return format(value, "f")


def expected_dates(target: date) -> list[date]:
    return [target - timedelta(days=PERIOD - 1 - i) for i in range(PERIOD)]


def normalize_candles(raw: object, target: date, now: datetime) -> list[dict]:
    """Accept Coinbase's earlier extra buckets, never current/future buckets."""
    if target != target_day(now):
        raise SignalError("stale_target_date")
    if not isinstance(raw, list) or not raw or len(raw) > 300:
        raise SignalError("invalid_candle_response")
    first = target - timedelta(days=PERIOD - 1)
    seen: set[int] = set()
    selected: list[dict] = []
    for row in raw:
        if not isinstance(row, list) or len(row) != 6:
            raise SignalError("invalid_candle_shape")
        stamp = row[0]
        if isinstance(stamp, bool) or not isinstance(stamp, int) or stamp < 0 or stamp % INTERVAL:
            raise SignalError("invalid_candle_timestamp")
        if stamp in seen:
            raise SignalError("duplicate_candle")
        seen.add(stamp)
        try:
            start = datetime.fromtimestamp(stamp, UTC)
        except (ValueError, OverflowError, OSError) as exc:
            raise SignalError("invalid_candle_timestamp") from exc
        if start.date() > target or start + timedelta(seconds=INTERVAL) > now.astimezone(UTC):
            raise SignalError("unclosed_candle")
        low, high, opening, close = [decimal_value(x) for x in row[1:5]]
        volume = decimal_value(row[5], volume=True)
        if low > high or not low <= opening <= high or not low <= close <= high:
            raise SignalError("inconsistent_ohlc")
        if start.date() < first:
            continue
        selected.append({"date": start.date().isoformat(), "timestamp": stamp,
                         "open": decimal_text(opening), "high": decimal_text(high),
                         "low": decimal_text(low), "close": decimal_text(close),
                         "volume": decimal_text(volume)})
    selected.sort(key=lambda c: c["timestamp"])
    if [c["date"] for c in selected] != [d.isoformat() for d in expected_dates(target)]:
        raise SignalError("missing_or_stale_candles")
    return selected


def calculate(candles: list[dict]) -> dict:
    if len(candles) != PERIOD:
        raise SignalError("insufficient_candles")
    closes = [decimal_value(c["close"]) for c in candles]
    with localcontext() as ctx:
        ctx.prec = 200
        total = sum(closes, Decimal(0))
        average = total / Decimal(PERIOD)
        decision = "BTC" if closes[-1] * PERIOD > total else "CASH"
    return {"decision": decision, "close": decimal_text(closes[-1]),
            "sma100": decimal_text(average), "sum100": decimal_text(total)}


def read_json(path: Path) -> dict:
    try:
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise SignalError("duplicate_json_key")
                result[key] = value
            return result
        result = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique,
                            parse_constant=lambda _: (_ for _ in ()).throw(SignalError("invalid_number")))
    except (OSError, ValueError, UnicodeError) as exc:
        raise SignalError("invalid_artifact") from exc
    if not isinstance(result, dict):
        raise SignalError("invalid_artifact")
    return result


def validate_input(payload: dict, target: date) -> list[dict]:
    if set(payload) != {"schema_version", "source", "product", "interval_seconds", "target_date", "fetched_at_utc", "candles"}:
        raise SignalError("input_contract_mismatch")
    if (payload.get("schema_version") != "1.0" or payload.get("source") != SOURCE
            or payload.get("product") != PRODUCT or payload.get("interval_seconds") != INTERVAL
            or payload.get("target_date") != target.isoformat()):
        raise SignalError("input_contract_mismatch")
    fetched = parse_utc(payload.get("fetched_at_utc"))
    candles = payload.get("candles")
    if not isinstance(candles, list) or len(candles) != PERIOD:
        raise SignalError("insufficient_candles")
    try:
        raw = [[c["timestamp"], c["low"], c["high"], c["open"], c["close"], c["volume"]] for c in candles]
    except (KeyError, TypeError) as exc:
        raise SignalError("invalid_candle_shape") from exc
    normalized = normalize_candles(raw, target, fetched)
    if normalized != candles:
        raise SignalError("noncanonical_candles")
    return normalized


def load_verified_record(root: Path, path: Path | str) -> dict:
    path = Path(path)
    absolute = path if path.is_absolute() else Path(root) / path
    return validate_record(root, path, read_json(absolute))


def validate_record(root: Path, path: Path | str, record: dict) -> dict:
    root = Path(root).resolve()
    path = Path(path)
    path = path if path.is_absolute() else root / path
    if set(record) != {"schema_version", "record_id", "target_date", "rule_id", "source", "product",
                       "interval_seconds", "generated_at_utc", "implementation", "window", "input_ref",
                       "input_sha256", "decision", "close", "sma100", "sum100", "result_sha256"}:
        raise SignalError("record_contract_mismatch")
    try:
        target = date.fromisoformat(record["target_date"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SignalError("invalid_target_date") from exc
    expected_id = f"{RULE_ID}-{target.isoformat()}"
    if (record.get("schema_version") != "1.0" or record.get("rule_id") != RULE_ID
            or record.get("source") != SOURCE or record.get("product") != PRODUCT
            or record.get("record_id") != expected_id or record.get("interval_seconds") != INTERVAL):
        raise SignalError("record_contract_mismatch")
    if path.resolve() != root / "records" / RULE_ID / f"{target.isoformat()}.json":
        raise SignalError("record_path_mismatch")
    expected_ref = f"inputs/{RULE_ID}/{target.isoformat()}.json"
    if record.get("input_ref") != expected_ref:
        raise SignalError("input_path_mismatch")
    payload = dict(record)
    given_hash = payload.pop("result_sha256", None)
    if given_hash != digest(canonical(payload)):
        raise SignalError("record_hash_mismatch")
    input_path = root / expected_ref
    if input_path.resolve() != input_path:
        raise SignalError("input_path_mismatch")
    try:
        input_blob = input_path.read_bytes()
    except OSError as exc:
        raise SignalError("input_missing") from exc
    if record.get("input_sha256") != digest(input_blob):
        raise SignalError("input_hash_mismatch")
    source_input = read_json(input_path)
    candles = validate_input(source_input, target)
    if record.get("window") != {"start_date": expected_dates(target)[0].isoformat(), "end_date": target.isoformat(), "count": PERIOD}:
        raise SignalError("window_mismatch")
    generated = parse_utc(record.get("generated_at_utc"))
    if target_day(generated) != target or generated < parse_utc(source_input.get("fetched_at_utc")):
        raise SignalError("record_time_mismatch")
    implementation = record.get("implementation")
    if (not isinstance(implementation, dict) or set(implementation) != {"git_commit", "files_sha256"}
            or not re.fullmatch(r"[0-9a-f]{40}", str(implementation.get("git_commit", "")))):
        raise SignalError("implementation_missing")
    hashes = implementation.get("files_sha256")
    expected_files = {"src/btc_signal/core.py", "src/btc_signal/daily.py", "src/btc_signal/storage.py"}
    if (not isinstance(hashes, dict) or set(hashes) != expected_files
            or not all(isinstance(v, str) and re.fullmatch(r"[0-9a-f]{64}", v) for v in hashes.values())):
        raise SignalError("implementation_missing")
    # Consumption verifies the frozen artifact rather than calculating a new signal.
    if record.get("decision") not in {"BTC", "CASH"}:
        raise SignalError("invalid_decision")
    for key in ("close", "sma100", "sum100"):
        if not isinstance(record.get(key), str):
            raise SignalError("invalid_number")
        decimal_value(record[key])
    if record["close"] != candles[-1]["close"]:
        raise SignalError("close_mismatch")
    return record


def presentation(record: dict) -> dict:
    """One allowlisted projection for both site and Discord; no market fetch."""
    names = ("record_id", "target_date", "rule_id", "decision", "close", "sma100",
             "source", "product", "generated_at_utc", "result_sha256", "input_ref")
    return {name: record[name] for name in names}


def audit_record_calculation(root: Path, path: Path | str) -> dict:
    """Explicit offline audit only: compare the saved result with its saved inputs.

    Routine reruns, publication and delivery never call this function.
    """
    record = load_verified_record(root, path)
    source_input = read_json(Path(root) / record["input_ref"])
    expected = calculate(source_input["candles"])
    if any(record[key] != value for key, value in expected.items()):
        raise SignalError("calculation_mismatch")
    return record
