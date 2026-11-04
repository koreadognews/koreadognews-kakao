"""Offline verification of the proposed unified edition; no API calls or Kakao sends."""
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bot


class TestUnifiedIssue(unittest.TestCase):
    def test_regular_interval(self):
        start, end = bot.bounds(dt.date(2026, 11, 4))
        self.assertEqual(start.isoformat(), '2026-11-03T06:00:00+09:00')
        self.assertEqual(end.isoformat(), '2026-11-04T06:00:00+09:00')

    def test_monday_interval(self):
        start, end = bot.bounds(dt.date(2026, 11, 9))
        self.assertEqual(start.isoformat(), '2026-11-06T06:00:00+09:00')

    def test_metadata_requires_timezone(self):
        self.assertIsNone(bot.parse_aware_timestamp('2026-11-03T12:34:00'))
        self.assertIsNotNone(bot.parse_aware_timestamp('2026-11-03T12:34:00+09:00'))

    def test_metadata_extraction(self):
        page = '<meta property="article:published_time" content="2026-11-03T12:34:00+09:00">'
        self.assertEqual(len(bot.source_metadata_times(page)), 1)

    def test_outside_window_excluded(self):
        start,end=bot.bounds(dt.date(2026,11,4))
        d={'title':'Industry','url':'https://example.com/news','publisher':'Outlet','published_at':'2026-11-02T08:00:00+09:00','summary':'Summary'}
        with patch.object(bot,'get_source',side_effect=AssertionError('Should not fetch old item')):
            out = bot.validate_news([d],start,end,15,set(),[],category='domestic_industry')
        self.assertEqual(out,[])

    def test_verified_news_included(self):
        start,end=bot.bounds(dt.date(2026,11,4))
        d={'title':'Industry','url':'https://example.com/news','publisher':'Outlet','published_at':'2026-11-03T12:34:00+09:00','summary':'Summary'}
        with patch.object(bot,'get_source',return_value='<meta property="article:published_time" content="2026-11-03T12:34:00+09:00">'):
            out = bot.validate_news([d],start,end,15,set(),[],category='domestic_industry')
        self.assertEqual(len(out),1)

    def test_missing_metadata_excluded(self):
        start,end=bot.bounds(dt.date(2026,11,4))
        d={'title':'Industry','url':'https://example.com/news','publisher':'Outlet','published_at':'2026-11-03T12:34:00+09:00','summary':'Summary'}
        with patch.object(bot,'get_source',return_value='<html><body>story</body></html>'):
            out = bot.validate_news([d],start,end,15,set(),[],category='domestic_industry')
        self.assertEqual(out,[])

    def test_shared_text_matches_saved_json(self):
        i=dt.date(2026,11,4)
        start,end=bot.bounds(i)
        edition={'schema_version':1,'issue_date':i.isoformat(),'collection_start':start.isoformat(),
                 'collection_end_exclusive':end.isoformat(),'generated_at':end.isoformat(),
                 'issue_number':'확인 중','domestic_industry':[{'title':'New product','url':'https://example.com/a','summary':'Summary'}],
                 'domestic_supplement':[],'overseas':[{'title':'Global market','url':'https://example.com/b','summary':'Summary'}],
                 'events':[]}
        with tempfile.TemporaryDirectory() as tmp, patch.object(bot,'SITE',Path(tmp)):
            bot.render(edition)
            data=json.loads((Path(tmp)/'issues'/f'{i}.json').read_text(encoding='utf-8'))
            text=(Path(tmp)/'issues'/f'{i}.txt').read_text(encoding='utf-8')
            self.assertEqual(data['text'],text)
            self.assertIn('[해외] Global market', text)
            self.assertIn('기사 기준일 2026-11-03',(Path(tmp)/'2026-11-03.html').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()

