"""Incrementally archive unverified first-party RSS candidates.

The archive is *discovery only*. bot.validate_news() must verify publisher
metadata before anything is used in a published newsletter.
No Kakao, OpenAI, GitHub API calls or sends happen in this script.
"""
import argparse
import datetime as dt
import json
import logging
import os
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import requests

from feed_collector import parse_feed

TZ = ZoneInfo('Asia/Seoul')
ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / 'feeds.json'
ARCHIVE = ROOT / 'data' / 'rss_archive'
CATEGORIES = ('domestic_industry', 'domestic_supplement', 'overseas')


def _iso_date(value):
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError('Date/time without explicit timezone')
    return parsed.astimezone(TZ)


def _url_key(url):
    parts = urlsplit(url)
    # Retain query parameters: for some publishers, query identifies the article.
    return (parts.hostname or '').lower(), parts.path.rstrip('/'), parts.query


def _day_path(folder, day):
    return folder / (day.isoformat() + '.json')


def _read_day(folder, day):
    path = _day_path(folder, day)
    if not path.exists():
        return []
    obj = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(obj, dict) or obj.get('date') != day.isoformat() or not isinstance(obj.get('items'), list):
        raise ValueError('Invalid archive file: ' + str(path))
    return obj['items']


def _write_day(folder, day, items):
    folder.mkdir(parents=True, exist_ok=True)
    dest = _day_path(folder, day)
    body = json.dumps({'version': 1, 'date': day.isoformat(), 'items': items},
                      ensure_ascii=False, indent=2) + '\n'
    fd, tmp = tempfile.mkstemp(prefix='.rss_', suffix='.tmp', dir=str(folder))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(body)
        os.replace(tmp, dest)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def collect(*, now=None, config_path=CONFIG, archive_dir=ARCHIVE, get=None):
    """Save only new candidates. Return counts; repeated collection is idempotent.

    Each source must be configured explicitly in feeds.json and article links
    must match the source's declared first-party hostnames (parse_feed enforces).
    """
    now = now or dt.datetime.now(TZ)
    if now.tzinfo is None:
        raise ValueError('now must have timezone')
    now = now.astimezone(TZ)
    archive_dir = Path(archive_dir)
    config = json.loads(Path(config_path).read_text(encoding='utf-8'))
    feeds = config.get('feeds', [])
    if not isinstance(feeds, list) or not any(f.get('enabled', True) for f in feeds):
        raise RuntimeError('No enabled RSS sources in feeds.json')
    get = get or requests.get
    # RSS represents a rolling feed: archive recent items now, even if their
    # publication time isn't in the *current* newsletter collection window.
    earliest = now - dt.timedelta(days=8)
    latest = now + dt.timedelta(minutes=2)
    pending = {}
    fetched, failed, discovered = 0, 0, 0
    for feed in feeds:
        if not feed.get('enabled', True):
            continue
        if (not feed.get('url', '').startswith('https://') or not feed.get('publisher')
                or feed.get('category') not in ('domestic', 'overseas')
                or not isinstance(feed.get('hosts'), list) or not feed['hosts']):
            failed += 1
            logging.warning('Invalid RSS settings: %s', feed.get('publisher', '?'))
            continue
        try:
            response = get(feed['url'], timeout=12, headers={'User-Agent': 'KoreaDogNewsFeedArchive/1.0'})
            response.raise_for_status()
            rows = parse_feed(response.content, feed, earliest, latest)
            fetched += 1
            discovered += len(rows)
            for category, item in rows:
                day = _iso_date(item['published_at']).date()
                rec = dict(item)
                rec['category'] = category
                rec['captured_at'] = now.isoformat()
                rec['verification'] = 'rss_candidate_unverified'
                pending.setdefault(day, []).append(rec)
            logging.info('RSS %s: %d relevant recent candidates', feed['publisher'], len(rows))
        except (requests.RequestException, ET.ParseError, ValueError, KeyError) as exc:
            failed += 1
            logging.warning('RSS %s failed (%s)', feed.get('publisher', '?'), type(exc).__name__)
    if not fetched:
        raise RuntimeError('No RSS sources could be read: archive not changed')
    added = 0
    for day, new_items in sorted(pending.items()):
        existing = _read_day(archive_dir, day)
        seen = {_url_key(x['url']) for x in existing}
        changed = False
        for rec in new_items:
            key = _url_key(rec['url'])
            if key in seen:
                continue
            existing.append(rec)
            seen.add(key)
            added += 1
            changed = True
        if changed:
            existing.sort(key=lambda x: x['published_at'])
            _write_day(archive_dir, day, existing)
    logging.info('ARCHIVE_RESULT feeds_ok=%d feeds_failed=%d candidates=%d added=%d',
                 fetched, failed, discovered, added)
    return {'feeds_ok': fetched, 'feeds_failed': failed, 'candidates': discovered, 'added': added}


