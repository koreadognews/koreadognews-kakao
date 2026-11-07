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
