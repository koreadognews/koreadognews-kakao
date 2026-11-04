"""Korea Dog News unified edition producer / existing Kakao sender.

One immutable issue (issue_date, KST) -> JSON, TXT, HTML and legacy URLs.
This program does not claim that HTTP 200 alone verifies a publication time.
"""
import argparse
import datetime as dt
import html
from html.parser import HTMLParser
import json
import logging
import os
from pathlib import Path
import re
import time
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests

TZ = ZoneInfo('Asia/Seoul')
ROOT = Path(__file__).parent
SITE = ROOT / 'docs'
STORED = ROOT / 'tokens.json'
UA = 'KoreaDogNewsNewsletterBot/2.0 (+https://www.koreadognews.co.kr)'
ROOM = 'https://open.kakao.com/o/g2xkhE1b'
INTRO = '아래 뉴스는 ‘애견신문사’에서 국내외 미디어를 통해 게재된 반려동물 산업에 관련된 최신 기사를 간추린 소식지입니다.'


def issue_day():
    return dt.datetime.now(TZ).date()


def target_date():  # Legacy name retained: used by send_once.py to identify news date
    return issue_day() - dt.timedelta(days=1)


def bounds(issue):
    end = dt.datetime.combine(issue, dt.time(hour=6), TZ)
    start = end - dt.timedelta(days=3 if issue.weekday() == 0 else 1)
    return start, end


def is_retriable_rate_limit(error):
    message = str(error).lower()
    return not any(s in message for s in ('insufficient_quota', 'no credits remaining', 'billing', 'quota exceeded'))


def fetch_research(issue, start=None, end=None):
    if start is None or end is None:
        start, end = bounds(issue)
    from openai import OpenAI, APIConnectionError, APITimeoutError, InternalServerError, RateLimitError

    client = OpenAI(
        max_retries=0,
        timeout=120.0
    )

    prompt = f'''Prepare candidate items for Korea Dog News industry digest dated {issue.isoformat()}.
First publication interval [inclusive start, exclusive end] in Asia/Seoul:
{start.isoformat()} to {end.isoformat()}.
For discovery use Korean original publishers and government/local governments, RDA, KREI, aT, Statistics Korea, KCS, research institutes, corporate releases, and BigKinds where publicly accessible. BigKinds is a discovery aid, NOT the origin URL. Search globalpetindustry.com, petfoodindustry.com and trade press for overseas industry coverage.
Sections:
- domestic_industry: up to 15 original Korean industry items. Focus on pet food/goods, production, distribution, ecommerce, statistics, investment, exports, nonmedical tech and policy. Avoid veterinary clinical content, pharmaceuticals and devices. koreadognews.co.kr no more than 2.
- domestic_supplement: ONLY if industry items are under 15; Korean culture/lifestyle/event-news originally published in the same interval.
- overseas: up to 5 verified overseas pet INDUSTRY items. Labeling [해외] is performed by software.
- events: independently announced upcoming pet exhibitions/cultural events held in Korea between {issue.isoformat()} and one month later, regardless of original announcement date. Include name, start_date, end_date, direct announcement URL; do not invent.
Return ONLY JSON {{"domestic_industry":[{{"title":"...","url":"https://original-article...","publisher":"...","published_at":"YYYY-MM-DDTHH:MM:SS+09:00","summary":"one sentence"}}],"domestic_supplement":[],"overseas":[],"events":[{{"name":"...","start_date":"YYYY-MM-DD","end_date":"YYYY-MM-DD","url":"https://..."}}]}}.
Every news item MUST have a direct original article URL and first publication timestamp including timezone; omit unknown-time candidates rather than inventing. Event announcement dates are not bounded by the news interval. Do NOT pad counts. Do not fabricate dates, original URLs, facts or issue number. Return no markdown.'''
    for attempt in range(3):
        try:
            r = client.responses.create(model=os.getenv('OPENAI_MODEL', 'gpt-5-mini'), tools=[{'type': 'web_search'}], input=prompt)
            break
        except (APIConnectionError, APITimeoutError, InternalServerError, RateLimitError) as e:
            if isinstance(e, RateLimitError) and not is_retriable_rate_limit(e):
                raise
            if attempt == 2:
                raise
            delay = 20 if attempt == 0 else 60
            logging.warning('OpenAI temporary error (%s); retry in %ss', type(e).__name__, delay)
            time.sleep(delay)
    match = re.search(r'\{.*\}', r.output_text.strip(), re.S)
    if not match:
        raise ValueError('OpenAI did not return JSON')
    return json.loads(match.group(0))


