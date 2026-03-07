#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "btcsignal_log.json"
CORE_LOG = ROOT / "data" / "log.json"
OUT = ROOT / "data" / "btcsignal_log_live.json"
LIVE_CONTRACT_START_DATE = "2026-02-26"
BOOTSTRAP_EXCEPTION_DATE = "2026-02-26"

EXCLUDE_REASON_PATTERNS = (
    "data_warmup_seed",
    "seed_source=csv",
)


def load_json(path: Path, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, obj) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")
    tmp.replace(path)


def to_public_state(raw_state: str) -> str:
    return "BTC" if str(raw_state).upper() == "HOLD" else "CASH"


def is_warmup(reason: str) -> bool:
    r = str(reason or "")
    return any(p in r for p in EXCLUDE_REASON_PATTERNS)


def is_valid_iso_utc_z(v: str) -> bool:
    s = str(v or "").strip()
    if not s or not s.endswith("Z"):
        return False
    try:
        datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return False
    return True


def parse_iso_utc_z(v: str) -> datetime | None:
    s = str(v or "").strip()
    if not s or not s.endswith("Z"):
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def classify_published_at_reason(record_date: str, published_at_utc: str) -> str:
    s = str(published_at_utc or "").strip()
    if not s:
        return f"published_at_missing:{record_date}"
    dt = parse_iso_utc_z(s)
    if dt is None:
        return f"published_at_invalid_format:{record_date}"
    ts_date = dt.date().isoformat()
    if ts_date < record_date:
        return f"published_at_invalid_old_date:{record_date}:{s}"
    if ts_date > record_date:
        return f"published_at_invalid_future_date:{record_date}:{s}"
    return ""


def parse_finite_number(v):
    if v is None:
        return None
    try:
        n = float(v)
    except Exception:
        return None
    return n if n == n and n not in (float("inf"), float("-inf")) else None


def build_core_published_map(core_log: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    rows = core_log.get("entries", []) if isinstance(core_log, dict) else []
    if not isinstance(rows, list):
        return out
    for e in rows:
        if not isinstance(e, dict):
            continue
        d = str(e.get("date") or "").strip()[:10]
        if not d:
            continue
        ts = str(e.get("published_at_utc") or "").strip()
        if is_valid_iso_utc_z(ts):
            out[d] = ts
    return out


def build_live_entries(entries: list[dict], core_published_map: dict[str, str]) -> tuple[list[dict], list[str]]:
    out: list[dict] = []
    exceptions: list[str] = []
    for e in entries:
        reason = str(e.get("reason") or "").strip()
        if is_warmup(reason):
            continue
        date = str(e.get("date") or "").strip()
        if not date:
            continue
        d10 = date[:10]
        published_at_utc = str(e.get("published_at_utc") or "").strip()
        if not is_valid_iso_utc_z(published_at_utc):
            published_at_utc = str(core_published_map.get(d10) or "").strip()
        published_reason = classify_published_at_reason(d10, published_at_utc)
        if d10 == BOOTSTRAP_EXCEPTION_DATE and published_reason == f"published_at_missing:{BOOTSTRAP_EXCEPTION_DATE}":
            exceptions.append(f"{BOOTSTRAP_EXCEPTION_DATE}: published_at_utc missing (bootstrap)")
        elif d10 >= LIVE_CONTRACT_START_DATE and published_reason:
            raise ValueError(published_reason)
        out.append(
            {
                "date": d10,
                "state": to_public_state(str(e.get("state") or "")),
                "reason": reason or "unavailable",
                "published_at_utc": published_at_utc,
                "btc_usd": parse_finite_number(e.get("close")),
            }
        )
    return out, exceptions


def main() -> int:
    src = load_json(SRC, {"entries": []})
    core = load_json(CORE_LOG, {"entries": []})
    entries = src.get("entries", []) if isinstance(src, dict) else []
    if not isinstance(entries, list):
        entries = []
    core_published_map = build_core_published_map(core)

    live_entries, contract_exceptions = build_live_entries(entries, core_published_map)
    live_start_date = live_entries[0]["date"] if live_entries else "unavailable"

    payload = {
        "meta": {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "source": "btcsignal_log.json",
            "note": "warmup seed rows are excluded from this live decision log",
            "excluded_reason_patterns": list(EXCLUDE_REASON_PATTERNS),
            "live_start_date": live_start_date,
            "live_contract_start_date": LIVE_CONTRACT_START_DATE,
            "contract_exceptions": contract_exceptions,
            "live_entries_count": len(live_entries),
        },
        "entries": live_entries,
    }
    save_json(OUT, payload)
    print(f"OK: wrote {OUT.name} entries={len(live_entries)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
