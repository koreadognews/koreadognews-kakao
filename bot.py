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
    """Make a single canonical, newspaper-style web edition for both channels."""
    SITE.mkdir(exist_ok=True)
    path = SITE / f'{date.isoformat()}.html'
    issue_date = date + dt.timedelta(days=1)
    start, end = collection_window(issue_date)
    weekdays = ('월요일', '화요일', '수요일', '목요일', '금요일', '토요일', '일요일')
    issue_label = f'{issue_date.year}년 {issue_date.month}월 {issue_date.day}일 {weekdays[issue_date.weekday()]}'
    room = 'https://open.kakao.com/o/g2xkhE1b'

    def esc(value):
        return html.escape(str(value), quote=True)

    def articles_html(items, heading, overseas=False):
        sections = [f'<h2>{esc(heading)}</h2>']
        for number, article in enumerate(items, 1):
            title = ('[해외] ' if overseas else '') + article['title']
            try:
                stamp = dt.datetime.fromisoformat(article['published_at']).astimezone(TZ).strftime('%m.%d %H:%M')
            except (ValueError, TypeError, KeyError):
                stamp = '게시시각 확인 필요'
            sections.append(
                f'<article class="news"><h3>{number:02d}. {esc(title)}</h3>'
                f'<div class="meta">{esc(article["publisher"])} | {esc(stamp)}</div>'
                f'<p>{esc(article["summary"])}</p>'
                f'<a href="{esc(article["url"])}" target="_blank" '
                f'rel="noopener noreferrer">기사 원문 보기</a></article>'
            )
        if not items:
            sections.append('<p>확인된 기사가 없습니다.</p>')
        return ''.join(sections)

    domestic = articles_html(data['domestic'], '국내 반려동물 산업뉴스')
    international = articles_html(data['international'], '해외 반려동물 산업뉴스', overseas=True)
    # Existing data lacks a verified 'supplementary' classification: do not guess.
    events = ['<h2>향후 한 달 펫박람회·문화행사 일정</h2>']
    if data['events']:
        events.append('<table><thead><tr><th>행사명</th><th>개최일</th></tr></thead><tbody>')
        for event in data['events']:
            first = dt.date.fromisoformat(event['start_date'])
            last = dt.date.fromisoformat(event['end_date'])
            when = first.strftime('%m.%d')
            if last != first:
                when += '~' + last.strftime('%m.%d')
            events.append(f'<tr><td><a href="{esc(event["url"])}" target="_blank" '
                          f'rel="noopener noreferrer">{esc(event["title"])}</a></td>'
                          f'<td>{esc(when)}</td></tr>')
        events.append('</tbody></table>')
    else:
        events.append('<p>향후 한 달 내 확인된 행사 없음</p>')
    # The original date-based issue_number() is not editorially verified.
    verified = os.getenv('VERIFIED_ISSUE_NUMBER', '').strip()
    issue_no = f' · 제{verified}호' if verified.isdecimal() else ' · 호수 확인 중'
    intro = ('아래 뉴스는 ‘애견신문사’에서 국내외 미디어와 정부기관·지자체·연구기관을 '
             '통해 공개된 반려동물 산업 관련 최신 정보를 간추린 소식지입니다.')
    page = f'''<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>애견신문사 · 반려동물산업 소식지 {issue_date}</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;background:#f5f7f9;color:#22313d;
font:16px/1.7 system-ui,'Malgun Gothic',sans-serif}}
main{{max-width:790px;margin:24px auto;padding:36px;background:#fff}}
.brand{{font-size:13px;font-weight:700;letter-spacing:.07em;color:#34647c}}
h1{{font-size:29px;margin:5px 0 8px}}h2{{font-size:21px;border-top:2px solid #334c5a;
padding-top:16px;margin-top:35px}}h3{{font-size:17px;line-height:1.5;margin:0 0 7px}}
.meta{{font-size:13px;color:#647582}}.news{{padding:20px 0;border-bottom:1px solid #dfe6eb}}
.news p{{margin:10px 0}}a{{color:#14618b;text-decoration:none;overflow-wrap:anywhere}}
a:hover{{text-decoration:underline}}.room{{padding:12px 16px;background:#f1f7fa;
margin:20px 0;border-left:3px solid #286789}}.room strong{{display:block}}
table{{width:100%;border-collapse:collapse;font-size:14px}}th,td{{padding:11px 8px;
border-bottom:1px solid #dfe6eb;text-align:left}}th{{background:#f2f6f9}}
footer{{border-top:2px solid #334c5a;margin-top:35px;padding-top:20px;font-size:14px}}
.copy{{text-align:right;margin:10px 0}}button{{padding:9px 12px;border:1px solid #b5cad6;
border-radius:6px;background:#fff;color:#14587e;cursor:pointer}}
@media(max-width:650px){{main{{margin:0;padding:22px 17px}}h1{{font-size:25px}}}}
</style></head><body><main id="edition">
<div class="brand">애견신문사 · KOREA DOG NEWS</div>
<h1>반려동물산업 소식지</h1>
<div class="meta">{esc(issue_label)}{esc(issue_no)}</div>
<div class="meta">뉴스 집계: {start:%Y.%m.%d %H:%M} ~ {end:%Y.%m.%d %H:%M} 미만 (KST)</div>
<p>기사 기준일 {date}</p>
<div class="copy"><button type="button" onclick="copyEdition()">▣ 소식지 전문 복사</button></div>
<p>{esc(intro)}</p>
<div class="room"><strong>펫비즈니스방 참가하기</strong>
반려동물산업 관계자 단톡방<br><a href="{room}">{room}</a></div>
<h2>편집 기준 안내</h2><p>국내 {len(data['domestic'])}건 · 해외 {len(data['international'])}건.
집계기간에 해당하는 확인 자료만 수록하고 목표 수를 채우기 위해 과거 기사를 추가하지 않았습니다.</p>
{domestic}{international}{''.join(events)}
<footer><strong>애견신문사 · KOREA DOG NEWS</strong><br>
홈페이지: <a href="https://www.koreadognews.co.kr">www.koreadognews.co.kr</a><br>
광고·제보 02-1566-5663<br>newsdog@naver.com
<div class="room"><strong>펫비즈니스방 참가하기</strong>
반려동물산업 관계자 단톡방<br><a href="{room}">{room}</a></div>
</footer></main><script>
function copyEdition(){{
 const el=document.getElementById('edition').cloneNode(true);
 el.querySelector('.copy')?.remove();
 el.querySelectorAll('a').forEach(a=>{{
   a.replaceWith(document.createTextNode((a.textContent||'').trim()+' '+a.href));
 }});
 el.style.position='fixed';el.style.left='-10000px';el.style.top='0';
 document.body.appendChild(el);
 const text=(el.innerText||el.textContent).trim();
 el.remove();
 if(navigator.clipboard&&window.isSecureContext){{
 navigator.clipboard.writeText(text).then(()=>alert('소식지 전문을 복사했습니다.'),
 ()=>alert('복사에 실패했습니다.'));}}
 else{{alert('복사를 지원하지 않는 환경입니다.');}}
}}
</script></body></html>'''
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
