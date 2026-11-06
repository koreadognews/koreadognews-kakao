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
KEYWORDS_KO = ('반려동물', '반려견', '반려묘', '애견', '애묘', '애완동물', '펫푸드', '펫테크', '펫산업', '펫보험', '펫용품', '펫숍', '펫샵', '강아지', '고양이', '동물등록', '유기동물', '펫')
KEYWORDS_EN = ('pet', 'dog', 'cat', 'animal', 'kibble', 'treat')
INDUSTRY_KO = ('산업', '시장', '기업', '제조', '생산', '수출', '수입', '유통', '투자', '매출', '통계', '정책', '지원', '기술', '사료', '간식', '펫푸드', '펫테크', '제품', '용품', '브랜드', '사업', '스타트업')
BLOCKED_KO = ('동물병원', '수의사', '임상시험', '동물용 의약품', '동물용 의료기기')


def _clean(text):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', text or ''))).strip()


# Only these original Korean government publishers use timezone-less local RSS dates.
# The inferred timestamp is a CANDIDATE, not a verified original publication time.
KST_ASSUMED_SOURCES = frozenset({
    '농림축산식품부 보도자료', '중소벤처기업부 보도자료'
})


def _date(text, *, assume_kst=False):
    if not text:
        return None
    raw = text.strip()
    # Korean weekday and month names with explicit KST (e.g. MOIS).
    korean = re.fullmatch(
        r'(?:[월화수목금토일],\s*)?(\d{1,2})\s+(\d{1,2})월\s+(\d{4})\s+(\d{2}:\d{2}:\d{2})\s+KST',
        raw
    )
    if korean:
        day, month, year, hhmmss = korean.groups()
        try:
            parsed = dt.datetime.strptime(
                f'{year}-{int(month):02d}-{int(day):02d} {hhmmss}',
                '%Y-%m-%d %H:%M:%S'
            )
            return parsed.replace(tzinfo=TZ)
        except ValueError:
            return None
    try:
        value = dt.datetime.fromisoformat(raw.replace('Z', '+00:00'))
    except ValueError:
        try:
            # MSS uses YYYYMMDDHHMMSS, without an offset.
            value = dt.datetime.strptime(raw, '%Y%m%d%H%M%S')
        except ValueError:
            try:
                value = email.utils.parsedate_to_datetime(raw)
            except (TypeError, ValueError, IndexError):
                return None
    if value.tzinfo is None or value.utcoffset() is None:
        # Never silently assume a timezone for unapproved sources.
        return value.replace(tzinfo=TZ) if assume_kst else None
    return value.astimezone(TZ)


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



# Vendor-specific RSS tolerance. Returned values are unverified candidates:
# bot.validate_news still requires matching publisher-page timestamps.
LOCAL_RSS_SOURCES = frozenset({
    '농림축산식품부 보도자료', '중소벤처기업부 보도자료',
    '행정안전부 보도자료', '매일경제 기업·경영', '매일경제 경제'
})


def _source_date_base(node, source):
    raw = _field(node, {'published', 'pubDate', 'date', 'issued', 'regDate', 'datePublished'})
    if not raw:
        return None
    publisher = source.get('publisher', '')
    # Only approved Korean first-party RSS vendors may use KST for a
    # timezone-less date. This is NEVER publication verification.
    assume_kst = publisher in LOCAL_RSS_SOURCES
    parsed = _date(raw, assume_kst=assume_kst)
    if parsed is not None:
        return parsed
    # Some publishers use a Korean/compact dotted date, not RFC 822.
    # A full time of day is required; date-only records stay excluded.
    exact = raw.strip()
    for fmt in ('%Y.%m.%d %H:%M:%S', '%Y.%m.%d. %H:%M:%S',
                '%Y.%m.%d %H:%M', '%Y.%m.%d. %H:%M',
                '%Y년 %m월 %d일 %H:%M:%S', '%Y년 %m월 %d일 %H:%M'):
        try:
            candidate = dt.datetime.strptime(exact, fmt)
        except ValueError:
            continue
        return candidate.replace(tzinfo=TZ) if assume_kst else None
    # Explicit Korean-standard-time suffix from an authorized RSS.
    if assume_kst and exact.endswith(' KST'):
        core = exact[:-4].strip()
        try:
            candidate = dt.datetime.fromisoformat(core)
        except ValueError:
            candidate = None
        if candidate is not None and candidate.tzinfo is None:
            return candidate.replace(tzinfo=TZ)
    return None



