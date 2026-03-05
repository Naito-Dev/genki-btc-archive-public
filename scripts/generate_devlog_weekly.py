#!/usr/bin/env python3
from __future__ import annotations

import argparse
import glob
import json
import re
import subprocess
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore


ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = ROOT / "logs"
VERIFY_DIR = ROOT / "verification"
SUBSTACK_DIR = ROOT / "substack"
DOCS_DIR = ROOT / "docs" / "devlog"
DEFAULT_REPO = "Naito-Dev/genki-btc-archive-public"


@dataclass
class WeeklyFacts:
    start: date
    end: date
    days_published: int
    max_delay_sec: int | None
    valid_days: int
    last30_status: str


@dataclass
class ChangeItem:
    pr: int | None
    title: str
    rank: int
    impact_en: str
    impact_ja: str
    merged_at: datetime


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate weekly Dev Log (facts-only)")
    p.add_argument("--repo", default=DEFAULT_REPO)
    p.add_argument("--out-doc", default="")
    p.add_argument("--out-discord", default="")
    p.add_argument("--out-x", default="")
    p.add_argument("--out-meta", default="")
    p.add_argument("--end-date", default="")
    return p.parse_args()


def jst_today() -> date:
    if ZoneInfo is None:
        return datetime.now(timezone.utc).date()
    return datetime.now(ZoneInfo("Asia/Tokyo")).date()


def parse_iso_datetime(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def extract_delay_sec(entry: dict) -> int | None:
    for key in ("delay_sec", "publish_delay_sec"):
        v = entry.get(key)
        if isinstance(v, (int, float)):
            return int(v)
        if isinstance(v, str) and v.strip().isdigit():
            return int(v.strip())
    return None


def compute_weekly_facts(start: date, end: date) -> WeeklyFacts:
    days = [start + timedelta(days=i) for i in range(7)]
    rows: list[dict] = []
    for d in days:
        p = LOGS_DIR / f"{d.isoformat()}.json"
        if not p.exists():
            continue
        try:
            rows.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            continue

    if not rows:
        raise RuntimeError("DEVLOG_FAIL:ops_numbers_unavailable:no_logs_for_window")

    days_published = len(rows)
    delays = [v for v in (extract_delay_sec(r) for r in rows) if isinstance(v, int)]
    max_delay_sec = max(delays) if delays else None
    valid_days = sum(1 for r in rows if str(r.get("chain_integrity", "")).upper() == "VALID")
    match = last30_match_status(start, end)
    return WeeklyFacts(start, end, days_published, max_delay_sec, valid_days, match)


def last30_match_status(start: date, end: date) -> str:
    paths = sorted(glob.glob(str(VERIFY_DIR / "last30_match_report_*.txt")))
    candidates: list[tuple[date, Path]] = []
    for raw in paths:
        p = Path(raw)
        m = re.search(r"last30_match_report_(\d{4}-\d{2}-\d{2})\.txt$", p.name)
        if not m:
            continue
        try:
            d = date.fromisoformat(m.group(1))
        except ValueError:
            continue
        if start <= d <= end:
            candidates.append((d, p))
    if not candidates:
        return "unavailable"
    candidates.sort(key=lambda x: x[0])
    text = candidates[-1][1].read_text(encoding="utf-8", errors="ignore")
    m = re.search(r"result\s*=\s*(PASS|FAIL)", text, flags=re.IGNORECASE)
    if not m:
        return "unavailable"
    return m.group(1).upper()


def run_git(*args: str) -> str:
    res = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True)
    return res.stdout


def collect_changes(start: date, end: date, repo: str) -> list[ChangeItem]:
    after = start.isoformat()
    before = (end + timedelta(days=1)).isoformat()
    raw = run_git(
        "log",
        "--first-parent",
        "--since",
        after,
        "--until",
        before,
        "--format=%H%x1f%cI%x1f%s",
        "main",
    )
    items: list[ChangeItem] = []
    seen_pr: set[int] = set()
    for line in raw.splitlines():
        if not line.strip():
            continue
        parts = line.split("\x1f")
        if len(parts) != 3:
            continue
        _sha, merged_at_raw, subject = parts
        pr_num = None
        m = re.search(r"\(#(\d+)\)", subject)
        if m:
            pr_num = int(m.group(1))
            if pr_num in seen_pr:
                continue
            seen_pr.add(pr_num)
        rank, impact_en, impact_ja = classify_change(subject)
        items.append(
            ChangeItem(
                pr=pr_num,
                title=subject.strip(),
                rank=rank,
                impact_en=impact_en,
                impact_ja=impact_ja,
                merged_at=parse_iso_datetime(merged_at_raw),
            )
        )

    items.sort(key=lambda x: (x.rank, -int(x.merged_at.timestamp())))
    top = items[:3]
    while len(top) < 3:
        top.append(
            ChangeItem(
                pr=None,
                title="No qualifying merged PR in this window",
                rank=9,
                impact_en="No user-facing impact in this category.",
                impact_ja="このカテゴリでユーザー影響のある変更はありません。",
                merged_at=datetime.now(timezone.utc),
            )
        )
    return top


