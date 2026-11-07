"""Korean Pet Industry Daily: 06:00 KST editorial window, verified link digest.

Preserves the bot.py CLI used by daily.yml and send_once.py.
"""
import argparse
import datetime as dt
import html
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
UA = 'KoreaDogNewsNewsletterBot/1.0 (+https://www.koreadognews.co.kr)'


def target_date():
    return dt.datetime.now(TZ).date() - dt.timedelta(days=1)


def issue_number(date):
    origin = dt.date.fromisoformat(os.getenv('ISSUE_BASE_DATE', '2026-11-02'))
    base = int(os.getenv('ISSUE_BASE', '1044'))
    return base + (date - origin).days


def collection_window(issue_date):
    """End is exclusive; on Monday begin at the previous Friday 06:00 KST."""
    end = dt.datetime.combine(issue_date, dt.time(6), tzinfo=TZ)
    days = 3 if issue_date.weekday() == 0 else 1
    return end - dt.timedelta(days=days), end


def fetch_research(date):
    from openai import (OpenAI, APIConnectionError, APITimeoutError,
                        InternalServerError, RateLimitError)
    issue_date = date + dt.timedelta(days=1)
    start, end = collection_window(issue_date)
    prompt = f'''You are the fact-checking research editor for Korean Pet Industry Daily.
Use live web search for original reports FIRST published in this Asia/Seoul window:
{start.isoformat()} inclusive to {end.isoformat()} exclusive.
For Monday, Friday 06:00 through Monday 06:00; otherwise previous day 06:00
through issue day 06:00. Search indexing and revision times do not count.

Find UP TO 15 distinct Korean domestic pet industry stories and UP TO 5 distinct
foreign pet industry stories, never padding or fabricating results. Prioritize
business, manufacturing, food, technology, distribution, investment, trade,
exports, insurance, government agencies, municipalities and research institutes.
Use official government/institute materials and BigKinds where accessible.
No more than 2 stories from koreadognews.co.kr. Minimize clinical veterinary
stories; avoid veterinary drugs and medical devices. Only when industry stories
are scarce, use relevant culture/lifestyle items as lower-priority domestic
stories. Events are NOT industry stories. Source articles must be distinct.

For each story supply title, original HTTPS article URL, publisher, a neutral
short Korean summary, and its source-supported first publication timestamp
including a timezone offset (published_at, ISO 8601). Omit stories for which
the original publication TIME is not reliably available. Never invent a time.
For overseas articles provide a Korean headline but a real foreign source URL.

Separately list upcoming pet fairs and pet culture events that take place from
{issue_date.isoformat()} through {(issue_date + dt.timedelta(days=30)).isoformat()}
(inclusive). Event announcements can have been published earlier. Require the
organizer's original HTTPS page, verified title, start_date and end_date.

Return ONLY JSON, exactly this structure:
{{"domestic":[{{"title":"...","url":"https://...","publisher":"...",
"published_at":"YYYY-MM-DDTHH:MM:SS+09:00","summary":"..."}}],
"international":[{{"title":"...","url":"https://...","publisher":"...",
"published_at":"YYYY-MM-DDTHH:MM:SS+09:00","summary":"..."}}],
"events":[{{"title":"...","start_date":"YYYY-MM-DD",
"end_date":"YYYY-MM-DD","url":"https://..."}}]}}'''
    client = OpenAI(max_retries=0)
    for attempt in range(3):
        try:
            response = client.responses.create(
                model=os.getenv('OPENAI_MODEL', 'gpt-5-mini'),
                tools=[{'type': 'web_search'}], input=prompt)
            break
        except (APIConnectionError, APITimeoutError,
                InternalServerError, RateLimitError) as error:
            if isinstance(error, RateLimitError):
                detail = str(error).lower()
                if any(x in detail for x in
                       ('insufficient_quota', 'no credits remaining',
                        'billing', 'quota exceeded')):
                    raise
            if attempt == 2:
                raise
            delay = 20 if attempt == 0 else 60
            logging.warning('Temporary API error; retrying in %s seconds', delay)
            time.sleep(delay)
    match = re.search(r'\{.*\}', response.output_text.strip(), re.S)
    if not match:
        raise ValueError('AI returned no JSON: sending cancelled')
    return json.loads(match.group(0))


def parse_published_at(value):
    if not isinstance(value, str):
        raise ValueError('Missing source publication timestamp')
    result = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError('Publication timestamp must include timezone')
    return result.astimezone(TZ)


def valid_source(url, check_http=True):
    if not isinstance(url, str):
        return False
    parsed = urlparse(url)
    if parsed.scheme != 'https' or not parsed.hostname:
        return False
    if parsed.hostname.lower() in {
        'news.google.com', 'www.google.com', 'google.com',
        'search.naver.com', 'www.bigkinds.or.kr'
    }:
        return False
    if not check_http:
        return True
    try:
        response = requests.get(url, timeout=12,
                                headers={'User-Agent': UA}, allow_redirects=True)
        return response.status_code < 400 and response.url.startswith('https://')
    except requests.RequestException:
        return False