class MetadataReader(HTMLParser):
    def __init__(self):
        super().__init__()
        self.values = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'meta':
            k = (a.get('property') or a.get('name') or a.get('itemprop') or '').lower()
            if k in {'article:published_time', 'datepublished', 'datecreated', 'pubdate', 'publish_date', 'sailthru.date'}:
                self.values.append(a.get('content', ''))
        if tag == 'time' and a.get('datetime'):
            self.values.append(a['datetime'])


def parse_aware_timestamp(s):
    if not isinstance(s, str):
        return None
    try:
        value = dt.datetime.fromisoformat(s.strip().replace('Z', '+00:00'))
        return value.astimezone(TZ) if value.tzinfo and value.utcoffset() is not None else None
    except ValueError:
        return None


def source_metadata_times(page):
    reader = MetadataReader()
    reader.feed(page)
    vals = list(reader.values)
    # JSON-LD published metadata. This is a fallback when meta tags are absent.
    vals.extend(re.findall(r'"(?:datePublished|dateCreated)"\s*:\s*"([^"\\]+)"', page, re.I))
    return [t for x in vals if (t := parse_aware_timestamp(x)) is not None]


def get_source(url):
    host = urlparse(url).hostname
    if urlparse(url).scheme != 'https' or not host or host in {'news.google.com', 'search.naver.com', 'www.google.com'}:
        raise ValueError('Not a direct HTTPS publisher URL')
    r = requests.get(url, timeout=16, headers={'User-Agent': UA}, allow_redirects=True)
    r.raise_for_status()
    if urlparse(r.url).scheme != 'https':
        raise ValueError('Source redirected to non-HTTPS')
    return r.text


def validate_news(candidates, start, end, limit, seen, accepted, korean=False, category=''):
    out = []
    if not isinstance(candidates, list):
        return out
    blocked = ('수의사', '임상시험', '동물병원', '동물용 의약품', '동물용 의료기기')
    for item in candidates:
        if len(out) >= limit:
            break
        try:
            title, url = item['title'].strip(), item['url'].strip()
            publisher, summary = item['publisher'].strip(), item['summary'].strip()
            claimed = parse_aware_timestamp(item['published_at'])
            if not (title and publisher and summary and claimed and start <= claimed < end):
                continue
            if korean and category == 'domestic_industry' and any(x in title + summary for x in blocked):
                continue
            key = (urlparse(url).hostname or '', urlparse(url).path.rstrip('/'))
            if key in seen:
                continue
            source = get_source(url)
            published = source_metadata_times(source)
            # Require the claimed first-published time to correspond to source metadata,
            # and reject contradictory dated source metadata when possible.
            matched = [x for x in published if abs((x - claimed).total_seconds()) <= 120]
            if not matched or not (start <= matched[0] < end):
                logging.warning('Source timestamp not verified: %s', url)
                continue
            if korean and 'koreadognews.co.kr' in (key[0] or ''):
                if sum('koreadognews.co.kr' in (urlparse(x['url']).hostname or '') for x in accepted) >= 2:
                    continue
            record = {'title':title, 'url':url, 'publisher':publisher,
                      'published_at':matched[0].isoformat(), 'summary':summary,
                      'category':category, 'verification':'source_metadata'}
            seen.add(key)
            accepted.append(record)
            out.append(record)
        except (KeyError, TypeError, ValueError, requests.RequestException) as e:
            logging.warning('Excluded unverified news candidate: %s', type(e).__name__)
    return out


def month_later(day):
    y, m = day.year, day.month + 1
    if m == 13:
        y, m = y + 1, 1
    import calendar
    return dt.date(y, m, min(day.day, calendar.monthrange(y, m)[1]))


def validate_events(candidates, issue):
    out, seen = [], set()
    for item in candidates if isinstance(candidates, list) else []:
        try:
            name, url = item['name'].strip(), item['url'].strip()
            first = dt.date.fromisoformat(item['start_date'])
            last = dt.date.fromisoformat(item.get('end_date') or item['start_date'])
            if not (name and issue <= last and first <= month_later(issue) and first <= last):
                continue
            if (name, url) in seen:
                continue
            page = get_source(url)
            # Confirm dates appear in the announcement; otherwise do not include.
            condensed = re.sub(r'\s+', '', re.sub('<[^>]+>', ' ', page))
            date_strings = (first.isoformat(), first.strftime('%Y.%m.%d'), first.strftime('%Y/%m/%d'), first.strftime('%m.%d'))
            if not any(x in condensed for x in date_strings):
                continue
            seen.add((name, url))
            out.append({'name':name, 'start_date':first.isoformat(), 'end_date':last.isoformat(), 'url':url})
        except (KeyError, ValueError, TypeError, requests.RequestException):
            continue
    return out