# This publisher-specific candidate parser does NOT verify publication.
# All accepted items still require bot.validate_news original-page checks.
HERALD_TEST_PUBLISHERS = frozenset({
    '헤럴드경제 산업 추가시험', '헤럴드경제 컨슈머 추가시험',
    '헤럴드경제 생활문화 추가시험', '헤럴드경제 전체 추가시험'
})


def _source_date(node, source):
    known = _source_date_base(node, source)
    if known is not None:
        return known
    # First-party Herald Google News RSS only; never reinterpret dates
    # from unrelated publishers or sites as Korean standard time.
    if (source.get('publisher') not in HERALD_TEST_PUBLISHERS
            or not source.get('url', '').startswith('https://biz.heraldcorp.com/rss/google/')
            or 'biz.heraldcorp.com' not in source.get('hosts', [])):
        return None
    for child in list(node):
        label = child.tag.rsplit('}', 1)[-1].lower()
        if label not in {
            'pubdate', 'published', 'updated', 'date', 'issued',
            'regdate', 'datepublished', 'created', 'publicationdate',
            'publishdate', 'datecreated', 'publish_date', 'datetime'
        }:
            continue
        raw = (child.text or '').strip()
        # No date-only guessing: exact clock time is mandatory.
        if not re.search(r'\d{1,2}:\d{2}', raw):
            continue
        parsed = _date(raw, assume_kst=True)
        if parsed:
            return parsed
        for fmt in (
            '%Y.%m.%d %H:%M:%S', '%Y.%m.%d %H:%M',
            '%Y.%m.%d. %H:%M:%S', '%Y.%m.%d. %H:%M',
            '%Y/%m/%d %H:%M:%S', '%Y/%m/%d %H:%M',
            '%Y년 %m월 %d일 %H:%M:%S', '%Y년 %m월 %d일 %H:%M',
        ):
            try:
                return dt.datetime.strptime(raw, fmt).replace(tzinfo=TZ)
            except ValueError:
                pass
    return None



def _source_link(node, source):
    raw = _item_link(node)
    parsed = urlparse(raw)
    # MAFRA RSS sometimes gives first-party HTTP URLs. Upgrade only
    # these exact government hosts to HTTPS; never accept third parties.
    if (source.get('publisher') == '농림축산식품부 보도자료'
            and parsed.scheme == 'http'
            and (parsed.hostname or '').lower() in {'www.mafra.go.kr', 'mafra.go.kr'}):
        return parsed._replace(scheme='https').geturl()
    return raw



# Domestic editorial prefilter: candidate discovery only, NOT final approval.
# Ground headline topic first: generic mentions buried in a summary of an
# unrelated performance or crime must not become pet-industry news.
_DOMESTIC_HEADLINE_PET = (
    '반려동물', '반려견', '반려묘', '애견', '애묘', '애완동물',
    '펫푸드', '펫테크', '펫산업', '펫보험', '펫용품', '펫숍',
    '펫샵', '펫', '강아지', '고양이', '유기동물', '동물등록',
    'pet food', 'petfood', 'pet tech', 'pet industry',
)
_GOVERNMENT_FEEDS = frozenset({
    '농림축산식품부 보도자료', '중소벤처기업부 보도자료',
    '행정안전부 보도자료', '서울시 내 손안에 서울',
})
_GOVERNMENT_SUMMARY_PET = (
    '반려동물', '반려견', '반려묘', '펫푸드', '펫테크', '펫산업',
    '애견', '애묘', '유기동물', '강아지', '고양이',
)
_INCIDENT_HEADLINE = (
    '사체', '주검', '학대', '죽은', '숨진', '사망',
    '유골함', '도살', '살해', '동물사건', '입건', '수사', '고발',
)
_INDUSTRY_HEADLINE = (
    '산업', '시장', '기업', '제조', '생산', '수출', '수입',
    '유통', '투자', '매출', '통계', '정책', '규제', '지원사업',
    '서비스', '사료', '간식', '펫푸드', '펫테크', '제품',
    '용품', '브랜드', '사업', '창업', '스타트업', '판매',
    '출시', '협약', '인력', '육성', '플랫폼', '개발',
    '가맹', '쇼핑', '매장', '보험', '특허',
)
_EVENT_HEADLINE = (
    '입양', '축제', '캠페인', '자선', '봉사', '문화행사', '박람회',
)


