"""At-most-once Kakao delivery per news date.

A durable claim is created before sending. A failed/uncertain delivery remains
claimed and requires manual investigation; it is never automatically resent.
"""
import base64
import datetime as dt
import json
import os
import subprocess
import sys
from zoneinfo import ZoneInfo

import requests

API = 'https://api.github.com'
REPO = os.environ.get('GITHUB_REPOSITORY', '')
TOKEN = os.environ.get('GITHUB_TOKEN', '')
BRANCH = os.environ.get('GITHUB_REF_NAME', 'main')
TODAY = dt.datetime.now(ZoneInfo('Asia/Seoul')).date()
DATE = (TODAY - dt.timedelta(days=1)).isoformat()
PATH = f'.delivery-ledger/{DATE}.json'


def api(method, *, body=None):
    url = f'{API}/repos/{REPO}/contents/{PATH}'
    headers = {
        'Authorization': f'Bearer {TOKEN}',
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
    }
    try:
        response = requests.request(method, url, headers=headers,
                                    params={'ref': BRANCH} if method == 'GET' else None,
                                    json=body, timeout=30)
    except requests.RequestException as exc:
        raise RuntimeError('GitHub delivery ledger unavailable; no Kakao send attempted') from exc
    return response


def write_record(status, *, sha=None):
    record = {
        'news_date': DATE,
        'status': status,
        'run_id': os.environ.get('GITHUB_RUN_ID', ''),
        'updated_at': dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    body = {
        'message': f'Kakao delivery {status} for {DATE}',
        'content': base64.b64encode(json.dumps(record).encode()).decode(),
        'branch': BRANCH,
    }
    if sha:
        body['sha'] = sha
    return api('PUT', body=body)


def main():
    if REPO != 'koreadognews/koreadognews-kakao' or not TOKEN:
        raise RuntimeError('Repository or GITHUB_TOKEN not configured; no send attempted')

    current = api('GET')
    if current.status_code == 200:
        print(f'Delivery record already exists for {DATE}. Skipping to avoid duplicate.')
        return
    if current.status_code != 404:
        raise RuntimeError(f'Cannot check delivery record (HTTP {current.status_code}); not sending')

    claimed = write_record('claimed')
    if claimed.status_code == 422:
        print('Another run already claimed this date; skipping send')
        return
    if claimed.status_code != 201:
        raise RuntimeError(f'Cannot reserve delivery (HTTP {claimed.status_code}); not sending')
    sha = claimed.json()['content']['sha']
    print(f'Reserved Kakao delivery for {DATE}')

    # Never retry uncertain sends: Kakao may accept a message before an HTTP timeout.
    result = subprocess.run([sys.executable, 'bot.py', '--send-only', '--send'], check=False)
    if result.returncode != 0:
        raise RuntimeError('Kakao send did not finish cleanly. Claim retained; check chat before manual recovery.')

    done = write_record('sent', sha=sha)
    if done.status_code != 200:
        raise RuntimeError('Kakao API reported success, but ledger update failed. Claim retained; DO NOT resend.')
    print(f'Kakao delivery for {DATE} marked sent')


if __name__ == '__main__':
    main()