def validate(data, date, check_http=True):
    """Validate time window, category caps, article URLs, event date ranges."""
    if not isinstance(data, dict):
        raise ValueError('Data must be a JSON object')
    issue_date = date + dt.timedelta(days=1)
    start, end = collection_window(issue_date)
    output = {'domestic': [], 'international': [], 'events': []}
    seen, own_count = set(), 0
    for category, limit in (('domestic', 15), ('international', 5)):
        candidates = data.get(category, [])
        if not isinstance(candidates, list):
            raise ValueError(f'{category} must be a list')
        for item in candidates:
            try:
                title, summary = item['title'].strip(), item['summary'].strip()
                publisher, url = item['publisher'].strip(), item['url'].strip()
                published = parse_published_at(item['published_at'])
                if not title or not summary or not publisher:
                    continue
                if not (start <= published < end) or not valid_source(url, check_http):
                    continue
                host = urlparse(url).hostname.lower()
                key = (host, urlparse(url).path.rstrip('/'))
                if key in seen:
                    continue
                own = host == 'koreadognews.co.kr' or host.endswith('.koreadognews.co.kr')
                if own and own_count >= 2:
                    continue
                if category == 'domestic' and any(term in title for term in
                    ('펫페어', '박람회 개최', '축제 개최')):
                    continue
                seen.add(key)
                if own:
                    own_count += 1
                output[category].append({
                    'title': title, 'summary': summary, 'publisher': publisher,
                    'url': url, 'published_at': published.isoformat(),
                    'publication_date': published.date().isoformat(),
                })
                if len(output[category]) == limit:
                    break
            except (KeyError, ValueError, TypeError, AttributeError) as error:
                logging.warning('Invalid %s article: %s', category, error)

    upcoming_end = issue_date + dt.timedelta(days=30)
    candidates = data.get('events', [])
    if not isinstance(candidates, list):
        raise ValueError('events must be a list')
    event_seen = set()
    for item in candidates:
        try:
            title = item['title'].strip()
            first = dt.date.fromisoformat(item['start_date'])
            last = dt.date.fromisoformat(item.get('end_date') or item['start_date'])
            url = item['url'].strip()
            if not title or last < first or last < issue_date or first > upcoming_end:
                continue
            if not valid_source(url, check_http):
                continue
            key = (title.casefold(), first, last)
            if key in event_seen:
                continue
            event_seen.add(key)
            output['events'].append({
                'title': title, 'start_date': str(first),
                'end_date': str(last), 'url': url,
            })
        except (KeyError, ValueError, TypeError, AttributeError) as error:
            logging.warning('Invalid event: %s', error)
    output['events'].sort(key=lambda item: (item['start_date'], item['title']))
    if not output['domestic'] and not output['international']:
        raise RuntimeError('No in-window source-linked articles: cancelled')
    logging.info('Validated domestic=%d international=%d events=%d',
                 len(output['domestic']), len(output['international']),
                 len(output['events']))
    return output


def render(data, date):
    SITE.mkdir(exist_ok=True)
    path = SITE / f'{date.isoformat()}.html'
    issue_date = date + dt.timedelta(days=1)
    start, end = collection_window(issue_date)

    def article_section(articles, title, overseas=False):
        items = ''
        for article in articles:
            headline = ('[해외] ' if overseas else '') + article['title']
            items += (f'<li><a href="{html.escape(article["url"], quote=True)}" '
                      f'rel="noopener noreferrer" target="_blank">'
                      f'{html.escape(headline)}</a>'
                      f'<small>{html.escape(article["publisher"])} · '
                      f'{html.escape(article["published_at"])}</small>'
                      f'<p>{html.escape(article["summary"])}</p></li>')
        return f'<h2>{title} ({len(articles)}건)</h2><ol>{items}</ol>'

    event_items = ''
    for event in data['events']:
        dates = event['start_date']
        if event['end_date'] != dates:
            dates += ' ~ ' + event['end_date']
        event_items += (f'<li><a href="{html.escape(event["url"], quote=True)}" '
                        f'rel="noopener noreferrer" target="_blank">'
                        f'{html.escape(event["title"])}</a> — '
                        f'{html.escape(dates)}</li>')
    events_html = ('<h2>향후 한 달간 반려동물 행사</h2><ul>' + event_items + '</ul>'
                   if event_items else '')
    domestic = article_section(data['domestic'], '국내 반려동물산업')
    overseas = article_section(data['international'], '해외 반려동물산업', True)
    page = f'''<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>반려동물산업 소식지 {issue_date}</title>
<style>body{{font-family:system-ui,'Malgun Gothic',sans-serif;max-width:760px;
margin:35px auto;padding:0 18px;color:#202a32;line-height:1.65}}
h1{{color:#16445c}}li{{margin:16px 0}}a{{color:#086390;font-weight:650}}
small{{display:block;color:#64748b}}footer{{border-top:1px solid #ddd;
padding-top:16px;color:#555}}</style></head><body>
<h1>🔹 반려동물산업 소식지 🔹</h1>
<p>발행 - {issue_number(date)}호 · {issue_date} / 기사 기준일 {date}</p>
<p>기사 수집 기간: {start:%Y-%m-%d %H:%M} 이상 ~ {end:%Y-%m-%d %H:%M} 미만 (한국시간)</p>
<p>한국애견신문에서 산업 보도, 정부 및 연구기관 자료를 선별했습니다.
원문과 행사 정보는 각 링크를 확인하세요.</p>
{domestic}{overseas}{events_html}
<footer>펫비즈니스방: <a href="https://open.kakao.com/o/g2xkhE1b">참가하기</a>
<br><a href="https://www.koreadognews.co.kr">한국애견신문</a> ·
newsdog@naver.com</footer></body></html>'''
    path.write_text(page, encoding='utf-8')
    (SITE / 'index.html').write_text(page, encoding='utf-8')
    return path


