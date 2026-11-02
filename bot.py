"""Korean pet industry newsletter. Research -> guarded validation -> HTML -> Kakao self-message.

Never treat generated news as verified until manually or programmatically validated.
"""
import argparse
import datetime as dt
import html
import json
import logging
import os
from pathlib import Path
import re
import sys
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
    # Editorial issue numbers may skip non-publication days; edit if necessary.
    return base + (date - origin).days


def fetch_research(date):
    from openai import OpenAI
    client = OpenAI()
    prompt = f'''Search the live web for actual coverage first published on {date.isoformat()} in Korean local time. Search Korean and international pet business and specifically site:koreadognews.co.kr. Exclude all festivals, pet fairs, cultural events, lifestyle and celebrity articles. Seek actual industry news: investment, corporate results, M&A, food, veterinary medicine, insurance, AI, technology, distribution, exports and policy. Return up to 15 DISTINCT verified candidate articles. Each needs publisher, exact source article URL (never invented and avoid news index/search URLs), publication_date in YYYY-MM-DD, accurate Korean headline, and a one-sentence neutral summary. Return ONLY JSON object {{"articles":[{{"title":"...","url":"https://...","publisher":"...","publication_date":"YYYY-MM-DD","summary":"..."}}]}}. Do NOT pad to 15, NEVER invent source URLs, and drop uncertain dates. Prefer industry relevance. For an article from koreadognews.co.kr, include it only if it qualifies. Do not return markdown.''' 
    response = client.responses.create(model=os.getenv('OPENAI_MODEL', 'gpt-5-mini'), tools=[{'type':'web_search'}], input=prompt)
    raw = response.output_text.strip()
    match = re.search(r'\{.*\}', raw, re.S)
    if not match:
        raise ValueError('AI did not return a JSON object; no message sent')
    return json.loads(match.group(0))


def validate(data, date, check_http=True):
    articles = data.get('articles', [])
    if not isinstance(articles, list):
        raise ValueError('articles must be a list')
    output, seen = [], set()
    blocked = ('축제', '문화행사', '체험행사', '반려문화축제', '펫페어', '펫쇼 개최', '박람회 개최')
    for a in articles:
        try:
            title, url = a['title'].strip(), a['url'].strip()
            publisher = a['publisher'].strip()
            pub_date = dt.date.fromisoformat(a['publication_date'])
            summary = a['summary'].strip()
            parsed = urlparse(url)
            if pub_date != date or parsed.scheme != 'https' or not parsed.hostname or not publisher or not summary:
                continue
            if any(t in (title + ' ' + summary) for t in blocked):
                continue
            if parsed.hostname in {'news.google.com','www.google.com','google.com','search.naver.com'}:
                continue
            key = (parsed.netloc.lower(), parsed.path.rstrip('/'))
            if key in seen:
                continue
            if check_http:
                try:
                    r = requests.get(url, timeout=12, headers={'User-Agent': UA}, allow_redirects=True)
                    if r.status_code >= 400 or not r.url.startswith('https://'):
                        logging.warning('Unreachable article: %s [%s]', url, r.status_code)
                        continue
                except requests.RequestException as ex:
                    logging.warning('Could not verify article URL: %s (%s)', url, ex)
                    continue
            seen.add(key)
            output.append({'title':title,'url':url,'publisher':publisher,'publication_date':str(pub_date),'summary':summary})
        except (KeyError, ValueError, AttributeError, TypeError) as ex:
            logging.warning('Invalid candidate: %s', ex)
    return output[:15]