def classify_change(title: str) -> tuple[int, str, str]:
    s = title.lower()
    if any(k in s for k in ("fail-closed", "auto-heal", "slo", "delay", "stale", "monitor", "heartbeat", "pipeline", "retry", "chain")):
        return 1, "Improves reliability and incident recovery.", "信頼性と障害復旧性を改善。"
    if any(k in s for k in ("timestamp", "label", "ui", "dashboard", "link", "url", "favicon", "published_at", "record date")):
        return 2, "Reduces user misunderstanding in public UI.", "公開UIでの誤解を減らす。"
    if any(k in s for k in ("compliance", "guardrail", "record-only", "separation", "advice", "policy")):
        return 3, "Strengthens compliance guardrails.", "コンプライアンスガードレールを強化。"
    return 8, "Operational maintenance update.", "運用保守の更新。"


def next_devlog_number() -> int:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    max_n = 0
    for p in DOCS_DIR.glob("*.md"):
        m = re.search(r"Dev Log #(\d+)", p.read_text(encoding="utf-8", errors="ignore"))
        if not m:
            continue
        max_n = max(max_n, int(m.group(1)))
    return max_n + 1


def weekly_public_url() -> str:
    p = SUBSTACK_DIR / "last_published_weekly.json"
    if not p.exists():
        return ""
    try:
        j = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return ""
    url = str(j.get("last_post_url") or "").strip()
    if url.startswith("https://"):
        return url
    return ""


def format_change_en(item: ChangeItem, repo: str) -> str:
    if item.pr is None:
        return f"{item.title} -> {item.impact_en}"
    return f"PR #{item.pr} -> {item.impact_en}"


def format_change_ja(item: ChangeItem, repo: str) -> str:
    if item.pr is None:
        return f"{item.title} -> {item.impact_ja}"
    return f"PR #{item.pr} -> {item.impact_ja}"


def build_discord_message(
    n: int,
    facts: WeeklyFacts,
    top3: list[ChangeItem],
    repo: str,
    weekly_url: str,
) -> str:
    end_str = facts.end.isoformat()
    lines = [
        "@Genki #btcsignal",
        "",
        f"Dev Log #{n:03d} — week ending {end_str}",
        "",
        "Ops (facts):",
        f"- Published: {facts.days_published}/7",
    ]
    if facts.max_delay_sec is None:
        lines.append("- Delay: unavailable")
    else:
        lines.append(f"- Max delay: {facts.max_delay_sec} sec (SLO ±5m)")
    lines.extend(
        [
            f"- Integrity: VALID {facts.valid_days}/7",
            f"- Last30 match: {facts.last30_status}",
            "",
            "EN (Top 3):",
            f"1) {format_change_en(top3[0], repo)}",
            f"2) {format_change_en(top3[1], repo)}",
            f"3) {format_change_en(top3[2], repo)}",
            "",
            "JA (Top 3):",
            f"1) {format_change_ja(top3[0], repo)}",
            f"2) {format_change_ja(top3[1], repo)}",
            f"3) {format_change_ja(top3[2], repo)}",
            "",
            "Links:",
            "- https://btcsignal.org",
        ]
    )
    if weekly_url:
        lines.append(f"- {weekly_url}")
    pr_urls = [f"https://github.com/{repo}/pull/{i.pr}" for i in top3 if i.pr is not None][:3]
    if pr_urls:
        lines.append("- PR: " + " ".join(pr_urls))
    return "\n".join(lines)


def build_x_text(facts: WeeklyFacts, top3: list[ChangeItem], repo: str) -> str:
    end_str = facts.end.isoformat()
    prs = [f"#{i.pr}" for i in top3 if i.pr is not None][:3]
    delay = f"{facts.max_delay_sec}s" if facts.max_delay_sec is not None else "unavailable"
    pr_part = " ".join(prs) if prs else "none"
    text = (
        f"Naito Dev Log (week ending {end_str})\n"
        f"Ops: {facts.days_published}/7, delay {delay}, VALID {facts.valid_days}/7, last30 {facts.last30_status}.\n"
        f"Merged PRs: {pr_part}\n"
        f"https://btcsignal.org"
    )
    if len(text) > 280:
        text = (
            f"Naito Dev Log {end_str}: Published {facts.days_published}/7, delay {delay}, "
            f"VALID {facts.valid_days}/7, last30 {facts.last30_status}. https://btcsignal.org"
        )
    if len(text) > 280:
        raise RuntimeError("DEVLOG_FAIL:x_text_overflow")
    return text


def write_text(path_str: str, text: str) -> None:
    if not path_str:
        return
    p = Path(path_str)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    end = date.fromisoformat(args.end_date) if args.end_date else jst_today()
    start = end - timedelta(days=6)
    doc_path = Path(args.out_doc) if args.out_doc else DOCS_DIR / f"{end.isoformat()}.md"

    if doc_path.exists():
        meta = {"status": "noop", "reason": "doc_exists", "doc_path": str(doc_path)}
        write_text(args.out_meta, json.dumps(meta, ensure_ascii=False))
        return 0

    facts = compute_weekly_facts(start, end)
    top3 = collect_changes(start, end, args.repo)
    devlog_num = next_devlog_number()
    weekly_url = weekly_public_url()

    message = build_discord_message(devlog_num, facts, top3, args.repo, weekly_url)
    x_text = build_x_text(facts, top3, args.repo)

    write_text(str(doc_path), message)
    write_text(args.out_discord, message)
    write_text(args.out_x, x_text)

    meta = {
        "status": "ok",
        "doc_path": str(doc_path),
        "week_ending": end.isoformat(),
        "devlog_number": devlog_num,
    }
    write_text(args.out_meta, json.dumps(meta, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        msg = f"DEVLOG_FAIL:{e}"
        print(msg)
        raise
