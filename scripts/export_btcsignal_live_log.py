#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "btcsignal_log.json"
OUT = ROOT / "btcsignal_log_live.json"

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


def build_live_entries(entries: list[dict]) -> list[dict]:
    out: list[dict] = []
    for e in entries:
        reason = str(e.get("reason") or "").strip()
        if is_warmup(reason):
            continue
        date = str(e.get("date") or "").strip()
        if not date:
            continue
        out.append(
            {
                "date": date[:10],
                "state": to_public_state(str(e.get("state") or "")),
                "reason": reason or "unavailable",
            }
        )
    return out


def main() -> int:
    src = load_json(SRC, {"entries": []})
    entries = src.get("entries", []) if isinstance(src, dict) else []
    if not isinstance(entries, list):
        entries = []

    live_entries = build_live_entries(entries)
    payload = {
        "meta": {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "source": "btcsignal_log.json",
            "note": "warmup seed rows are excluded from this live decision log",
            "excluded_reason_patterns": list(EXCLUDE_REASON_PATTERNS),
        },
        "entries": live_entries,
    }
    save_json(OUT, payload)
    print(f"OK: wrote {OUT.name} entries={len(live_entries)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

