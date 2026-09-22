from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from html.parser import HTMLParser
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "src"))
import build_site
from btc_signal import core
from btc_signal.daily import record_daily


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


class StaticSiteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        shutil.copytree(REPO / "site", self.root / "site")
        self.out = self.root / "_site"
        self.now = datetime(2026, 9, 22, 1, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self.temp.cleanup()

    def put(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False) + "\n")
        return path

    def make_record(self, target=date(2026, 9, 21)):
        now = datetime.combine(target + timedelta(days=1), time(1), timezone.utc)
        raw = []
        for i in range(100):
            stamp = int(datetime.combine(target - timedelta(days=99-i), time(), timezone.utc).timestamp())
            close = 200 if i == 99 else 100
            raw.append([stamp, close, close, close, close, 10])
        return record_daily(self.root, now=lambda: now, fetcher=lambda _: raw,
                            implementation={"git_commit": "a" * 40,
                                            "files_sha256": {f"src/btc_signal/{name}.py": "b" * 64
                                                             for name in ("core", "daily", "storage")}})[0]

    def test_maintenance_is_default_exact_notice_and_no_current_decision(self):
        self.put("btcsignal_log_live.json", {"entries": [{"date": "2026-09-20", "state": "CASH", "reason": "private_current_signal"}]})
        result = build_site.build(self.root, self.out, self.now)
        html = (self.out / "index.html").read_text()
        self.assertEqual(result["operation_mode"], "maintenance")
        self.assertIn(build_site.NOTICE, html)
        self.assertNotIn('class="decision"', html)
        self.assertIn("2026-09-20", html)

    def test_history_labels_use_evidence_not_cash_and_keep_originals(self):
        public = self.put("btcsignal_log_live.json", {"entries": [
            {"date": "2020-01-01", "state": "CASH", "reason": "crash_breaker_fired"},
            {"date": "2026-09-20", "state": "BTC", "reason": "private_current_signal"}]})
        detail = self.put("data/log.json", {"entries": [
            {"date": "2020-01-01", "status": "HISTORICAL_BACKFILL", "data_source": "BINANCE_D1_BACKFILL", "reason_summary": "historical_backfill"},
            {"date": "2026-09-20", "allocation": 0, "position": "CASH", "data_source": "unavailable", "reason_summary": "MINIMAL_MODE: regime unavailable"}]})
        before = {p: p.read_bytes() for p in (public, detail)}
        history = build_site.build_history(self.root)
        bydate = {r["date"]: r for r in history["rows"]}
        self.assertEqual(bydate["2020-01-01"]["kind"], "legacy_model")
        self.assertEqual(bydate["2026-09-20"]["original_state"], "BTC")
        self.assertEqual(bydate["2026-09-20"]["kind"], "private_unverified")
        self.assertEqual(bydate["2026-09-20"]["detail_evidence"][0]["kind"], "calculation_fallback")
        build_site.build(self.root, self.out, self.now)
        for path, blob in before.items():
            self.assertEqual(path.read_bytes(), blob)

    def test_unknown_fields_and_account_data_are_not_published(self):
        secret = "DO_NOT_PUBLISH_ACCOUNT_SECRET"
        self.put("data/log.json", {"entries": [{"date": "2026-09-20", "position": "CASH", "reason_summary": "MINIMAL_MODE: regime unavailable", "data_source": "unavailable", "equity_usd": secret, "balance_source": secret, "portfolio_snapshot": {"token": secret}}]})
        self.put("status/latest.json", {"state": "failed", "target_date": "2026-09-21", "error_code": "market_unavailable", "webhook": secret})
        self.put("config/system.json", {"operation_mode": "manual_only", "discord_webhook": secret})
        self.put("output/private.json", {"password": secret})
        (self.root / ".env").write_text(secret)
        build_site.build(self.root, self.out, self.now)
        for path in self.out.rglob("*"):
            if path.is_file():
                self.assertNotIn(secret, path.read_text())
        self.assertFalse((self.out / "output").exists())
        self.assertFalse((self.out / ".env").exists())
        self.assertFalse((self.out / "data/log.json").exists())

    def test_public_record_and_input_identity_match_without_computation(self):
        self.put("config/system.json", {"operation_mode": "manual_only"})
        record = self.make_record()
        with patch("btc_signal.core.calculate", side_effect=AssertionError("No recomputation")), \
             patch("urllib.request.urlopen", side_effect=AssertionError("No external API")):
            build_site.build(self.root, self.out, self.now)
        views = json.loads((self.out / "data/records.json").read_text())
        self.assertEqual(views, [core.presentation(record)])
        self.assertEqual((self.out / record["input_ref"]).read_bytes(), (self.root / record["input_ref"]).read_bytes())
        rel = Path("records") / core.RULE_ID / (record["target_date"] + ".json")
        self.assertEqual((self.out / rel).read_bytes(), (self.root / rel).read_bytes())
        html = (self.out / "index.html").read_text()
        self.assertIn(record["record_id"], html)
        self.assertIn(record["result_sha256"], html)
        self.assertIn("自動更新停止中・単発検証", html)

    def test_failed_attempt_does_not_replace_last_good_with_cash(self):
        self.put("config/system.json", {"operation_mode": "manual_only"})
        record = self.make_record(date(2026, 9, 20))
        self.assertEqual(record["decision"], "BTC")
        self.put("status/latest.json", {"state": "failed", "target_date": "2026-09-21", "label": "CASH", "decision": "CASH", "error_code": "market_unavailable"})
        build_site.build(self.root, self.out, self.now)
        status = json.loads((self.out / "status/latest.json").read_text())
        self.assertEqual(status["label"], "判定不能")
        self.assertNotIn("decision", status)
        html = (self.out / "index.html").read_text()
        self.assertIn("判定不能", html)
        self.assertIn("最後の正常な記録は 2026-09-20", html)
        self.assertIn('<p class="decision">BTC</p>', html)

    def test_tampered_record_blocks_build_without_replacing_previous_site(self):
        record = self.make_record()
        build_site.build(self.root, self.out, self.now)
        original_site = (self.out / "index.html").read_bytes()
        rel = f'records/{core.RULE_ID}/{record["target_date"]}.json'
        record["decision"] = "CASH"
        self.put(rel, record)
        with self.assertRaises(core.SignalError):
            build_site.build(self.root, self.out, self.now)
        self.assertEqual((self.out / "index.html").read_bytes(), original_site)

    def test_delivery_failure_is_separate_and_private_receipt_fields_are_excluded(self):
        self.put("config/system.json", {"operation_mode": "manual_only"})
        record = self.make_record()
        self.put(f'delivery/{record["record_id"]}.json', {
            "schema_version": "1.0", "record_id": record["record_id"], "result_sha256": record["result_sha256"],
            "notification_state": "failed", "error_code": "discord_http_404", "message_id": "PRIVATE_MESSAGE_ID",
            "owner_workflow_run": "INTERNAL_WORKFLOW_ID", "attempts": [{"secret": "DO_NOT_PUBLISH"}]})
        build_site.build(self.root, self.out, self.now)
        self.assertIn("Discord通知：送信失敗", (self.out / "index.html").read_text())
        receipt = json.loads((self.out / "delivery" / f'{record["record_id"]}.json').read_text())
        self.assertEqual(receipt["result_sha256"], record["result_sha256"])
        self.assertNotIn("message_id", receipt)
        self.assertNotIn("attempts", receipt)
        self.assertNotIn("owner_workflow_run", receipt)
        self.assertEqual(json.loads((self.out / "data/records.json").read_text())[0]["decision"], record["decision"])

    def test_historical_text_is_escaped_and_site_has_no_executable_client(self):
        self.put("btcsignal_log_live.json", {"entries": [{"date": "2026-09-20", "state": "CASH", "reason": '<script src="https://example.invalid/track"></script>'}]})
        build_site.build(self.root, self.out, self.now)
        for path in self.out.rglob("*.html"):
            parsed = PageParser()
            parsed.feed(path.read_text())
            for tag, attrs in parsed.tags:
                self.assertNotIn(tag, {"script", "iframe", "form"})
                self.assertFalse(any(k.startswith("on") for k in attrs))
                for key in ("src", "action"):
                    self.assertFalse(attrs.get(key, "").startswith(("http:", "https:", "//")))
                if tag == "link":
                    self.assertFalse(attrs.get("href", "").startswith(("http:", "https:", "//")))
        self.assertIn("&lt;script", (self.out / "history/index.html").read_text())

    def test_unknown_output_directory_is_not_deleted(self):
        self.out.mkdir()
        original = self.out / "unrelated.txt"
        original.write_text("keep")
        with self.assertRaises(ValueError):
            build_site.build(self.root, self.out, self.now)
        self.assertEqual(original.read_text(), "keep")

    def test_every_local_html_link_resolves_and_raw_account_files_are_absent(self):
        self.make_record()
        self.put("btcsignal_log_live.json", {"entries": [{"date": "2026-09-20", "state": "CASH", "reason": "private_current_signal"}]})
        build_site.build(self.root, self.out, self.now)
        for path in self.out.rglob("*.html"):
            parsed = PageParser()
            parsed.feed(path.read_text())
            for tag, attrs in parsed.tags:
                href = attrs.get("href", "")
                if not href or href.startswith(("#", "http:", "https:")):
                    continue
                self.assertTrue((path.parent / href).resolve().is_file(), (path, href))
        manifest = json.loads((self.out / "build-manifest.json").read_text())
        for name in manifest["files"]:
            self.assertFalse(name.startswith(("logs/", "output/", ".github/", "scripts/", "src/")))


if __name__ == "__main__":
    unittest.main()
