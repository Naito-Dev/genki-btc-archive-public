"""Scheduled entry tests. No market requests, calculation, git writes or notifications."""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import operate
from btc_signal.core import SignalError


class ScheduleTests(unittest.TestCase):
    def env(self, event='schedule', ref='refs/heads/main'):
        return {'GITHUB_ACTIONS': 'true', 'GITHUB_EVENT_NAME': event,
                'GITHUB_REF': ref, 'GITHUB_REPOSITORY': operate.REPOSITORY,
                'GITHUB_RUN_ID': '123', 'GITHUB_RUN_ATTEMPT': '1'}

    def test_schedule_and_manual_contexts_are_allowed(self):
        for event in ('schedule', 'workflow_dispatch'):
            with self.subTest(event=event), patch.dict(os.environ, self.env(event), clear=True):
                self.assertEqual(operate.context(), '123-1')

    def test_other_events_and_branches_are_rejected(self):
        for event, ref in [('push', 'refs/heads/main'), ('repository_dispatch', 'refs/heads/main'),
                           ('schedule', 'refs/heads/test')]:
            with self.subTest(event=event, ref=ref), patch.dict(os.environ, self.env(event, ref), clear=True):
                with self.assertRaises(SignalError):
                    operate.context()

    def test_schedule_cannot_select_notification_or_historical_date(self):
        for mode, path in [('notify_only', ''), ('publish_only', ''),
                           ('record', 'records/SMA100_CLOSE_V1/2026-09-21.json')]:
            with self.subTest(mode=mode), patch.dict(os.environ, self.env(), clear=True):
                with patch.object(operate, 'open_record_prs') as lookup:
                    with self.assertRaisesRegex(SignalError, 'scheduled_run_must_record_latest_day'):
                        operate.operate(mode, path)
                    lookup.assert_not_called()

    def test_schedule_reaches_record_entry_without_calling_real_calculation(self):
        with patch.dict(os.environ, self.env(), clear=True), \
             patch.object(operate, 'open_record_prs', return_value=[]), \
             patch.object(operate, 'verify'), \
             patch.object(operate, 'record_daily', side_effect=RuntimeError('entry reached')) as entry:
            with self.assertRaisesRegex(RuntimeError, 'entry reached'):
                operate.operate('record', '')
            entry.assert_called_once_with(operate.ROOT, run_id='123-1')


if __name__ == '__main__':
    unittest.main()