def refresh_access_token():
    key = os.environ['KAKAO_REST_API_KEY']
    secret = os.getenv('KAKAO_CLIENT_SECRET', '')
    refresh = os.environ['KAKAO_REFRESH_TOKEN']
    payload = {'grant_type': 'refresh_token', 'client_id': key,
               'refresh_token': refresh}
    if secret:
        payload['client_secret'] = secret
    response = requests.post('https://kauth.kakao.com/oauth/token',
                             data=payload, timeout=20)
    response.raise_for_status()
    result = response.json()
    if result.get('refresh_token') and result['refresh_token'] != refresh:
        logging.warning('Kakao refresh token rotated; update GitHub secret')
        STORED.write_text(json.dumps({'refresh_token': result['refresh_token']}),
                          encoding='utf-8')
        try:
            STORED.chmod(0o600)
        except OSError:
            pass
    return result['access_token']


def send_kakao(text, url):
    access = refresh_access_token()
    template = {'object_type': 'text', 'text': text[:180],
                'link': {'web_url': url, 'mobile_web_url': url},
                'button_title': '소식지 전문 보기'}
    response = requests.post(
        'https://kapi.kakao.com/v2/api/talk/memo/default/send',
        headers={'Authorization': f'Bearer {access}'},
        data={'template_object': json.dumps(template, ensure_ascii=False)},
        timeout=20)
    response.raise_for_status()
    if response.json().get('result_code') != 0:
        raise RuntimeError('Kakao send failed: nonzero result_code')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', default=target_date().isoformat(),
                        help='Previous calendar day of the KST issue YYYY-MM-DD')
    parser.add_argument('--input', help='Reviewed JSON file instead of AI search')
    parser.add_argument('--send', action='store_true')
    parser.add_argument('--send-only', action='store_true')
    parser.add_argument('--skip-http-check', action='store_true',
                        help='For offline tests only')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    date = dt.date.fromisoformat(args.date)
    saved = SITE / f'{date.isoformat()}.json'
    if args.input:
        raw = json.loads(Path(args.input).read_text(encoding='utf-8'))
    elif args.send_only:
        raw = json.loads(saved.read_text(encoding='utf-8'))
    else:
        raw = fetch_research(date)
    data = validate(raw, date,
                    check_http=not args.skip_http_check and not args.send_only)
    if args.send_only:
        path = SITE / f'{date.isoformat()}.html'
        if not path.exists():
            raise RuntimeError('HTML not yet generated')
    else:
        path = render(data, date)
        saved.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                         encoding='utf-8')
    base = os.environ.get('PUBLIC_BASE_URL', '').rstrip('/')
    if not base:
        raise RuntimeError('Set PUBLIC_BASE_URL before sending')
    link = f'{base}/{path.name}'
    issue_date = date + dt.timedelta(days=1)
    total = len(data['domestic']) + len(data['international'])
    top = (data['domestic'] + data['international'])[:2]
    highlights = ' / '.join(a['title'][:22] for a in top)
    message = (f'🔹반려동물산업 소식지🔹\n{issue_date} · '
               f'국내 {len(data["domestic"])}건 / 해외 {len(data["international"])}건\n'
               f'{highlights}\n전체 기사 보기')
    print(f'Generated {path}: {total} stories; {len(data["events"])} events; URL: {link}')
    if args.send:
        print('KAKAO_LINK_URL:', link, flush=True)
        send_kakao(message, link)
        print('Kakao API reported successful send')
    else:
        print('DRY RUN: no Kakao message sent')


if __name__ == '__main__':
    main()