def render(articles, date):
    SITE.mkdir(exist_ok=True)
    path = SITE / f'{date.isoformat()}.html'
    # Source attribution/links rather than full text copying.
    items = ''.join(f'<li><a href="{html.escape(a["url"], quote=True)}" rel="noopener noreferrer" target="_blank">{html.escape(a["title"])}</a><small>{html.escape(a["publisher"])} · {html.escape(a["publication_date"])}</small><p>{html.escape(a["summary"])}</p></li>' for a in articles)
    page = f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>반려동물산업 소식지 {date}</title><style>body{{font-family:system-ui,'Malgun Gothic',sans-serif;max-width:760px;margin:35px auto;padding:0 18px;color:#202a32;line-height:1.65}}h1{{color:#16445c}}li{{margin:20px 0}}a{{color:#086390;font-weight:650}}small{{display:block;color:#64748b}}footer{{border-top:1px solid #ddd;padding-top:16px;color:#555}}</style></head><body><h1>🔹 반려동물산업 소식지 🔹</h1><p>발행 - {issue_number(date)}호 · {date + dt.timedelta(days=1)} / 기사 기준일 {date}</p><p>한국애견신문에서 국내외 보도 중 펫산업·경제·업계 동향 기사를 선별했습니다. 기사 본문은 원문 링크를 참고하세요.</p><ol>{items}</ol><footer>펫비즈니스방: <a href="https://open.kakao.com/o/g2xkhE1b">참가하기</a><br><a href="https://www.koreadognews.co.kr">한국애견신문</a> · newsdog@naver.com</footer></body></html>'''
    path.write_text(page, encoding='utf-8')
    (SITE / 'index.html').write_text(page, encoding='utf-8')
    return path


def refresh_access_token():
    key = os.environ['KAKAO_REST_API_KEY']
    secret = os.getenv('KAKAO_CLIENT_SECRET', '')
    # In GH Actions use secret KAKAO_REFRESH_TOKEN. If updated, notify operator to rotate secret.
    refresh = os.environ['KAKAO_REFRESH_TOKEN']
    payload = {'grant_type':'refresh_token', 'client_id':key, 'refresh_token':refresh}
    if secret:
        payload['client_secret'] = secret
    r = requests.post('https://kauth.kakao.com/oauth/token', data=payload, timeout=20)
    r.raise_for_status()
    result = r.json()
    if result.get('refresh_token') and result['refresh_token'] != refresh:
        logging.warning('Kakao rotated refresh token: update KAKAO_REFRESH_TOKEN secret promptly')
        # Never print token in CI logs. Save local only if user controls the environment.
        STORED.write_text(json.dumps({'refresh_token':result['refresh_token']}), encoding='utf-8')
        try: STORED.chmod(0o600)
        except OSError: pass
    return result['access_token']


def send_kakao(text, url):
    access = refresh_access_token()
    template = {'object_type':'text','text':text[:180], 'link':{'web_url':url,'mobile_web_url':url}, 'button_title':'소식지 전문 보기'}
    r = requests.post('https://kapi.kakao.com/v2/api/talk/memo/default/send',headers={'Authorization':f'Bearer {access}'},data={'template_object':json.dumps(template,ensure_ascii=False)},timeout=20)
    r.raise_for_status()
    if r.json().get('result_code') != 0:
        raise RuntimeError('Kakao send failed: nonzero result_code')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', default=target_date().isoformat(), help='KST news publication date YYYY-MM-DD')
    parser.add_argument('--input', help='Read manually reviewed JSON instead of live AI search')
    parser.add_argument('--send',action='store_true', help='Actually send Kakao message; otherwise dry run')
    parser.add_argument('--send-only',action='store_true',help='Use previously generated date JSON and HTML; do not re-research')
    parser.add_argument('--skip-http-check',action='store_true',help='For testing only, skip link HTTP validation')
    args=parser.parse_args()
    logging.basicConfig(level=logging.INFO,format='%(levelname)s %(message)s')
    date=dt.date.fromisoformat(args.date)
    saved = SITE / f'{date.isoformat()}.json'
    data=json.loads(Path(args.input).read_text(encoding='utf-8')) if args.input else (json.loads(saved.read_text(encoding='utf-8')) if args.send_only else fetch_research(date))
    articles=validate(data,date,check_http=not args.skip_http_check)
    if not articles:
        raise RuntimeError('No validated articles; newsletter and Kakao send cancelled')
    if args.send_only:
        path=SITE / f'{date.isoformat()}.html'
        if not path.exists(): raise RuntimeError('HTML not yet generated')
    else:
        path=render(articles,date)
        saved.write_text(json.dumps({'articles':articles},ensure_ascii=False,indent=2),encoding='utf-8')
    base=os.environ.get('PUBLIC_BASE_URL','').rstrip('/')
    if not base:
        raise RuntimeError('Set PUBLIC_BASE_URL to deployed GitHub Pages URL before sending')
    link=f'{base}/{path.name}'
    publish_date=date + dt.timedelta(days=1)
    highlights=' / '.join(a['title'][:22] for a in articles[:2])
    message=f'🔹반려동물산업 소식지🔹\n{publish_date} · {len(articles)}건\n{highlights}\n전체 기사 보기'
    print(f'Generated {path}: {len(articles)} items; landing URL: {link}')
    if args.send:
        send_kakao(message,link)
        print('Kakao API reported successful send')
    else:
        print('DRY RUN: no Kakao message sent')

if __name__=='__main__':
    main()
