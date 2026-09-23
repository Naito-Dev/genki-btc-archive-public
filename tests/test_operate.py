"""Production orchestration tests with every service and git mutation mocked."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import operate
from btc_signal.core import SignalError
from stage_site import stage


class OperateTests(unittest.TestCase):
    def setUp(self):
        # These scenarios model manual recovery as well as recording. Do not
        # inherit the outer Actions event; schedule guards have dedicated tests.
        event = patch.dict(os.environ, {"GITHUB_EVENT_NAME": "workflow_dispatch"})
        event.start()
        self.addCleanup(event.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.record = {"rule_id": "SMA100_CLOSE_V1", "target_date": "2026-09-21",
                       "record_id": "SMA100_CLOSE_V1-2026-09-21", "result_sha256": "a" * 64}
        self.path = "records/SMA100_CLOSE_V1/2026-09-21.json"
        self.events = []
        patches = {
            "ROOT": self.root, "context": lambda: "101-1", "open_record_prs": lambda: [],
            "verify": lambda root: None,
            "record_daily": lambda *a, **k: (self.record, True),
            "load_verified_record": lambda *a, **k: self.record,
            "build": lambda: self.events.append("build"),
            "persist": lambda run, phase, **kw: self.events.append("save:" + phase) or phase,
            "publish": lambda commit: self.events.append("publish:" + commit) or "2026-09-22T00:30:00Z",
            "verify_public": lambda path: self.events.append("public-match"),
            "prepare_notification": lambda *a, **k: self.events.append("prepare") or {"notification_state": "pending", "attempt_id": "nonce"},
            "send_notification": lambda *a, **k: self.events.append("POST") or {},
        }
        self.original_guards = operate.open_record_prs
        self.original_publish = operate.publish
        self.original_persist = operate.persist
        self.original_public_verify = operate.verify_public
        for name, value in patches.items():
            p = patch.object(operate, name, value)
            p.start()
            self.addCleanup(p.stop)

    def test_notification_intent_is_preserved_before_post(self):
        operate.operate("record", "")
        self.assertLess(self.events.index("public-match"), self.events.index("prepare"))
        self.assertLess(self.events.index("save:notification-intent"), self.events.index("POST"))
        self.assertLess(self.events.index("POST"), self.events.index("save:notification-receipt"))

    def test_failed_calculation_is_saved_and_published_but_not_notified(self):
        with patch.object(operate, "record_daily", side_effect=SignalError("missing_candles")):
            with self.assertRaisesRegex(SignalError, "missing_candles"):
                operate.operate("record", "")
        self.assertIn("save:record-and-site", self.events)
        self.assertIn("publish:record-and-site", self.events)
        self.assertNotIn("prepare", self.events)

    def test_site_failure_preserves_new_result(self):
        with patch.object(operate, "build", side_effect=ValueError("site broken")):
            with patch.object(operate, "persist", return_value="saved") as save:
                with self.assertRaisesRegex(SignalError, "site_build_failed_result_preserved"):
                    operate.operate("record", "")
        save.assert_called_once_with("101-1", "record-preservation", include_site=False)
        self.assertNotIn("POST", self.events)

    def test_failed_save_never_publishes_or_sends(self):
        with patch.object(operate, "persist", side_effect=SignalError("saved_pr_not_merged")):
            with self.assertRaisesRegex(SignalError, "saved_pr_not_merged"):
                operate.operate("record", "")
        self.assertFalse(any(x.startswith("publish:") for x in self.events))
        self.assertNotIn("POST", self.events)

    def test_preservation_refuses_changes_to_committed_record(self):
        with patch.object(operate, "command", return_value="M\trecords/SMA100_CLOSE_V1/2026-09-20.json") as command:
            with self.assertRaisesRegex(SignalError, "committed_record_or_input_changed"):
                self.original_persist("101-1", "record-preservation", include_site=False)
        command.assert_called_once()

    def test_publication_failure_stops_before_notify(self):
        with patch.object(operate, "publish", side_effect=SignalError("pages_build_failed")):
            with self.assertRaisesRegex(SignalError, "pages_build_failed"):
                operate.operate("record", "")
        self.assertIn("save:record-and-site", self.events)
        self.assertNotIn("prepare", self.events)

    def test_publication_retry_never_calls_calculation(self):
        with patch.object(operate, "record_daily", side_effect=AssertionError("no recalculation")):
            operate.operate("publish_only", self.path)
        self.assertIn("POST", self.events)

    def test_existing_pr_blocks_before_calculation(self):
        with patch.object(operate, "open_record_prs", return_value=[{"number": 9}]):
            with patch.object(operate, "record_daily") as calculate:
                with self.assertRaisesRegex(SignalError, "previous_saved_record_pr_needs_attention"):
                    operate.operate("record", "")
        calculate.assert_not_called()

    def test_pushed_branch_without_pr_blocks_replacement(self):
        refs = [{"ref": "refs/heads/codex/daily-100-1-record-and-site", "object": {"sha": "a" * 40}}]
        with patch.object(operate, "api", side_effect=[[], refs, []]):
            with self.assertRaisesRegex(SignalError, "previous_saved_branch_needs_attention"):
                self.original_guards()

    def test_merged_preservation_branch_does_not_block(self):
        ref = "codex/daily-100-1-record-and-site"
        refs = [{"ref": "refs/heads/" + ref, "object": {"sha": "a" * 40}}]
        merged = [{"merged_at": "2026-09-22T00:10:00Z", "head": {"ref": ref}, "base": {"ref": "main"}}]
        with patch.object(operate, "api", side_effect=[[], refs, merged]):
            self.assertEqual(self.original_guards(), [])

    def test_notification_failure_receipt_is_saved_then_run_fails(self):
        with patch.object(operate, "send_notification", side_effect=SignalError("discord_http_404")):
            with self.assertRaisesRegex(SignalError, "discord_http_404"):
                operate.operate("record", "")
        self.assertIn("save:notification-intent", self.events)
        self.assertIn("save:notification-receipt", self.events)
        self.assertIn("publish:notification-receipt", self.events)

    def test_failed_intent_commit_never_posts(self):
        def save(run, phase, **kw):
            if phase == "notification-intent":
                raise SignalError("saved_pr_not_merged")
            return phase
        with patch.object(operate, "persist", save), self.assertRaises(SignalError):
            operate.operate("record", "")
        self.assertNotIn("POST", self.events)

    def test_public_mismatch_stops_notification(self):
        with patch.object(operate, "verify_public", side_effect=SignalError("published_record_does_not_match")):
            with self.assertRaisesRegex(SignalError, "published_record_does_not_match"):
                operate.operate("record", "")
        self.assertNotIn("prepare", self.events)

    def test_public_comparison_is_bounded(self):
        path = self.root / self.path
        path.parent.mkdir(parents=True)
        path.write_bytes(b"expected")
        class Different:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, maximum): return b"different"
        with patch.object(operate.request, "urlopen", return_value=Different()) as get:
            with patch.object(operate.time, "sleep"):
                with self.assertRaisesRegex(SignalError, "published_record_does_not_match"):
                    self.original_public_verify(self.path)
        self.assertEqual(get.call_count, 6)
        self.assertTrue(all(call.kwargs["timeout"] == 15 for call in get.call_args_list))

    def test_pages_moved_main_is_not_reported_as_this_commit(self):
        with patch.object(operate, "api", return_value={"object": {"sha": "different"}}) as api:
            with self.assertRaisesRegex(SignalError, "pages_main_changed_retry_publication"):
                self.original_publish("expected")
        api.assert_called_once_with("git/ref/heads/main")


class StageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        for folder in ("config", "docs", "_site"):
            (self.root / folder).mkdir()
        (self.root / "config/legacy-sha256.json").write_text('{"files":{}}')

    def test_removed_output_is_returned_for_git_deletion(self):
        (self.root / "docs/build-manifest.json").write_text('{"files":["old.json"]}')
        (self.root / "docs/old.json").write_text("old")
        (self.root / "_site/build-manifest.json").write_text('{"files":["index.html"]}')
        (self.root / "_site/index.html").write_text("new")
        allowed = stage(self.root)
        self.assertIn("docs/old.json", allowed)
        self.assertFalse((self.root / "docs/old.json").exists())
        self.assertEqual((self.root / "docs/index.html").read_text(), "new")

    def test_symlink_cannot_overwrite_outside_docs(self):
        outside = self.root / "original.txt"
        outside.write_text("preserve")
        (self.root / "docs/index.html").symlink_to(outside)
        (self.root / "_site/index.html").write_text("new")
        (self.root / "_site/build-manifest.json").write_text('{"files":["index.html"]}')
        with self.assertRaisesRegex(ValueError, "unsafe_static_symlink"):
            stage(self.root)
        self.assertEqual(outside.read_text(), "preserve")


if __name__ == "__main__":
    unittest.main()
