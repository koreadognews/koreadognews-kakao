"""Offline newsletter regression checks; every outbound request is blocked."""
import contextlib
import copy
import datetime as dt
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import bot
import send_once
import sync_kakao_token


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.site = Path(self.temp.name) / 'docs'
        self.enterContext(patch.object(bot, 'SITE', self.site))
        self.enterContext(patch('requests.sessions.Session.request', side_effect=AssertionError('Network forbidden')))
        self.date = dt.date(2026, 11, 8)  # Monday edition
        self.data = {
            'domestic': [dict(title='국내 산업 <보도>', summary='산업 요약 & 사실',
                              publisher='국내매체', published_at='2026-11-06T06:00:00+09:00', url='https://example.org/domestic')],
            'international': [dict(title='해외 산업', summary='해외 요약', publisher='Foreign News',
                                   published_at='2026-11-08T12:00:00+09:00', url='https://example.org/world')],
            'events': [dict(title='공식 행사', start_date='2026-11-20', end_date='2026-11-22', url='https://example.org/event')],
        }

    def render(self):
        return bot.render(self.data, self.date).read_text(encoding='utf-8')

    def test_preserves_all_items_and_canonical_paths(self):
        before = copy.deepcopy(self.data)
        page = self.render()
        self.assertEqual(before, self.data)
        self.assertEqual(page, (self.site / 'index.html').read_text(encoding='utf-8'))
        self.assertTrue((self.site / '2026-11-08.html').exists())
        for section in self.data.values():
            for item in section:
                self.assertIn(item['url'], page)
        self.assertIn('국내 산업 &lt;보도&gt;', page)
        self.assertIn('[해외] 해외 산업', page)
        self.assertIn('11.20~11.22', page)
        self.assertNotIn('국내 보충뉴스', page)

    def test_monday_window_and_no_fabricated_issue(self):
        with patch.dict(os.environ, {'VERIFIED_ISSUE_NUMBER': ''}):
            page = self.render()
        self.assertIn('2026.11.06 06:00 ~ 2026.11.09 06:00', page)
        self.assertIn('호수 확인 중', page)
        self.assertNotIn('제1051호', page)
        for day, hours in [(dt.date(2026, 11, 9), 72), (dt.date(2026, 11, 10), 24)]:
            start, end = bot.collection_window(day)
            self.assertEqual((end-start).total_seconds(), hours*3600)

    def test_verified_issue_only(self):
        with patch.dict(os.environ, {'VERIFIED_ISSUE_NUMBER': '1234'}):
            self.assertIn('제1234호', self.render())
        with patch.dict(os.environ, {'VERIFIED_ISSUE_NUMBER': '<script>'}):
            self.assertIn('호수 확인 중', self.render())

    def test_empty_events_and_articles(self):
        self.data = {'domestic': [], 'international': [], 'events': []}
        page = self.render()
        self.assertIn('향후 한 달 내 확인된 행사 없음', page)
        self.assertIn('확인된 기사가 없습니다.', page)

    def test_cli_generate_and_send_only_without_send(self):
        fixture = Path(self.temp.name) / 'input.json'
        fixture.write_text(json.dumps(self.data), encoding='utf-8')
        with patch.dict(os.environ, {'PUBLIC_BASE_URL': 'https://example.org/news'}), patch.object(bot, 'send_kakao') as send:
            for args in [ ['--input', str(fixture), '--skip-http-check'], ['--send-only'] ]:
                output = io.StringIO()
                with patch('sys.argv', ['bot.py', '--date', str(self.date)] + args), contextlib.redirect_stdout(output):
                    bot.main()
                self.assertIn('DRY RUN: no Kakao message sent', output.getvalue())
            send.assert_not_called()
        saved = json.loads((self.site / '2026-11-08.json').read_text(encoding='utf-8'))
        for key in self.data:
            self.assertEqual(len(saved[key]), len(self.data[key]))

    def test_duplicate_delivery_does_not_send(self):
        with patch.object(send_once, 'REPO', 'koreadognews/koreadognews-kakao'), patch.object(send_once, 'TOKEN', 'offline'), patch.object(send_once, 'api') as api, patch.object(send_once.subprocess, 'run') as run:
            api.return_value.status_code = 200
            send_once.main()
            run.assert_not_called()
            api.assert_called_once_with('GET')

    def test_no_token_file_does_not_update_secret(self):
        with patch.object(sync_kakao_token, 'TOKEN_FILE', Path(self.temp.name) / 'absent.json'), patch.object(sync_kakao_token.subprocess, 'run') as run:
            sync_kakao_token.main()
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