def load_for_issue(issue, *, archive_dir=ARCHIVE):
    """Replay saved candidates for the exact 06:00 KST edition window.

    Never changes the archive. Caller MUST use bot.create_issue for source
    publication-time verification, exclusion rules and final rendering.
    """
    end = dt.datetime.combine(issue, dt.time(6), TZ)
    start = end - dt.timedelta(days=3 if issue.weekday() == 0 else 1)
    results = {'domestic_industry': [], 'domestic_supplement': [], 'overseas': [], 'events': []}
    seen = set()
    day = start.date()
    archive_dir = Path(archive_dir)
    while day <= end.date():
        for rec in _read_day(archive_dir, day):
            try:
                moment = _iso_date(rec['published_at'])
                category = rec['category']
                key = _url_key(rec['url'])
                if category not in CATEGORIES or not (start <= moment < end) or key in seen:
                    continue
                # Only fields recognized by existing bot.validate_news.
                fields = ('title', 'url', 'publisher', 'published_at', 'summary')
                item = {x: rec[x] for x in fields}
                if not all(isinstance(v, str) and v.strip() for v in item.values()):
                    continue
                results[category].append(item)
                seen.add(key)
            except (KeyError, ValueError, TypeError):
                continue
        day += dt.timedelta(days=1)
    for category in CATEGORIES:
        results[category].sort(key=lambda x: x['published_at'], reverse=True)
        results[category] = results[category][:60]
    logging.info('REPLAY_RESULT issue=%s domestic=%d supplement=%d overseas=%d',
                 issue, len(results['domestic_industry']), len(results['domestic_supplement']),
                 len(results['overseas']))
    return results