def create_issue(issue, candidates=None):
    start, end = bounds(issue)
    if candidates is None:
        from research_split import fetch_research_split
        candidates = fetch_research_split(issue, start, end)
    logging.warning(
        "Research candidates: domestic=%d, supplement=%d, overseas=%d, events=%d",
        len(candidates.get("domestic_industry", [])),
        len(candidates.get("domestic_supplement", [])),
        len(candidates.get("overseas", [])),
        len(candidates.get("events", [])),
    )
    seen, accepted = set(), []
    domestic = validate_news(candidates.get('domestic_industry', []), start, end, 15, seen, accepted, korean=True, category='domestic_industry')
    supplement = validate_news(candidates.get('domestic_supplement', []), start, end, 15-len(domestic), seen, accepted, korean=True, category='domestic_supplement')
    overseas = validate_news(candidates.get('overseas', []), start, end, 5, seen, accepted, category='overseas')
    events = validate_events(candidates.get('events', []), issue)
    logging.warning(
        "Verified results: domestic=%d, supplement=%d, overseas=%d, events=%d",
        len(domestic), len(supplement), len(overseas), len(events)
    )
    if not (domestic or supplement or overseas):
        raise RuntimeError('No news with verified first publication time; no edition sent')
    return {'schema_version': 1, 'issue_date':issue.isoformat(), 'collection_start':start.isoformat(),
            'collection_end_exclusive':end.isoformat(), 'generated_at':dt.datetime.now(TZ).isoformat(),
            'issue_number':'확인 중', 'domestic_industry':domestic,
            'domestic_supplement':supplement, 'overseas':overseas, 'events':events}


def format_article(item, index, overseas=False):
    title = f"[해외] {item['title']}" if overseas else re.sub(r'^\[[^\]]+\]\s*', '', item['title'])
    return f"{index:02d}. {title}\n{item['url']}\n{item['summary']}\n"


def render_text(issue):
    date = dt.date.fromisoformat(issue['issue_date'])
    start = dt.datetime.fromisoformat(issue['collection_start']).astimezone(TZ)
    end = dt.datetime.fromisoformat(issue['collection_end_exclusive']).astimezone(TZ)
    n = issue['issue_number']
    parts = [f'애견신문사 | 반려동물산업 소식지',
             f"발행: {date.isoformat()} | 발행 호수: {n}",
             f"집계기간: {start:%Y.%m.%d %H:%M} ~ {end:%Y.%m.%d %H:%M} 직전 (KST)",
             INTRO, f'펫비즈니스방 참가하기 · 반려동물산업 관계자 단톡방\n{ROOM}',
             '━━━━━━━━━━━━━━━━━━\n국내 주요 뉴스\n━━━━━━━━━━━━━━━━━━']
    domestic = issue['domestic_industry'] + issue['domestic_supplement']
    if domestic:
        parts.extend(format_article(x, i) for i, x in enumerate(domestic, 1))
    else:
        parts.append('확인된 국내 기사가 없습니다.')
    parts.append('━━━━━━━━━━━━━━━━━━\n해외 반려동물 산업뉴스\n━━━━━━━━━━━━━━━━━━')
    if issue['overseas']:
        parts.extend(format_article(x, i, True) for i,x in enumerate(issue['overseas'], 1))
    else:
        parts.append('집계기간 내 검증된 해외 산업뉴스가 없습니다.')
    parts.append('━━━━━━━━━━━━━━━━━━\n향후 한 달 펫박람회·문화행사 일정\n━━━━━━━━━━━━━━━━━━')
    if issue['events']:
        for e in issue['events']:
            a,b = dt.date.fromisoformat(e['start_date']),dt.date.fromisoformat(e['end_date'])
            day = a.strftime('%m.%d') if a == b else f"{a:%m.%d}~{b:%m.%d}"
            parts.append(f"{e['name']} | {day}\n{e['url']}")
    else:
        parts.append('향후 한 달 내 확인된 행사 없음')
    parts.append(f'애견신문사 | https://www.koreadognews.co.kr | newsdog@naver.com | 광고·제보 02-1566-5663')
    parts.append(f'펫비즈니스방 참가하기 · 반려동물산업 관계자 단톡방\n{ROOM}')
    return '\n\n'.join(parts) + '\n'


