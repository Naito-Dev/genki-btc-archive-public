"""Offline regression tests; all network calls use injected fakes."""
import copy
import json
import multiprocessing
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from btc_signal.core import (RULE_ID, SignalError, audit_record_calculation, calculate, canonical, digest, expected_dates,
                             load_verified_record, normalize_candles, presentation,
                             read_json, target_day)
from btc_signal.daily import fetch_candles, record_daily
from btc_signal.notify import build_message, notify_record, prepare_notification, send_notification
from btc_signal.storage import atomic_write

NOW = datetime(2026, 9, 22, 0, 20, tzinfo=timezone.utc)
TARGET = target_day(NOW)
IMPL = {"git_commit": "a" * 40, "files_sha256": {f"src/btc_signal/{name}.py": "b" * 64 for name in ("core", "daily", "storage")}}
WEBHOOK = "https://discord.com/api/webhooks/123/test-token"


def fixture_rows(last=None):
    rows = []
    for i, day in enumerate(expected_dates(TARGET)):
        close = str(100 + i) if last is None else "100"
        if last is not None and i == 99:
            close = last
        stamp = int(datetime.combine(day, datetime.min.time(), timezone.utc).timestamp())
        rows.append([stamp, "1", "1000", "100", close, "12"])
    return list(reversed(rows))  # Coinbase's response order is not trusted.


def concurrent_worker(folder, queue):
    root = Path(folder)
    def fetch(_):
        with (root / "fetch-count.txt").open("a") as handle:
            handle.write("fetched\n")
        return fixture_rows()
    try:
        result, created = record_daily(root, now=lambda: NOW, fetcher=fetch, implementation=IMPL)
        queue.put((created, result["result_sha256"]))
    except Exception as exc:
        queue.put(("error", type(exc).__name__))