def classify_domestic(title, summary, publisher=''):
    """Return domestic_industry, domestic_supplement or None (not verified)."""
    title = (title or '').lower()
    summary = (summary or '').lower()
    topic = title + ' ' + summary
    if any(word in topic for word in BLOCKED_KO):
        return None
    anchored = any(word in title for word in _DOMESTIC_HEADLINE_PET)
    if not anchored:
        # Limited exception for official civic/government releases with a
        # clearly identified pet topic in their summary.
        if publisher not in _GOVERNMENT_FEEDS or not any(
            word in summary for word in _GOVERNMENT_SUMMARY_PET
        ):
            return None
    # Accidents/crimes are excluded even if their teaser mentions an industry.
    if any(word in title for word in _INCIDENT_HEADLINE):
        return None
    # Adoption/culture are supplemental unless the headline also describes a
    # concrete commercial/policy/industrial action.
    has_industry_headline = any(word in title for word in _INDUSTRY_HEADLINE)
    if any(word in title for word in _EVENT_HEADLINE) and not has_industry_headline:
        return 'domestic_supplement'
    if has_industry_headline:
        return 'domestic_industry'
    # Some short English headlines need the actual RSS summary for context;
    # official policy releases can also use an abbreviated headline.
    if (title.strip() in {'pet food', 'petfood', 'pet tech', 'pet industry'}
            or publisher in _GOVERNMENT_FEEDS):
        if any(word in summary for word in INDUSTRY_KO):
            return 'domestic_industry'
    return 'domestic_supplement'



def parse_feed(xml_bytes, source, start, end):
    """Return eligible RSS/Atom candidate records without claiming verified publication."""
    root = ET.fromstring(xml_bytes)
    result = []
    for entry in root.iter():
        if entry.tag.rsplit('}', 1)[-1] not in ('item', 'entry'):
            continue
        title = _clean(_field(entry, {'title'}))
        url = _source_link(entry, source)
        timestamp = _source_date(entry, source)
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
            category = classify_domestic(title, summary, source.get('publisher', ''))
            if category is None:
                continue
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
            entries = [
                e for e in ET.fromstring(response.content).iter()
                if e.tag.rsplit('}', 1)[-1] in ('item', 'entry')
            ]
            dates = [
                _source_date(e, feed)
                for e in entries
            ]
            valid_dates = [d for d in dates if d is not None]
            in_window = sum(1 for d in valid_dates if start <= d < end)
            sample = next(
                (e for e, d in zip(entries, dates)
                 if d is not None and start <= d < end),
                entries[0] if entries else None
            )
            sample_url = _item_link(sample) if sample is not None else ''
            logging.warning(
                "RSS DIAG %s: total=%d dated=%d in_window=%d "
                "newest=%s oldest=%s host=%s scheme=%s",
                feed['publisher'],
                len(entries),
                len(valid_dates),
                in_window,
                max(valid_dates).isoformat() if valid_dates else "none",
                min(valid_dates).isoformat() if valid_dates else "none",
                urlparse(sample_url).hostname or "none",
                urlparse(sample_url).scheme or "none"          
            )
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