def render(issue):
    SITE.mkdir(parents=True, exist_ok=True)
    issued = dt.date.fromisoformat(issue['issue_date'])
    day = issued - dt.timedelta(days=1)
    folder = SITE / 'issues'
    folder.mkdir(parents=True, exist_ok=True)
    plaintext = render_text(issue)
    payload = dict(issue)
    payload['text'] = plaintext
    (folder / f'{issued}.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    (folder / f'{issued}.txt').write_text(plaintext, encoding='utf-8')
    # Keep the legacy website path (news date) for the existing daily.yml URL verifier and Kakao message.
    links = re.compile(r'(https://[^\s<>]+)')
    body = ''.join(f'<a href="{html.escape(x, quote=True)}" target="_blank" rel="noopener noreferrer">{html.escape(x)}</a>' if x.startswith('https://') else html.escape(x) for x in links.split(plaintext))
    page = '''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>반려동물산업 소식지</title><style>body{font-family:system-ui,sans-serif;max-width:760px;margin:24px auto;padding:0 15px;color:#273442}pre{white-space:pre-wrap;word-break:break-word;font:inherit;line-height:1.65}button{padding:10px 14px;border:1px solid #aaa;border-radius:8px;background:#fff;cursor:pointer}</style></head><body><h1>애견신문사 | 반려동물산업 소식지</h1><button type="button" onclick="navigator.clipboard.writeText(document.getElementById('issue').innerText)">▣ 소식지 전문 복사</button><pre id="issue">''' + body + '''</pre></body></html>'''
    # preserve verifier that searches for exact '기사 기준일 YYYY-MM-DD'
    page = page.replace('</body>', f'<p>기사 기준일 {day.isoformat()}</p></body>')
    path = SITE / f'{day.isoformat()}.html'
    path.write_text(page, encoding='utf-8')
    (SITE/'index.html').write_text(page, encoding='utf-8')
    (SITE/f'{day.isoformat()}.json').write_text(json.dumps({'articles': issue['domestic_industry']+issue['domestic_supplement']+issue['overseas']}, ensure_ascii=False, indent=2), encoding='utf-8')
    return path


def refresh_access_token():
    payload = {'grant_type':'refresh_token', 'client_id':os.environ['KAKAO_REST_API_KEY'],
               'refresh_token':os.environ['KAKAO_REFRESH_TOKEN']}
    if os.getenv('KAKAO_CLIENT_SECRET'):
        payload['client_secret'] = os.environ['KAKAO_CLIENT_SECRET']
    r = requests.post('https://kauth.kakao.com/oauth/token', data=payload, timeout=20)
    r.raise_for_status()
    result = r.json()
    updated = result.get('refresh_token')
    if updated and updated != payload['refresh_token']:
        STORED.write_text(json.dumps({'refresh_token':updated}), encoding='utf-8')
        STORED.chmod(0o600)
        logging.warning('Kakao refresh token rotated; sync required')
    return result['access_token']


def send_kakao(text, url):
    access = refresh_access_token()
    template = {'object_type':'text','text':text[:180], 'link':{'web_url':url,'mobile_web_url':url}, 'button_title':'소식지 전문 보기'}
    r = requests.post('https://kapi.kakao.com/v2/api/talk/memo/default/send',
                      headers={'Authorization':f'Bearer {access}'},
                      data={'template_object':json.dumps(template,ensure_ascii=False)},timeout=20)
    r.raise_for_status()
    if r.json().get('result_code') != 0:
        raise RuntimeError('Kakao send failed: nonzero result_code')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--send', action='store_true')
    parser.add_argument('--send-only', action='store_true')
    parser.add_argument('--date', help='Issue date YYYY-MM-DD (KST), defaults today')
    parser.add_argument('--input', help='Editor-reviewed candidate JSON; do not use AI')
    parser.add_argument('--skip-http-check', action='store_true', help='Legacy, ignored: published-time checks are mandatory')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    issue = dt.date.fromisoformat(args.date) if args.date else issue_day()
    saved = SITE/'issues'/f'{issue}.json'
    if args.send_only:
        if not saved.exists():
            raise FileNotFoundError(f'Canonical edition missing: {saved}')
        record = json.loads(saved.read_text(encoding='utf-8'))
        path = SITE/f'{issue - dt.timedelta(days=1)}.html'
        if not path.exists():
            raise FileNotFoundError(f'Published HTML missing: {path}')
    else:
        if args.input:
            # Editorial source still goes through the same timestamp and URL verification.
            candidates = json.loads(Path(args.input).read_text(encoding='utf-8'))
            record = create_issue(issue, candidates=candidates)
        else:
            record = create_issue(issue)
        path = render(record)
    base = os.environ['PUBLIC_BASE_URL'].rstrip('/')
    url = f'{base}/{path.name}'
    summary = f"애견신문사 | 반려동물산업 소식지\n{issue.isoformat()}\n국내 {len(record['domestic_industry'])+len(record['domestic_supplement'])}건 · 해외 {len(record['overseas'])}건 · 행사 {len(record['events'])}건\n소식지 전문 보기"
    print(f'Canonical issue: {base}/issues/{issue}.json', flush=True)
    print(f'KAKAO_LINK_URL: {url}', flush=True)
    if args.send:
        send_kakao(summary,url)
        print('Kakao API reported successful send', flush=True)
    else:
        print('DRY RUN: no Kakao message sent', flush=True)


if __name__ == '__main__':
    main()
