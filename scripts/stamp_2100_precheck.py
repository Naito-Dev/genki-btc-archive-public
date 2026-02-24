#!/usr/bin/env python3
"""21:00 JST stamp system precheck (no trading).

Outputs a compact JSON to stdout (no secrets):
{
  "ok": bool,
  "jst_date": "YYYY-MM-DD",
  "jst_time": "HH:MM",
  "run_id": "...",
  "checks": {
    "api": {"pass": bool, "detail": "..."},
    "env": {"pass": bool, "detail": "..."},
    "dry_run": {"pass": bool, "rc": int, "detail": "..."},
    "audit": {"pass": bool, "detail": "...", "sync_status": "...", "error_type": "..."}
  }
}

Notes:
- Uses Bitget public time endpoint for connectivity check.
- Runs execute_live_trade.py in DRY_RUN with EXEC_SKIP_LOCK=1 to avoid consuming daily lock.
- Uses FORCE_SYNC dry-run path so execution_sync_audit_YYYY-MM.ndjson emits START + terminal.
- Never prints secrets.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "output"
SECRETS_ENV_PRIMARY = ROOT / "secrets/.env.bitget_trade"
SECRETS_ENV_FALLBACK = ROOT / "secrets/.env.live"
EXEC = ROOT / "scripts/execute_live_trade.py"
PY = ROOT / ".venv/bin/python"


def now_jst() -> datetime:
    return datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=9)))


def jst_date_time() -> tuple[str, str]:
    n = now_jst()
    return n.strftime("%Y-%m-%d"), n.strftime("%H:%M")


def _short(s: str, max_len: int = 180) -> str:
    s = (s or "").strip().replace("\n", " ")
    return s if len(s) <= max_len else (s[: max_len - 3] + "...")


def check_api() -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            ["curl", "-I", "--max-time", "10", "https://api.bitget.com/api/v2/public/time"],
            capture_output=True,
            text=True,
            check=False,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        ok = proc.returncode == 0 and re.search(r"^HTTP/\S+\s+2\d\d", out, flags=re.MULTILINE)
        return (bool(ok), f"rc={proc.returncode} {'HTTP_2XX' if ok else 'NO_HTTP_2XX'}")
    except Exception as e:
        return (False, f"exception:{type(e).__name__}")


def check_env() -> tuple[bool, str]:
    # Only validate presence of keys; never print values.
    env_path = SECRETS_ENV_PRIMARY if SECRETS_ENV_PRIMARY.exists() else SECRETS_ENV_FALLBACK
    if not env_path.exists():
        return (False, "missing secrets env file")
    txt = env_path.read_text(encoding="utf-8", errors="ignore")
    if "BITGET_API_KEY=" not in txt or "BITGET_API_SECRET=" not in txt:
        return (False, "missing required keys")
    has_passphrase = ("BITGET_PASSPHRASE=" in txt) or ("BITGET_API_PASSPHRASE=" in txt)
    if not has_passphrase:
        return (False, "missing required keys")
    return (True, f"keys_present:{env_path.name}")


@dataclass
class DryRunResult:
    ok: bool
    rc: int
    run_id: str
    detail: str


def run_dry_run() -> DryRunResult:
    env = os.environ.copy()
    env.update(
        {
            "EXEC_SKIP_LOCK": "1",
            "DRY_RUN": "1",
            "TRADING_ENABLED": "1",
            "ARMED": "YES",
            "BALANCE_MAX_AGE_MIN": "100000",
            # Precheck requires execution_sync_audit verification; trigger force-sync in DRY_RUN only.
            "FORCE_SYNC": "1",
            "FORCE_SYNC_TARGET_RATIO": "0",
        }
    )
    proc = subprocess.run([str(PY), str(EXEC)], capture_output=True, text=True, env=env)
    combined = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()

    # Extract run_id from the single-line status, if present.
    m = re.search(r"\brun_id=([^\s]+)", combined)
    run_id = m.group(1) if m else ""

    ok = proc.returncode == 0 and bool(run_id)
    detail = _short(combined.splitlines()[-1] if combined else f"rc={proc.returncode}")
    return DryRunResult(ok=ok, rc=int(proc.returncode), run_id=run_id, detail=detail)


def audit_path_for_run() -> Path:
    ym = datetime.now(timezone.utc).strftime("%Y-%m")
    return OUT_DIR / f"execution_sync_audit_{ym}.ndjson"


def check_audit(run_id: str) -> tuple[bool, str, str, str]:
    # Returns: pass, detail, sync_status, error_type
    p = audit_path_for_run()
    if not p.exists():
        return (False, "missing audit file", "", "")

    start = False
    terminal = None
    sync_status = ""
    error_type = ""

    try:
        for raw in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            if not raw.strip():
                continue
            try:
                j = json.loads(raw)
            except Exception:
                continue
            rid = str(j.get("run_id") or "")
            # force-sync audit uses run_id with suffix: <run_id>-sync-<uuid>
            if rid != run_id and not rid.startswith(run_id + "-sync-"):
                continue
            st = str(j.get("sync_status") or "")
            if st == "START":
                start = True
            if st and st != "START":
                terminal = j
                sync_status = st
                error_type = str(j.get("error_type") or "")
        if not start or terminal is None:
            return (False, "missing START or terminal", sync_status, error_type)
        return (True, "START+terminal_ok", sync_status, error_type)
    except Exception as e:
        return (False, f"read_error:{type(e).__name__}", sync_status, error_type)


def main() -> int:
    jst_d, jst_t = jst_date_time()

    api_ok, api_detail = check_api()
    env_ok, env_detail = check_env()

    dry = run_dry_run()
    audit_ok, audit_detail, sync_status, error_type = (False, "skipped", "", "")
    if dry.ok:
        audit_ok, audit_detail, sync_status, error_type = check_audit(dry.run_id)

    ok = bool(api_ok and env_ok and dry.ok and audit_ok)

    out = {
        "ok": ok,
        "jst_date": jst_d,
        "jst_time": jst_t,
        "run_id": dry.run_id or "",
        "checks": {
            "api": {"pass": api_ok, "detail": api_detail},
            "env": {"pass": env_ok, "detail": env_detail},
            "dry_run": {"pass": dry.ok, "rc": dry.rc, "detail": dry.detail},
            "audit": {"pass": audit_ok, "detail": audit_detail, "sync_status": sync_status, "error_type": error_type},
        },
    }

    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