# Self-tests use fixtures only, never RSS APIs, OpenAI, GitHub writes, or Kakao.
class ArchiveSelfTests(unittest.TestCase):
    NOW = dt.datetime(2026, 11, 6, 19, 0, tzinfo=TZ)
    RSS = b'''<?xml version="1.0"?><rss version="2.0"><channel>
    <item><title>\xeb\xb0\x98\xeb\xa0\xa4\xeb\x8f\x99\xeb\xac\xbc \xec\x82\xac\xeb\xa3\x8c \xec\x82\xb0\xec\x97\x85 \xeb\x89\xb4\xec\x8a\xa4</title>
    <link>https://www.newswire.co.kr/newsRead.php?no=123</link>
    <pubDate>Fri, 06 Nov 2026 05:30:00 +0900</pubDate>
    <description>\xec\x82\xac\xeb\xa3\x8c \xec\x82\xb0\xec\x97\x85 \xec\x8b\xa0\xec\xa0\x9c\xed\x92\x88</description></item>
    <item><title>\xeb\xb0\x98\xeb\xa0\xa4\xeb\x8f\x99\xeb\xac\xbc \xec\x82\xac\xeb\xa3\x8c \xec\x82\xb0\xec\x97\x85</title>
    <link>https://www.newswire.co.kr/newsRead.php?no=124</link>
    <pubDate>Fri, 06 Nov 2026 08:30:00 +0900</pubDate>
    <description>\xeb\xb0\x98\xeb\xa0\xa4\xeb\x8f\x99\xeb\xac\xbc \xec\x82\xac\xeb\xa3\x8c</description></item>
    </channel></rss>'''

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.feedfile = self.folder / 'feeds.json'
        self.feedfile.write_text(json.dumps({'feeds': [
            {'publisher': 'News', 'category': 'domestic', 'enabled': True,
             'url': 'https://api.newswire.co.kr/rss/all', 'hosts': ['www.newswire.co.kr']}
        ]}), encoding='utf-8')
        self.getter = lambda *a, **kw: _FakeResponse(self.RSS)

    def _collect(self):
        return collect(now=self.NOW, config_path=self.feedfile,
                       archive_dir=self.folder / 'archive', get=self.getter)

    def test_first_run_adds_two(self):
        self.assertEqual(self._collect()['added'], 2)

    def test_second_run_adds_zero(self):
        self._collect()
        self.assertEqual(self._collect()['added'], 0)

    def test_replay_0600_excludes_later_news(self):
        self._collect()
        rows = load_for_issue(dt.date(2026, 11, 6), archive_dir=self.folder / 'archive')
        self.assertEqual(len(rows['domestic_industry']), 1)
        self.assertIn('no=123', rows['domestic_industry'][0]['url'])

    def test_replay_next_issue_includes_later_news(self):
        self._collect()
        rows = load_for_issue(dt.date(2026, 11, 7), archive_dir=self.folder / 'archive')
        self.assertEqual(len(rows['domestic_industry']), 1)
        self.assertIn('no=124', rows['domestic_industry'][0]['url'])

    def test_no_feed_does_not_write(self):
        self.feedfile.write_text('{"feeds":[]}', encoding='utf-8')
        with self.assertRaises(RuntimeError):
            self._collect()
        self.assertFalse((self.folder / 'archive').exists())

    def test_failed_feed_does_not_save(self):
        def fail(*a, **kw):
            raise requests.Timeout('timeout')
        self.getter = fail
        with self.assertRaises(RuntimeError):
            self._collect()
        self.assertFalse((self.folder / 'archive').exists())

    def test_monday_window_is_72_hours(self):
        # Fri 06:00 <= article < Mon 06:00; Sunday article must count.
        folder = self.folder / 'archive'
        day = dt.date(2026, 11, 8)
        _write_day(folder, day, [{
            'category': 'overseas', 'title': 'Pet industry',
            'url': 'https://example.com/article', 'publisher': 'Source',
            'published_at': '2026-11-08T20:00:00+09:00', 'summary': 'Pet business'
        }])
        rows = load_for_issue(dt.date(2026, 11, 9), archive_dir=folder)
        self.assertEqual(len(rows['overseas']), 1)


class _FakeResponse:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        return None


def main():
    parser = argparse.ArgumentParser(description='KoreaDogNews RSS candidate archive pilot')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--collect', action='store_true', help='Read RSS feeds and archive new candidates')
    group.add_argument('--selftest', action='store_true', help='Run offline tests only')
    group.add_argument('--report', metavar='YYYY-MM-DD', help='Report archived candidates for issue date')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    if args.selftest:
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(ArchiveSelfTests)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        raise SystemExit(0 if result.wasSuccessful() else 1)
    elif args.collect:
        result = collect()
        print('ARCHIVE_OK added={added} feeds_ok={feeds_ok}'.format(**result))
    else:
        day = dt.date.fromisoformat(args.report)
        rows = load_for_issue(day)
        print('ARCHIVE_REPORT issue={} domestic={} supplement={} overseas={}'.format(
            day, len(rows['domestic_industry']), len(rows['domestic_supplement']), len(rows['overseas'])))


if __name__ == '__main__':
    main()
