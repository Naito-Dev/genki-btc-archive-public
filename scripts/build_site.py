#!/usr/bin/env python3
"""Build a static, allowlisted view; never fetch markets or calculate decisions."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from html import escape
import importlib
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile

RULE = "SMA100_CLOSE_V1"
ARCHIVE_COMMIT = "f60194683fbf7c92ba50e6ddcd519aa38883c807"
ARCHIVE_URL = f"https://github.com/Naito-Dev/genki-btc-archive-public/tree/{ARCHIVE_COMMIT}"
NOTICE = "判定システムを点検・再構築しています。過去の一部記録には、計算失敗時の代入値が含まれることを確認しました。過去ログは原本を保存し、正常に計算された判定とは区別しています。現在、新しい判定の自動公開を停止しています。"
DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
MODES = {"maintenance": "判定システム点検中", "manual_only": "自動更新停止中・単発検証", "automatic": "日次記録"}


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    if path.is_symlink():
        raise ValueError(f"Symlink is not an allowed input: {path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


def text(value) -> str:
    return escape(str(value) if value is not None else "—", quote=True)


def clean_date(value) -> str:
    value = str(value or "")
    if not DATE.fullmatch(value):
        raise ValueError("Invalid record date")
    datetime.strptime(value, "%Y-%m-%d")
    return value


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def entries(path: Path) -> list[dict]:
    value = read_json(path, {})
    rows = value.get("entries", [])
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError(f"Invalid history collection: {path.name}")
    return rows


def detail_evidence(row: dict, source: str) -> dict:
    reason = str(row.get("trigger") or row.get("reason_summary") or "")
    if reason == "MINIMAL_MODE: regime unavailable" and row.get("data_source") == "unavailable":
        kind, label = "calculation_fallback", "計算失敗時の代入値"
    elif row.get("status") == "HISTORICAL_BACKFILL":
        kind, label = "historical_backfill", "後付け価格データ"
    elif row.get("data_source") == "csv":
        kind, label = "csv_unverified", "CSV計算記録・入力鮮度未確認"
    else:
        kind, label = "unverified", "生成根拠未確認"
    # Deliberate field allowlist: account balances, equity and profit never pass through.
    return {"date": clean_date(row.get("date")), "kind": kind, "label": label,
            "original_reason": reason, "original_position": str(row.get("position") or ""),
            "allocation": row.get("allocation") if isinstance(row.get("allocation"), (int, float)) else None,
            "data_source": str(row.get("data_source") or ""),
            "status": str(row.get("status") or ""), "source_path": source}


def build_history(root: Path) -> dict:
    detail_rows = entries(root / "data/log.json")
    details = defaultdict(list)
    originals = defaultdict(list)
    for row in detail_rows:
        day = clean_date(row.get("date"))
        details[day].append(detail_evidence(row, "data/log.json"))
        originals[day].append(row)
    daily_differences = []
    daily_count = 0
    for path in sorted((root / "logs").glob("*.json")):
        row = read_json(path)
        day = clean_date(row.get("date"))
        daily_count += 1
        if originals[day] and not any(row == original for original in originals[day]):
            daily_differences.append(day)
        details[day].append(detail_evidence(row, f"logs/{path.name}"))

    public_rows = entries(root / "btcsignal_log_live.json")
    rows = []
    public_dates = set()
    legacy_reasons = {"data_warmup", "v1_cash_normal", "v1_hold_normal", "crash_breaker_fired",
                      "probation_cash", "guard_reject_cash_to_hold"}
    for original in public_rows:
        day = clean_date(original.get("date"))
        public_dates.add(day)
        reason = str(original.get("reason") or "")
        if reason == "private_current_signal":
            kind, label = "private_unverified", "公開判定の生成根拠未確認"
        elif reason in legacy_reasons:
            kind, label = "legacy_model", "旧方式の計算履歴"
        else:
            kind, label = "unverified", "生成根拠未確認"
        rows.append({"date": day, "original_state": str(original.get("state") or ""),
                     "original_reason": reason, "published_at_utc": str(original.get("published_at_utc") or ""),
                     "source_path": "btcsignal_log_live.json", "kind": kind, "label": label,
                     "detail_evidence": details.get(day, []), "originals_differ": day in daily_differences})
    # Retain detailed-only dates explicitly; do not invent a public decision for them.
    for day in sorted(set(details) - public_dates):
        rows.append({"date": day, "original_state": None, "original_reason": None,
                     "published_at_utc": None, "source_path": None, "kind": "no_public_record",
                     "label": "公開記録なし・詳細原本のみ", "detail_evidence": details[day],
                     "originals_differ": day in daily_differences})
    rows.sort(key=lambda row: row["date"], reverse=True)
    return {"archive_commit": ARCHIVE_COMMIT, "archive_last_date": max(details.keys() | public_dates, default=None),
            "rows": rows, "detail_counts": dict(Counter(detail_evidence(e, "data/log.json")["kind"] for e in detail_rows)),
            "daily_file_count": daily_count, "original_difference_dates": sorted(set(daily_differences)),
            "public_duplicate_dates": sorted(day for day, n in Counter(e["date"] for e in public_rows).items() if n > 1)}


def page(title: str, body: str, prefix: str, built_at: str) -> str:
    return f'''<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'self'; img-src 'self'; connect-src 'none'; base-uri 'none'; form-action 'none'">
<meta name="description" content="BTCの判定結果と検証用の記録を保存する静的アーカイブ。売買は行いません。">
<title>{text(title)} — BTC SIGNAL</title><link rel="stylesheet" href="{prefix}assets/style.css"></head>
<body><a class="skip" href="#main">本文へ</a><header><div class="shell header-inner">
<a class="brand" href="{prefix}index.html">BTC SIGNAL</a><nav aria-label="メイン"><a href="{prefix}records/index.html">新方式の記録</a><a href="{prefix}history/index.html">過去ログ</a><a href="{prefix}history/notes.html">記録の区分</a></nav></div></header>
<main id="main" class="shell">{body}</main><footer><div class="shell"><p>判定結果を保存する記録システムです。売買・注文は行いません。</p>
<p>ページ生成時刻：{text(built_at)} UTC。表示は保存済みの記録です。</p></div></footer></body></html>'''


def save_page(out: Path, relative: str, title: str, body: str, built_at: str) -> None:
    path = out / relative
    prefix = "../" * (len(Path(relative).parts) - 1)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page(title, body, prefix, built_at), encoding="utf-8")


def history_table(rows: list[dict]) -> str:
    lines = ['<div class="table-wrap"><table><thead><tr><th scope="col">記録日</th><th scope="col">公開表示・元の理由</th><th scope="col">原本から分かること</th></tr></thead><tbody>']
    for row in rows:
        labels = [f'<span class="label">{text(row["label"])}</span>']
        evidence = row["detail_evidence"]
        kinds = {e["kind"] for e in evidence}
        if "calculation_fallback" in kinds:
            labels.append('<p class="audit-note small">同日の詳細記録に計算失敗時の代入値があります。公開結果と同じ実行だったことは確認できません。</p>')
        elif "historical_backfill" in kinds:
            labels.append('<p class="small muted">同日の詳細データは後付け価格履歴です。当日に公開された判定を意味しません。</p>')
        elif "csv_unverified" in kinds:
            labels.append('<p class="small muted">CSV計算経路の記録があります。入力の鮮度は未確認です。</p>')
        if row["originals_differ"]:
            labels.append('<p class="small muted">集約と日次の原本に内容差があります。原本を統合・上書きしていません。</p>')
        if evidence:
            labels.append('<details><summary>詳細記録の理由を見る</summary>')
            for e in evidence:
                labels.append(f'<p><code>{text(e["source_path"])}</code><br>{text(e["label"])}：{text(e["original_reason"])}<br>記録上のposition：{text(e["original_position"])} / 配分：{text(e["allocation"])} / 状態：{text(e["status"])}</p>')
            labels.append('</details>')
        lines.append(f'<tr><td>{text(row["date"])}</td><td>{text(row["original_state"] or "公開記録なし")}<span class="reason">{text(row["original_reason"] or "—")}</span></td><td>{"".join(labels)}</td></tr>')
    return "".join(lines) + '</tbody></table></div>'


def build_history_pages(out: Path, history: dict, built_at: str) -> None:
    years = sorted({row["date"][:4] for row in history["rows"]}, reverse=True)
    intro = '<p class="lead">原本に残った日付・公開状態・理由を、そのまま確認するための旧方式の履歴です。新方式の判定とは分けて表示しています。</p><p class="small muted">CASHという表示だけでは計算の成功・失敗を判断できません。過去の数値で再計算して原本を置き換えてはいません。</p>'
    if not years:
        save_page(out, "history/index.html", "過去ログ", '<h1>過去ログ</h1>' + intro + '<p class="empty">表示できる過去ログはありません。</p>', built_at)
    for year in years:
        nav = '<nav class="year-nav" aria-label="記録年">' + ''.join(
            f'<a href="{y}.html"' + (' aria-current="page"' if y == year else '') + f'>{y}</a>'
            for y in years) + '</nav>'
        body = '<div class="eyebrow">旧方式の保存記録</div><h1>過去ログ</h1>' + intro + nav + history_table([row for row in history["rows"] if row["date"].startswith(year)])
        save_page(out, f"history/{year}.html", f"{year}年の過去ログ", body, built_at)
        if year == years[0]:
            save_page(out, "history/index.html", "過去ログ", body, built_at)
    counts = history["detail_counts"]
    notes = f'''<div class="eyebrow">履歴の読み方</div><h1>記録の区分</h1>
<p class="lead">旧原本を保存したうえで、当時の公開表示と、後から確認できた事実を分けて表示します。</p>
<dl class="facts"><dt>新方式の固定記録</dt><dd>入力・ルール・実装・結果のハッシュを確認した、新方式の保存済み判定です。</dd>
<dt>旧方式の計算履歴</dt><dd>旧モデルの理由を持つ記録です。再計算された履歴を含み、その日に公開されたことを保証しません。</dd>
<dt>生成根拠未確認</dt><dd>private_current_signalなど、公開元の計算理由が失われた記録です。CASHだけで計算失敗と扱いません。</dd>
<dt>計算失敗時の代入値</dt><dd>詳細原本にMINIMAL_MODE: regime unavailableと入力元unavailableが明記されています。公開記録とは別実行のため同一結果とは断定しません。</dd>
<dt>後付け価格データ</dt><dd>HISTORICAL_BACKFILLと明記された、入力のための過去データです。当日判定ではありません。</dd>
<dt>CSV計算記録</dt><dd>計算経路の理由が残っています。入力鮮度の保証がなく、新方式による正常記録とは区別します。</dd></dl>
<section class="section"><h2>詳細原本の内訳</h2><p>後付け価格データ {counts.get('historical_backfill', 0):,}件 / 計算失敗時の代入値 {counts.get('calculation_fallback', 0):,}件 / CSV計算記録 {counts.get('csv_unverified', 0):,}件。</p>
<p>集約と日次で内容差がある日：{text('、'.join(history['original_difference_dates']) or 'なし')}。違いを自動修正せず、それぞれの原本を維持しています。</p>
<p>旧版data/btcsignal_log_live.jsonは、ルートの公開ログとは別の原本です。公開ログの欠日や、詳細記録との状態の不一致も、過去の再計算で埋めていません。</p></section>
<section class="section"><h2>保存原本</h2><p>旧方式の原本は、記録日や判定を書き換えずGit履歴に保存しています。このサイトには口座残高・損益を含めない表示用データを掲載しています。</p>
<p><a href="{ARCHIVE_URL}">点検開始時の保存原本をGitHubで確認</a></p><p class="small muted">保存基準コミット：<code>{ARCHIVE_COMMIT}</code></p></section>'''
    save_page(out, "history/notes.html", "記録の区分", notes, built_at)


def verified_records(root: Path) -> list[tuple[dict, dict, Path]]:
    paths = sorted((root / "records" / RULE).glob("*.json"))
    if not paths:
        return []
    src_path = str(root / "src")
    if src_path not in sys.path:
        sys.path.insert(0, src_path)
    core = importlib.import_module("btc_signal.core")
    results = []
    for path in paths:
        record = core.load_verified_record(root, path)
        if record["rule_id"] != RULE or path.stem != clean_date(record["target_date"]):
            raise ValueError("Record path does not match its identity")
        view = core.presentation(record)
        results.append((record, view, path))
    return results


def record_facts(view: dict) -> str:
    items = [("記録日（UTC）", view["target_date"]), ("ルール", view["rule_id"]),
             ("対象市場", f'{view["source"]} / {view["product"]}'), ("確定終値（USD）", view["close"]),
             ("100日平均（USD）", view["sma100"]), ("記録ID", view["record_id"]),
             ("結果SHA-256", view["result_sha256"]), ("生成時刻（UTC）", view["generated_at_utc"])]
    return '<dl class="facts">' + ''.join(f'<dt>{text(k)}</dt><dd><code>{text(v)}</code></dd>' for k, v in items) + '</dl>'


def sanitize_status(status: dict) -> dict:
    allowed = ("state", "target_date", "record_id", "result_file", "result_sha256", "error_code", "label")
    clean = {k: status[k] for k in allowed if k in status and isinstance(status[k], (str, int, bool))}
    if clean.get("state") == "failed":
        clean["label"] = "判定不能"
        # A failed attempt must not claim a result identity.
        for k in ("record_id", "result_file", "result_sha256"):
            clean.pop(k, None)
    return clean


def delivery_for(root: Path, record: dict) -> dict:
    receipt = read_json(root / "delivery" / f'{record["record_id"]}.json', {})
    if not receipt:
        return {}
    if receipt.get("record_id") != record["record_id"] or receipt.get("result_sha256") != record["result_sha256"]:
        raise ValueError("Delivery receipt does not match the fixed record")
    if receipt.get("notification_state") not in {"pending", "sent", "failed", "uncertain"}:
        raise ValueError("Unknown notification state")
    allowed = ("schema_version", "record_id", "result_sha256", "target_date", "published_at_utc",
               "notification_state", "attempted_at_utc", "send_started_at_utc", "sent_at_utc",
               "finished_at_utc", "error_code")
    return {k: receipt[k] for k in allowed if k in receipt and isinstance(receipt[k], str)}


def delivery_label(receipt: dict) -> str:
    labels = {"pending": "送信待機", "sent": "送信済み", "failed": "送信失敗", "uncertain": "送信結果未確定"}
    state = receipt.get("notification_state")
    label = labels.get(state, "通知記録なし")
    note = " 判定の固定記録は保存されています。" if state in {"failed", "uncertain"} else ""
    return f'<p class="small muted">Discord通知：{text(label)}。{note}</p>'


def build(root: Path, output: Path, now: datetime | None = None) -> dict:
    root = root.resolve()
    if output.is_symlink():
        raise ValueError("Output may not be a symlink")
    output = output.resolve()
    if output == root or output in root.parents:
        raise ValueError("Output may not replace the repository or its parent")
    if output.exists() and any(output.iterdir()) and not (output / ".btc-signal-build").is_file():
        raise ValueError("Refusing to replace a non-generated directory")
    config = read_json(root / "config/system.json", {})
    mode = config.get("operation_mode", "maintenance")
    if mode not in MODES:
        raise ValueError("Unknown operation_mode")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    built_at = current.strftime("%Y-%m-%d %H:%M:%S")
    history = build_history(root)
    records = verified_records(root)
    status = sanitize_status(read_json(root / "status/latest.json", {}) or {})
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".static-build-", dir=output.parent) as temporary:
        out = Path(temporary) / "_site"
        (out / "assets").mkdir(parents=True)
        shutil.copyfile(root / "site/style.css", out / "assets/style.css")
        shutil.copyfile(root / "site/maintenance.html", out / "maintenance.html")
        (out / ".nojekyll").write_text("")
        (out / ".btc-signal-build").write_text("BTC SIGNAL static build v1\n")
        cname = root / "CNAME"
        if cname.exists():
            domain = cname.read_text().strip()
            if not re.fullmatch(r"[a-zA-Z0-9.-]+", domain):
                raise ValueError("Invalid CNAME")
            (out / "CNAME").write_text(domain + "\n")
        write_json(out / "data/history.json", history)
        write_json(out / "status/latest.json", status)
        write_json(out / "data/system.json", {"operation_mode": mode, "rule_id": RULE, "built_at_utc": current.isoformat()})
        build_history_pages(out, history, built_at)
        new_views = []
        deliveries = {}
        for record, view, path in records:
            day = view["target_date"]
            new_views.append(view)
            # These bytes were validated by the core loader. Do not reconstruct or recalculate them.
            destination = out / "records" / RULE / path.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, destination)
            input_ref = Path(record["input_ref"])
            if input_ref != Path("inputs") / RULE / f"{day}.json":
                raise ValueError("Unexpected public input path")
            input_destination = out / input_ref
            input_destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / input_ref, input_destination)
            receipt = delivery_for(root, record)
            deliveries[record["record_id"]] = receipt
            if receipt:
                write_json(out / "delivery" / f'{record["record_id"]}.json', receipt)
            body = f'<div class="eyebrow">新方式の固定記録</div><h1>{text(day)} の記録</h1><p class="decision">{text(view["decision"])}</p><p class="muted">保存済みの判定です。閲覧時に再計算は行いません。</p>' + record_facts(view)
            body += delivery_label(receipt)
            body += f'<div class="links"><a href="{day}.json">固定記録JSON</a><a href="../../{text(input_ref.as_posix())}">検証用入力JSON</a></div>'
            save_page(out, f"records/{RULE}/{day}.html", f"{day} の固定記録", body, built_at)
        write_json(out / "data/records.json", new_views)
        table = '<div class="table-wrap"><table><thead><tr><th>記録日（UTC）</th><th>判定</th><th>記録ID</th><th>結果SHA-256</th></tr></thead><tbody>'
        for view in reversed(new_views):
            table += f'<tr><td><a href="{RULE}/{text(view["target_date"])}.html">{text(view["target_date"])}</a></td><td>{text(view["decision"])}</td><td><code>{text(view["record_id"])}</code></td><td><code>{text(view["result_sha256"])}</code></td></tr>'
        table += '</tbody></table></div>'
        save_page(out, "records/index.html", "新方式の記録", '<div class="eyebrow">SMA100_CLOSE_V1</div><h1>新方式の記録</h1><p>検証済みの固定記録を、旧方式の履歴から分けて保存しています。</p>' + (table if new_views else '<p class="empty">新方式の記録はまだありません。</p>'), built_at)

        body = f'<div class="eyebrow">{MODES[mode]}</div><h1>判定結果の記録</h1>'
        if mode == "maintenance":
            body += f'<div class="notice maintenance"><p>{NOTICE}</p></div><p class="small muted">旧原本の最終記録日：{text(history["archive_last_date"])}。現在の判定ではありません。</p>'
        elif mode == "manual_only":
            body += '<p class="lead">自動更新を停止し、新方式を単発で検証しています。ここには検証を通過して保存した記録だけを掲載します。</p>'
        else:
            body += '<p class="lead">日次処理で確定した判定を保存しています。売買・注文は行いません。</p>'
        if mode != "maintenance" and status.get("state") == "failed":
            body += f'<div class="notice"><strong>判定不能</strong><p>{text(status.get("target_date"))} の処理は記録を確定できませんでした。新しい判定は掲載していません。<span class="small">理由：{text(status.get("error_code"))}</span></p></div>'
        if mode != "maintenance" and new_views:
            latest = new_views[-1]
            expected = (current.date() - timedelta(days=1)).isoformat()
            if latest["target_date"] < expected:
                body += f'<p class="notice">最後の正常な記録は {text(latest["target_date"])} です。最新の対象日まで更新されていません。</p>'
            body += f'<section class="section"><h2>最新の保存記録</h2><div class="latest"><div><p class="muted">{text(latest["target_date"])}（UTC）</p><p class="decision">{text(latest["decision"])}</p><p class="small">現在の売買指示ではありません。</p></div>{record_facts(latest)}</div><a href="records/{RULE}/{text(latest["target_date"])}.html">この固定記録を確認</a></section>'
            body += delivery_label(deliveries[latest["record_id"]])
        elif mode != "maintenance":
            body += '<p class="empty">新方式の判定記録はまだありません。</p>'
        body += '<section class="section"><h2>記録ルール</h2><p>Coinbase ExchangeのBTC-USD、UTC日足の確定終値を使います。直近100本の終値平均より対象日の終値が上ならBTC、同値以下ならCASHとして記録します。</p><p class="small muted">ルール：SMA100_CLOSE_V1。入力不足や取得・検証の失敗は「判定不能」として分離し、CASHで補いません。</p></section><section class="section"><h2>旧方式の過去ログ</h2><p>原本を維持し、計算失敗時の代入値、後付け履歴、生成根拠が未確認の記録を区別しています。</p><div class="links"><a href="history/index.html">過去ログを見る</a><a href="history/notes.html">記録の区分を確認</a></div></section>'
        save_page(out, "index.html", MODES[mode], body, built_at)
        files = sorted(str(path.relative_to(out)) for path in out.rglob("*") if path.is_file())
        write_json(out / "build-manifest.json", {"files": files, "operation_mode": mode, "record_count": len(records), "history_count": len(history["rows"])})
        if output.exists():
            shutil.rmtree(output)
        shutil.move(str(out), str(output))
    return {"operation_mode": mode, "record_count": len(records), "history_count": len(history["rows"]), "output": str(output)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.root, args.output or args.root / "_site"), ensure_ascii=False))


if __name__ == "__main__":
    main()
