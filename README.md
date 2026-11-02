# 한국애견신문 → 개인 카카오톡 자동 발행 프로젝트

## 개요
GitHub Actions가 **매일 06:00 KST(UTC 21:00)**에 웹검색 기반 산업 기사 후보 수집 → 링크 HTTP 검증 및 날짜/축제 필터링 → GitHub Pages HTML 발행 → 카카오톡 **나와의 채팅**에 소식지 요약 및 링크를 보냅니다. 토요일·일요일도 실행합니다.

> **주의**: AI가 반환한 발행일은 기사 본문에서 독립 추출해 확증하는 수준의 검증이 아닙니다. HTTP 검증은 링크가 열리는지 확인할 뿐 기사 날짜/진위를 보증하지 않습니다. 중요 보도에는 편집자 검수나 추가 기사 메타데이터/원문 검증 로직이 필요합니다. 실제 시각은 GitHub Actions 스케줄 지연과 뉴스 생성·배포 시간 때문에 06:00 이후일 수 있습니다. 

기존 ChatGPT 예약 작업과 **별도 시스템**입니다. ChatGPT 예약 작업의 결과를 이 코드가 직접 가져오지는 않습니다. OpenAI API 호출은 ChatGPT Plus와 **별도 과금**됩니다.

## 1. 카카오 준비
1. https://developers.kakao.com/ 에서 앱을 생성하고 카카오 로그인 사용 설정.
2. REST API 키 확인, 클라이언트 시크릿 확인, `talk_message` 동의항목 설정.
3. Redirect URI 등록: 예 `http://localhost:8765/callback` (디벨로퍼스에서 허용되는 방식 확인).
4. 브라우저에서 아래 주소의 YOUR_REST_KEY와 URL 인코딩된 REDIRECT_URI 교체 후 로그인하여 code를 받으세요.

```
https://kauth.kakao.com/oauth/authorize?client_id=YOUR_REST_KEY&redirect_uri=http%3A%2F%2Flocalhost%3A8765%2Fcallback&response_type=code&scope=talk_message
```

5. 터미널에서 인가 코드를 토큰으로 교환합니다. 시크릿이 ON이면 `client_secret`가 필요합니다.

```bash
curl -X POST https://kauth.kakao.com/oauth/token \
  -d 'grant_type=authorization_code' \
  -d 'client_id=YOUR_REST_KEY' \
  -d 'client_secret=YOUR_CLIENT_SECRET' \
  -d 'redirect_uri=http://localhost:8765/callback' \
  -d 'code=AUTH_CODE'
```

반환된 **refresh_token**을 GitHub Secret으로 저장하세요. **액세스 토큰/리프레시 토큰/시크릿을 채팅이나 공개 저장소에 공유하지 마세요.** `refresh_token`은 만료/교체될 수 있습니다. 이 프로젝트는 토큰이 교체되면 `tokens.json`에 로컬 저장하고 로그에 경고하지만, CI 비밀값이 자동 갱신되지는 않습니다. 만료 전 새로운 refresh token을 GitHub Secrets에 수동 갱신하는 운영 절차가 필요합니다. 완전 무인 장기운영에는 토큰을 지속적으로 저장하는 비밀 저장소로 개선하세요.

## 2. GitHub 저장소 / Pages
1. GitHub에서 새 저장소(예: `koreadognews-kakao`)를 만들고 이 ZIP 압축을 풀어 파일 업로드.
2. Settings → Pages → Build and deployment → **Deploy from a branch**, branch `main`, folder `/docs` 선택. `docs` 폴더는 최초 실행 시 생깁니다. 최초 생성 전에는 `docs`를 빈 디렉터리로 올릴 수 없으므로 `docs/.nojekyll`을 먼저 업로드하세요.
3. Repository Settings → Secrets and variables → Actions → **Secrets**:
    - `OPENAI_API_KEY` (OpenAI API 비용 별도)
    - `KAKAO_REST_API_KEY`
    - `KAKAO_CLIENT_SECRET` (사용하지 않는 경우 비워둘 수 있음)
    - `KAKAO_REFRESH_TOKEN`
4. 같은 화면의 **Variables**: `PUBLIC_BASE_URL` = `https://USERNAME.github.io/koreadognews-kakao` (본인 주소에 맞게 수정).
5. Actions → Korean Pet Industry Daily → Run workflow로 수동 시험. Actions 로그, Pages 페이지, 카카오톡 나와의 채팅을 순서대로 확인하세요.

## 3. 실행
```bash
python -m pip install -r requirements.txt
export OPENAI_API_KEY='...'
export PUBLIC_BASE_URL='https://USERNAME.github.io/koreadognews-kakao'
python bot.py                 # 검색 + docs/YYYY-MM-DD.html, .json 생성, 발송 안 함
python bot.py --send-only --send  # 페이지 배포 후 발송
```

검수된 뉴스 JSON이 있다면 `python bot.py --input approved.json`으로 HTML을 만들 수 있습니다. `sample_articles.json`은 실제 뉴스가 **아닌 예제**입니다.

## 4. 필수 운영 점검
- `docs/YYYY-MM-DD.json`에 저장된 기사들의 **실제 게시일**과 원문 링크 검증 (현재 자동 처리의 한계).
- 발행 호수: `ISSUE_BASE` / `ISSUE_BASE_DATE` 환경변수 조정. 기본값은 2026-11-02에 1044호, 이후 날짜당 +1 가정입니다.
- 문화행사 필터는 제목/요약 키워드 기반이며 완벽하지 않습니다.
- Google/GitHub의 서비스 이용 약관, API 쿼터, 카카오 발송 정책 준수.
- `GitHub Actions`는 예약 시각에 실행이 지연되거나 누락될 수 있어 엄격한 06:00 보장은 어렵습니다.
- 웹페이지에서 외부 기사 전문을 복제하지 않고 출처·제목·간단한 자체 요약·원문 링크만 게시하도록 설계했습니다.
