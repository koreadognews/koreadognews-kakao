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
