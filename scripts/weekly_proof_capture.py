#!/usr/bin/env python3
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
PROOF_ROOT = ROOT / "proof"
VERIFICATION_WEEKLY = ROOT / "verification" / "weekly"
LATEST_TXT = VERIFICATION_WEEKLY / "latest.txt"
REPORT_GLOB = "last30_match_report_*.txt"
DEFAULT_CHECK_URL = "https://btcsignal.org/verification/last30_match_report_2026-02-27.txt"
JST = ZoneInfo("Asia/Tokyo")


def _iso_week_id(now_jst: datetime) -> str:
    y, w, _ = now_jst.isocalendar()
    return f"{y}-W{w:02d}"


def _http_status(url: str) -> tuple[int | None, str]:
    req = Request(url, method="GET", headers={"User-Agent": "CodexWeeklyProof/1.0"})
    try:
        with urlopen(req, timeout=20) as r:  # noqa: S310
            return int(getattr(r, "status", 200)), "OK"
    except HTTPError as e:
        return e.code, "FAIL"
    except URLError:
        return None, "FAIL"
    except Exception:
        return None, "FAIL"


def _latest_match_report() -> Path | None:
    candidates = sorted((ROOT / "verification").glob(REPORT_GLOB))
    return candidates[-1] if candidates else None


def _parse_match_metrics(report_path: Path | None) -> dict[str, str]:
    metrics = {
        "compare_dates": "unavailable",
        "matches": "unavailable",
        "mismatches": "unavailable",
        "result": "unavailable",
    }
    if not report_path or not report_path.exists():
        return metrics
    text = report_path.read_text(encoding="utf-8", errors="replace")
    for key in ("compare_dates", "matches", "mismatches", "result"):
        m = re.search(rf"^{key}=(.+)$", text, flags=re.MULTILINE)
        if m:
            metrics[key] = m.group(1).strip()
    return metrics


def main() -> int:
    now_utc = datetime.now(timezone.utc)
    now_jst = now_utc.astimezone(JST)
    week_id = _iso_week_id(now_jst)
    day_id = now_jst.strftime("%Y-%m-%d")
    ts_jst = now_jst.strftime("%Y-%m-%d %H:%M:%S %Z")
    ts_utc = now_utc.strftime("%Y-%m-%d %H:%M:%S UTC")

    check_url = DEFAULT_CHECK_URL
    status_code, url_result = _http_status(check_url)

    weekly_dir = PROOF_ROOT / week_id
    weekly_dir.mkdir(parents=True, exist_ok=True)
    VERIFICATION_WEEKLY.mkdir(parents=True, exist_ok=True)

    report_path = weekly_dir / f"weekly_proof_report_{day_id}.txt"
    report_lines = [
        f"week={week_id}",
        f"checked_url={check_url}",
        f"checked_at_jst={ts_jst}",
        f"checked_at_utc={ts_utc}",
        f"http_status={status_code if status_code is not None else 'unavailable'}",
        f"result={url_result}",
    ]
    report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")

    match_report = _latest_match_report()
    metrics = _parse_match_metrics(match_report)
    screenshot_status = "unavailable"
    screenshot_reason = "not_captured_by_script"

    summary_path = weekly_dir / "summary.md"
    summary = [
        f"# Weekly Proof Summary ({week_id})",
        "",
        f"- Week: `{week_id}`",
        f"- Proof URL: `https://btcsignal.org/proof/{week_id}/{report_path.name}`",
        f"- result: `{metrics['result']}`",
        f"- compare_dates: `{metrics['compare_dates']}`",
        f"- matches: `{metrics['matches']}`",
        f"- mismatches: `{metrics['mismatches']}`",
        f"- checked_at_jst: `{ts_jst}`",
        f"- checked_at_utc: `{ts_utc}`",
        f"- screenshot_status: `{screenshot_status}`",
        f"- screenshot_reason: `{screenshot_reason}`",
        "",
        f"- source_match_report: `{match_report.name if match_report else 'unavailable'}`",
    ]
    summary_path.write_text("\n".join(summary) + "\n", encoding="utf-8")

    latest_url = f"https://btcsignal.org/proof/{week_id}/{report_path.name}"
    LATEST_TXT.write_text(latest_url + "\n", encoding="utf-8")

    print(f"updated_report={report_path}")
    print(f"updated_summary={summary_path}")
    print(f"updated_latest={LATEST_TXT}")
    print(f"proof_url={latest_url}")
    print(f"url_check={url_result} status={status_code if status_code is not None else 'unavailable'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
