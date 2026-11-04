"""No network/API access: strict tests for RSS discovery."""
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from zoneinfo import ZoneInfo
import feed_collector as fc

T = ZoneInfo('Asia/Seoul')
START = dt.datetime(2026, 11, 3, 6, tzinfo=T)
END = dt.datetime(2026, 11, 4, 6, tzinfo=T)
SOURCE = {'publisher': 'Example News', 'category': 'domestic', 'url': 'https://example.com/rss', 'hosts':['example.com']}
RSS = b'''<rss><channel><item><title>Pet food</title><link>https://example.com/a</link><pubDate>Tue, 03 Nov 2026 12:00:00 +0900</pubDate><description>\xeb\xb0\x98\xeb\xa0\xa4\xeb\x8f\x99\xeb\xac\xbc \xec\x82\xac\xeb\xa3\x8c \xec\x82\xb0\xec\x97\x85</description></item></channel></rss>'''

class FeedTests(unittest.TestCase):
    def test_valid_rss(self):
        rows=fc.parse_feed(RSS,SOURCE,START,END)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0][0],'domestic_industry')
    def test_rejects_non_original_link(self):
        s=RSS.replace(b'https://example.com/a',b'https://news.google.com/a')
        self.assertEqual(fc.parse_feed(s,SOURCE,START,END),[])
    def test_outside_window(self):
        self.assertEqual(fc.parse_feed(RSS,SOURCE,END,END+dt.timedelta(days=1)),[])
    def test_refuses_unconfigured_sources(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'feeds.json';p.write_text('{"feeds": []}',encoding='utf-8')
            with self.assertRaisesRegex(RuntimeError,'No verified'):
                fc.fetch_candidates(dt.date(2026,11,4),config_path=p)
    def test_collects_mock_feed(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'feeds.json';p.write_text(json.dumps({'feeds':[SOURCE]}),encoding='utf-8')
            resp=Mock(content=RSS);resp.raise_for_status.return_value=None
            r=fc.fetch_candidates(dt.date(2026,11,4),config_path=p,get=lambda *a,**k:resp)
            self.assertEqual(len(r['domestic_industry']),1)

if __name__=='__main__':unittest.main()
