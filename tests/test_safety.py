"""Offline regression tests. No GitHub writes, Kakao sends or OpenAI calls."""
import datetime as dt
import json
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

import bot
import send_once


class APIConnectionError(Exception):
    pass


class APITimeoutError(Exception):
    pass


class InternalServerError(Exception):
    pass


class RateLimitError(Exception):
    pass


class TestOpenAIRetry(unittest.TestCase):
    def setUp(self):
        self.create = MagicMock()
        self.client = MagicMock()
        self.client.responses.create = self.create
        self.openai = types.ModuleType('openai')
        self.openai.OpenAI = MagicMock(return_value=self.client)
        for cls in (APIConnectionError, APITimeoutError,
                    InternalServerError, RateLimitError):
            setattr(self.openai, cls.__name__, cls)
        self.openai_patch = patch.dict(sys.modules, {'openai': self.openai})
        self.sleep_patch = patch.object(bot.time, 'sleep')
        self.openai_patch.start()
        self.sleep = self.sleep_patch.start()
        self.addCleanup(self.openai_patch.stop)
        self.addCleanup(self.sleep_patch.stop)
        self.date = dt.date(2026, 11, 2)
        self.success = types.SimpleNamespace(output_text=json.dumps({'articles': []}))

    def test_first_attempt_success(self):
        self.create.return_value = self.success
        self.assertEqual(bot.fetch_research(self.date), {'articles': []})
        self.assertEqual(self.create.call_count, 1)
        self.sleep.assert_not_called()
        self.openai.OpenAI.assert_called_once_with(max_retries=0)

    def test_one_temporary_failure(self):
        self.create.side_effect = [APIConnectionError('network'), self.success]
        bot.fetch_research(self.date)
        self.assertEqual(self.create.call_count, 2)
        self.sleep.assert_called_once_with(20)

    def test_two_temporary_failures(self):
        self.create.side_effect = [APITimeoutError('timeout'),
                                   InternalServerError('server'), self.success]
        bot.fetch_research(self.date)
        self.assertEqual(self.create.call_count, 3)
        self.assertEqual([c.args[0] for c in self.sleep.call_args_list], [20, 60])

    def test_three_failures_stop(self):
        self.create.side_effect = APIConnectionError('network')
        with self.assertRaises(APIConnectionError):
            bot.fetch_research(self.date)
        self.assertEqual(self.create.call_count, 3)
        self.assertEqual([c.args[0] for c in self.sleep.call_args_list], [20, 60])

    def test_quota_error_does_not_retry(self):
        self.create.side_effect = RateLimitError('insufficient_quota')
        with self.assertRaises(RateLimitError):
            bot.fetch_research(self.date)
        self.assertEqual(self.create.call_count, 1)
        self.sleep.assert_not_called()

    def test_temporary_rate_limit_retries(self):
        self.create.side_effect = [RateLimitError('rate limit reached'), self.success]
        bot.fetch_research(self.date)
        self.assertEqual(self.create.call_count, 2)
        self.sleep.assert_called_once_with(20)


class TestKakaoDeliveryLedger(unittest.TestCase):
    def setUp(self):
        self.patches = [
            patch.object(send_once, 'REPO', 'koreadognews/koreadognews-kakao'),
            patch.object(send_once, 'TOKEN', 'FAKE_TEST_TOKEN'),
            patch.object(send_once, 'DATE', '2026-11-02'),
            patch.object(send_once, 'api'),
            patch.object(send_once.subprocess, 'run'),
        ]
        self.values = [p.start() for p in self.patches]
        for p in self.patches:
            self.addCleanup(p.stop)
        self.api, self.run = self.values[3:]
        self.run.return_value.returncode = 0

    @staticmethod
    def response(status, sha=None):
        r = MagicMock(status_code=status)
        if sha is not None:
            r.json.return_value = {'content': {'sha': sha}}
        return r

    def test_existing_ledger_skips_send(self):
        self.api.return_value = self.response(200)
        send_once.main()
        self.run.assert_not_called()
        self.assertEqual(self.api.call_count, 1)

    def test_first_send_records_sent(self):
        self.api.side_effect = [self.response(404), self.response(201, 'sha1'), self.response(200)]
        send_once.main()
        self.run.assert_called_once()
        self.assertEqual(self.api.call_count, 3)
        self.assertEqual(self.api.call_args_list[1].kwargs['body']['message'],
                         'Kakao delivery claimed for 2026-11-02')
        self.assertEqual(self.api.call_args_list[2].kwargs['body']['sha'], 'sha1')

    def test_failed_send_leaves_claim(self):
        self.api.side_effect = [self.response(404), self.response(201, 'sha1')]
        self.run.return_value.returncode = 1
        with self.assertRaisesRegex(RuntimeError, 'Claim retained'):
            send_once.main()
        self.assertEqual(self.api.call_count, 2)

    def test_permission_failure_does_not_send(self):
        self.api.return_value = self.response(403)
        with self.assertRaisesRegex(RuntimeError, 'Cannot check delivery record'):
            send_once.main()
        self.run.assert_not_called()

    def test_another_run_claimed_first(self):
        self.api.side_effect = [self.response(404), self.response(422)]
        send_once.main()
        self.run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