class Response:
    def __init__(self, data, status=200):
        self.data, self.status = data, status
    def read(self, limit=-1):
        return self.data[:limit] if limit >= 0 else self.data
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def save(self, rows=None, **kwargs):
        return record_daily(self.root, now=lambda: NOW,
                            fetcher=lambda _: rows if rows is not None else fixture_rows(),
                            implementation=IMPL, **kwargs)

    def record_path(self):
        return self.root / "records" / RULE_ID / f"{TARGET}.json"

    def delivery_path(self):
        return self.root / "delivery" / f"{RULE_ID}-{TARGET}.json"

    def test_rule_exact_equality_and_epsilon(self):
        for last, decision in [("100", "CASH"), ("100.0000000000000001", "BTC"), ("99.9999999999999999", "CASH")]:
            with self.subTest(last=last):
                values = calculate(normalize_candles(fixture_rows(last), TARGET, NOW))
                self.assertEqual(values["decision"], decision)
        regular = calculate(normalize_candles(fixture_rows(), TARGET, NOW))
        self.assertEqual(regular, {"decision": "BTC", "close": "199", "sum100": "14950", "sma100": "149.5"})

    def test_utc_jst_boundary(self):
        jst = timezone(timedelta(hours=9))
        self.assertEqual(str(target_day(datetime(2026, 9, 22, 8, 59, tzinfo=jst))), "2026-09-20")
        self.assertEqual(str(target_day(datetime(2026, 9, 22, 9, 0, tzinfo=jst))), "2026-09-21")
        with self.assertRaises(SignalError):
            target_day(datetime(2026, 9, 22))

    def test_invalid_inputs_never_make_normal_record(self):
        base = fixture_rows()
        examples = {"missing": base[:-1], "duplicate": base + [base[0]],
                    "stale": [list(x) for x in base], "nan": copy.deepcopy(base),
                    "negative": copy.deepcopy(base), "ohlc": copy.deepcopy(base),
                    "current": copy.deepcopy(base), "misaligned": copy.deepcopy(base)}
        for row in examples["stale"]:
            row[0] -= 86400
        examples["nan"][0][4] = "NaN"
        examples["negative"][0][4] = "-1"
        examples["ohlc"][0][4] = "2000"
        examples["current"][0][0] += 86400
        examples["misaligned"][0][0] += 1
        for label, rows in examples.items():
            with self.subTest(label=label), self.assertRaises(SignalError):
                self.save(rows, run_id=label)
            self.assertFalse(self.record_path().exists())
            status = read_json(self.root / "status/latest.json")
            self.assertEqual(status["state"], "failed")
            self.assertEqual(status["label"], "判定不能")
            self.assertNotIn("decision", status)

    def test_rerun_keeps_bytes_without_fetch_or_calculation(self):
        record, created = self.save()
        self.assertTrue(created)
        before = self.record_path().read_bytes()
        input_before = (self.root / record["input_ref"]).read_bytes()
        def forbidden(*_):
            raise AssertionError("must not fetch/recalculate")
        with patch("btc_signal.core.calculate", forbidden), patch("btc_signal.daily.calculate", forbidden):
            second, created = record_daily(self.root, now=lambda: NOW, fetcher=forbidden)
        self.assertFalse(created)
        self.assertEqual(record, second)
        self.assertEqual(before, self.record_path().read_bytes())
        self.assertEqual(input_before, (self.root / record["input_ref"]).read_bytes())

    def test_record_and_input_tamper_fail(self):
        record, _ = self.save()
        record_blob = self.record_path().read_bytes()
        edited = dict(record, decision="CASH")
        self.record_path().write_text(json.dumps(edited))
        with self.assertRaisesRegex(SignalError, "record_hash_mismatch"):
            load_verified_record(self.root, self.record_path())
        self.record_path().write_bytes(record_blob)
        source_path = self.root / record["input_ref"]
        source_path.write_bytes(source_path.read_bytes() + b" ")
        with self.assertRaisesRegex(SignalError, "input_hash_mismatch"):
            self.save()
        self.assertEqual(record_blob, self.record_path().read_bytes())

    def test_explicit_offline_audit_verifies_calculation_without_mutation(self):
        record, _ = self.save()
        before = self.record_path().read_bytes()
        self.assertEqual(audit_record_calculation(self.root, self.record_path()), record)
        self.assertEqual(before, self.record_path().read_bytes())
        # Hash integrity alone is distinct from independent calculation verification.
        changed = {k: v for k, v in record.items() if k != "result_sha256"}
        changed["decision"] = "CASH"
        changed["result_sha256"] = digest(canonical(changed))
        self.record_path().write_text(json.dumps(changed))
        with self.assertRaisesRegex(SignalError, "calculation_mismatch"):
            audit_record_calculation(self.root, self.record_path())

    def test_atomic_no_clobber(self):
        path = self.root / "immutable.json"
        atomic_write(path, {"a": 1}, immutable=True)
        before = path.read_bytes()
        with self.assertRaisesRegex(SignalError, "immutable_artifact_exists"):
            atomic_write(path, {"a": 2}, immutable=True)
        self.assertEqual(path.read_bytes(), before)

    def test_two_processes_make_one_record_and_fetch(self):
        ctx = multiprocessing.get_context("fork")
        queue = ctx.Queue()
        processes = [ctx.Process(target=concurrent_worker, args=(str(self.root), queue)) for _ in range(2)]
        for process in processes:
            process.start()
        for process in processes:
            process.join(10)
            self.assertEqual(process.exitcode, 0)
        results = [queue.get(timeout=3) for _ in processes]
        self.assertEqual(sorted(x[0] for x in results), [False, True])
        self.assertEqual(results[0][1], results[1][1])
        self.assertEqual((self.root / "fetch-count.txt").read_text(), "fetched\n")
        self.assertEqual(len(list((self.root / "records" / RULE_ID).glob("*.json"))), 1)

    def test_orphan_input_recovery_does_not_refetch(self):
        from btc_signal.daily import atomic_write as original_write
        def crash_before_record(path, value, **kwargs):
            if "records" in path.parts:
                raise OSError("interrupted before result")
            return original_write(path, value, **kwargs)
        with patch("btc_signal.daily.atomic_write", crash_before_record), self.assertRaises(SignalError):
            self.save()
        self.assertFalse(self.record_path().exists())
        def forbidden(_):
            raise AssertionError("input already fixed")
        result, created = record_daily(self.root, now=lambda: NOW, fetcher=forbidden, implementation=IMPL)
        self.assertTrue(created)
        self.assertEqual(result["decision"], "BTC")

    def test_malformed_orphan_input_fails_closed(self):
        record, _ = self.save()
        self.record_path().unlink()
        input_path = self.root / record["input_ref"]
        payload = read_json(input_path)
        payload["private_extra"] = "must-not-be-published"
        input_path.write_text(json.dumps(payload))
        with self.assertRaisesRegex(SignalError, "input_contract_mismatch"):
            self.save()
        self.assertFalse(self.record_path().exists())

    def test_bad_implementation_never_creates_result(self):
        with self.assertRaisesRegex(SignalError, "implementation_missing"):
            record_daily(self.root, now=lambda: NOW, fetcher=lambda _: fixture_rows(),
                         implementation={"git_commit": "a" * 40, "files_sha256": {}})
        self.assertFalse(self.record_path().exists())

    def test_midnight_crossing_fails(self):
        ticks = iter([NOW.replace(hour=23, minute=59), NOW + timedelta(days=1), NOW + timedelta(days=1)])
        with self.assertRaisesRegex(SignalError, "utc_day_changed"):
            record_daily(self.root, now=lambda: next(ticks), fetcher=lambda _: fixture_rows(), implementation=IMPL)
        self.assertFalse(self.record_path().exists())

    def test_fetch_errors_are_bounded_and_sanitized(self):
        calls = []
        def fail(req, timeout):
            calls.append((req.full_url, timeout))
            raise URLError("private details")
        with self.assertRaisesRegex(SignalError, "^market_unavailable$"):
            fetch_candles(TARGET, opener=fail, sleeper=lambda _: None)
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(timeout == 15 for _, timeout in calls))
        self.assertTrue(all(url.startswith("https://api.exchange.coinbase.com/products/BTC-USD/candles?") for url, _ in calls))

    def test_fetch_permanent_error_not_retried(self):
        calls = []
        def fail(req, timeout):
            calls.append(1)
            raise HTTPError(req.full_url, 404, "bad", {}, None)
        with self.assertRaisesRegex(SignalError, "market_http_404"):
            fetch_candles(TARGET, opener=fail, sleeper=lambda _: None)
        self.assertEqual(len(calls), 1)

    def test_discord_uses_same_record_and_sent_rerun_skips(self):
        record, _ = self.save()
        before = self.record_path().read_bytes()
        web = presentation(record)
        message = build_message(record)
        for key in ("record_id", "target_date", "decision", "close", "sma100", "result_sha256"):
            self.assertIn(web[key], message)
        calls = []
        def sent(req, timeout):
            calls.append(req)
            self.assertEqual(timeout, 15)
            self.assertTrue(req.full_url.endswith("?wait=true"))
            self.assertEqual(json.loads(req.data)["content"], message)
            self.assertEqual(read_json(self.delivery_path())["notification_state"], "pending")
            return Response(b'{"id":"12345"}')
        with patch.dict(os.environ, {"DISCORD_WEBHOOK_URL": WEBHOOK}):
            first = notify_record(self.root, self.record_path(), "2026-09-22T00:20:00Z", opener=sent, now=lambda: NOW)
            second = notify_record(self.root, self.record_path(), "2026-09-22T00:20:00Z", opener=sent, now=lambda: NOW)
        self.assertEqual(len(calls), 1)
        self.assertEqual(first, second)
        self.assertEqual(first["notification_state"], "sent")
        self.assertEqual(before, self.record_path().read_bytes())

    def test_discord_404_fails_without_deleting_record(self):
        self.save()
        before = self.record_path().read_bytes()
        def missing(req, timeout):
            raise HTTPError(req.full_url, 404, "secret-containing-error", {}, None)
        with patch.dict(os.environ, {"DISCORD_WEBHOOK_URL": WEBHOOK}), self.assertRaisesRegex(SignalError, "^discord_http_404$"):
            notify_record(self.root, self.record_path(), "2026-09-22T00:20:00Z", opener=missing, now=lambda: NOW)
        self.assertEqual(read_json(self.delivery_path())["notification_state"], "failed")
        self.assertEqual(before, self.record_path().read_bytes())

    def test_discord_timeout_not_blindly_retried(self):
        self.save()
        calls = []
        def timeout(req, timeout):
            calls.append(1)
            raise TimeoutError("token-must-not-leak")
        with patch.dict(os.environ, {"DISCORD_WEBHOOK_URL": WEBHOOK}):
            with self.assertRaisesRegex(SignalError, "^discord_delivery_uncertain$"):
                notify_record(self.root, self.record_path(), "2026-09-22T00:20:00Z", opener=timeout, now=lambda: NOW)
            with self.assertRaisesRegex(SignalError, "delivery_uncertain_manual_review_required"):
                notify_record(self.root, self.record_path(), "2026-09-22T00:20:00Z", opener=timeout, now=lambda: NOW)
        self.assertEqual(len(calls), 1)
        self.assertEqual(read_json(self.delivery_path())["notification_state"], "uncertain")

    def test_committed_pending_intent_cannot_be_sent_by_new_run(self):
        self.save()
        intent = prepare_notification(self.root, self.record_path(), "2026-09-22T00:20:00Z", owner="100:1", now=lambda: NOW)
        # This is the durable remote state if a runner died after POST but before receipt commit.
        durable = self.delivery_path().read_bytes()
        calls = []
        def sent(req, timeout):
            calls.append(1)
            return Response(b'{"id":"98765"}')
        with patch.dict(os.environ, {"DISCORD_WEBHOOK_URL": WEBHOOK}):
            result = send_notification(self.root, self.record_path(), intent["attempt_id"], owner="100:1", opener=sent, now=lambda: NOW)
            self.assertEqual(result["notification_state"], "sent")
            self.delivery_path().write_bytes(durable)  # simulate fresh checkout of committed intent
            with self.assertRaisesRegex(SignalError, "delivery_attempt_owner_mismatch"):
                send_notification(self.root, self.record_path(), intent["attempt_id"], owner="100:2", opener=sent, now=lambda: NOW)
            with self.assertRaisesRegex(SignalError, "delivery_uncertain_manual_review_required"):
                prepare_notification(self.root, self.record_path(), "2026-09-22T00:20:00Z", owner="101:1", now=lambda: NOW)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
