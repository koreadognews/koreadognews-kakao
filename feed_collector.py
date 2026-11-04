"""First-party RSS/Atom candidate discovery; source URL and timestamps are verified later in bot.

No OpenAI web-search calls. Source feeds are operator-configured in feeds.json.
Only direct original-publisher HTTPS article links are admitted as candidates.
"""
import datetime as dt
import email.utils
import html
import json
import logging
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests

TZ = ZoneInfo('Asia/Seoul')
FEEDS_FILE = Path(__file__).with_name('feeds.json')
KEYWORDS_KO = ('반려동물', '반려견', '반려묘', '펫푸드', '펫산업', '애견', '강아지', '고양이', '사료', '펫테크', '동물복지', '동물보호')
KEYWORDS_EN = ('pet', 'dog', 'cat', 'animal', 'kibble', 'treat')
INDUSTRY_KO = ('산업', '시장', '기업', '제조', '생산', '수출', '수입', '유통', '투자', '매출', '통계', '정책', '지원', '기술', '사료', '간식', '펫푸드', '펫테크', '제품', '용품', '브랜드', '사업', '스타트업')
BLOCKED_KO = ('동물병원', '수의사', '임상시험', '동물용 의약품', '동물용 의료기기')


def _clean(text):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', text or ''))).strip()


def _date(text):
    if not text:
        return None
    try:
        val = dt.datetime.fromisoformat(text.strip().replace('Z', '+00:00'))
    except ValueError:
        try:
            val = email.utils.parsedate_to_datetime(text.strip())
        except (TypeError, ValueError, IndexError):
            return None
    return val.astimezone(TZ) if val.tzinfo and val.utcoffset() is not None else None


def _item_link(node):
    for child in node:
        if child.tag.rsplit('}', 1)[-1] == 'link':
            href = child.get('href')
            if href and child.get('rel', 'alternate') == 'alternate':
                return href.strip()
            if (child.text or '').strip():
                return child.text.strip()
    return ''


def _field(node, names):
    for child in node:
        if child.tag.rsplit('}', 1)[-1] in names and (child.text or '').strip():
            return child.text.strip()
    return ''


def parse_feed(xml_bytes, source, start, end):
    """Return eligible RSS/Atom candidate records without claiming verified publication."""
    root = ET.fromstring(xml_bytes)
    result = []
    for entry in root.iter():
        if entry.tag.rsplit('}', 1)[-1] not in ('item', 'entry'):
            continue
        title = _clean(_field(entry, {'title'}))
        url = _item_link(entry)
        timestamp = _date(_field(entry, {'published', 'pubDate', 'date', 'issued'}))
        summary = _clean(_field(entry, {'description', 'summary', 'content', 'encoded'}))[:400]
        if not (title and url and timestamp and start <= timestamp < end):
            continue
        parsed = urlparse(url)
        if parsed.scheme != 'https' or not parsed.hostname:
            continue
        if parsed.hostname.lower() not in source['hosts']:
            continue
        topic = (title + ' ' + summary).lower()
        category = source['category']
        if category == 'overseas':
            if not any(word in topic for word in KEYWORDS_EN):
                continue
        else:
            if not any(word in topic for word in KEYWORDS_KO):
                continue
            if any(word in topic for word in BLOCKED_KO):
                continue
            category = 'domestic_industry' if any(word in topic for word in INDUSTRY_KO) else 'domestic_supplement'
        if not summary:
            summary = title  # transparent source headline, not a generated summary
        result.append((category, {'title':title, 'url':url, 'publisher':source['publisher'],
                                  'published_at':timestamp.isoformat(), 'summary':summary}))
    return result


def fetch_candidates(issue, start=None, end=None, *, config_path=None, get=None):
    if start is None or end is None:
        end = dt.datetime.combine(issue, dt.time(hour=6), TZ)
        start = end - dt.timedelta(days=3 if issue.weekday() == 0 else 1)
    path = Path(config_path) if config_path else FEEDS_FILE
    config = json.loads(path.read_text(encoding='utf-8'))
    feeds = config.get('feeds', [])
    if not feeds:
        raise RuntimeError('No verified RSS/Atom source URLs configured in feeds.json')
    candidates = {'domestic_industry': [], 'domestic_supplement': [], 'overseas': [], 'events': []}
    successes = 0
    requester = get or requests.get
    for feed in feeds:
        if not feed.get('enabled', True):
            continue
        if not (feed.get('url', '').startswith('https://') and feed.get('hosts')
                and feed.get('category') in ('domestic', 'overseas') and feed.get('publisher')):
            logging.warning('Invalid feed config skipped: %s', feed.get('publisher', 'unknown'))
            continue
        try:
            response = requester(feed['url'], timeout=12, headers={'User-Agent': 'KoreaDogNewsFeedBot/1.0'})
            response.raise_for_status()
            items = parse_feed(response.content, feed, start, end)
            for category, entry in items:
                candidates[category].append(entry)
            successes += 1
            logging.info('Feed %s: %d candidates', feed['publisher'], len(items))
        except (requests.RequestException, ET.ParseError, ValueError, KeyError) as exc:
            logging.warning('Feed failed for %s (%s)', feed['publisher'], type(exc).__name__)
    if not successes:
        raise RuntimeError('All configured news feeds failed; no newsletter generated')
    for key in candidates:
        candidates[key] = candidates[key][:60]
    logging.info('RSS candidates: domestic=%d supplement=%d overseas=%d',
                 len(candidates['domestic_industry']), len(candidates['domestic_supplement']),
                 len(candidates['overseas']))
    return candidates


def summarize_verified(issue_data):
    """Optional short OpenAI summarization AFTER original-source validation, no web tools.

    If API is slow/unavailable, retain source-provided abstracts rather than invent.
    """
    import os
    if os.getenv('ENABLE_OPENAI_SUMMARY', 'false').lower() != 'true':
        return issue_data
    records = issue_data['domestic_industry'] + issue_data['domestic_supplement'] + issue_data['overseas']
    if not records:
        return issue_data
    try:
        from openai import OpenAI
        client = OpenAI(max_retries=0, timeout=35.0)
        payload = [{'id': i, 'title': x['title'], 'publisher': x['publisher'],
                    'source_excerpt':x['summary']} for i, x in enumerate(records)]
        response = client.responses.create(
            model=os.getenv('OPENAI_MODEL', 'gpt-5-mini'),
            input=('Write concise Korean one-sentence news summaries using ONLY the data in this JSON. '
                   'Do not add facts. Return ONLY a JSON array of {"id":int,"summary":str}. '
                   'If a source excerpt is insufficient, copy or lightly rewrite its title.\n'
                   + json.dumps(payload, ensure_ascii=False)))
        parsed = json.loads(response.output_text)
        if isinstance(parsed, list):
            for part in parsed:
                if isinstance(part, dict) and isinstance(part.get('id'), int) and isinstance(part.get('summary'), str):
                    i = part['id']
                    if 0 <= i < len(records) and 5 <= len(part['summary']) <= 300:
                        records[i]['summary'] = part['summary'].strip()
    except Exception as exc:
        logging.warning('Optional AI summaries skipped: %s', type(exc).__name__)
    return issue_data
