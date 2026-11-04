"""Separate discovery queries for Korean pet industry newsletter.

Discovery candidates are NOT verified news. bot.create_issue() must independently
check publisher URL and first-publication metadata before publishing.
"""
import datetime as dt
import json
import logging
import os
import re
import time

from openai import (
    OpenAI, APIConnectionError, APITimeoutError, InternalServerError, RateLimitError,
)

SECTIONS = (
    ("domestic", ("domestic_industry", "domestic_supplement")),
    ("overseas", ("overseas",)),
    ("events", ("events",)),
)


def _prompt(section, issue, start, end):
    common = (
        f"Korea Dog News edition {issue.isoformat()}, Korea timezone. "
        f"First publication must be >= {start.isoformat()} and < {end.isoformat()}. "
        "Find actual publisher URLs only, never invent title, date, URL, or publication time. "
        "BIGKinds may be used for discovery but verify on original publisher pages. "
        "Return ONLY JSON with requested keys, lists, and no markdown. "
    )
    item = ('Each news record has title, url (direct https URL), publisher, '
            'published_at (original publication ISO 8601 WITH timezone), summary. '
            'If exact first publication time is unverified, OMIT the candidate. ')
    if section == "domestic":
        return (common + item +
                "Research original Korean newspapers, Ministry of Agriculture, local governments, "
                "RDA, KREI, aT, Statistics Korea, Korea Customs, research agencies and corporate releases. "
                "Prefer pet food, supplies, manufacturing, trade, investment, market data, policy, nonmedical pet-tech. "
                "Exclude veterinary/clinical, pet pharmaceuticals and medical devices. "
                "domestic_industry: at most 15 articles; at most 2 articles from koreadognews.co.kr. "
                "domestic_supplement: culture/lifestyle/events news published in the SAME window, "
                "only to make up a shortfall below 15 industry articles. "
                'Return {"domestic_industry":[],"domestic_supplement":[]}.')
    if section == "overseas":
        return (common + item +
                "Research up to 5 ORIGINAL overseas pet industry articles, not veterinary clinical news. "
                "Dates must be converted to Asia/Seoul when deciding eligibility. "
                'Return {"overseas":[]}.')
    return (f"Korea Dog News edition {issue.isoformat()}. "
            "Search publicly announced Korean pet fairs, expos and cultural events "
            "scheduled from the edition day through ONE calendar month later. "
            "Announcements may be older than the news interval. "
            "Include only real, verifiable dates and official announcements. "
            "Each record needs name, start_date, end_date in YYYY-MM-DD, and direct https url. "
            'Return ONLY JSON {"events":[]}. Never invent events or URLs.')


def _decode_json(output):
    raw = output.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw).strip()
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.S)
        if not match:
            raise ValueError("OpenAI response did not contain JSON")
        result = json.loads(match.group(0))
    if not isinstance(result, dict):
        raise ValueError("Expected JSON object")
    return result


def _query(client, prompt, section):
    """At most 2 attempts, no hidden SDK retries; avoid the 30-minute CI cap."""
    for attempt in range(2):
        try:
            response = client.responses.create(
                model=os.getenv("OPENAI_MODEL", "gpt-5-mini"),
                tools=[{"type": "web_search"}],
                input=prompt,
            )
            return _decode_json(response.output_text)
        except RateLimitError as e:
            msg = str(e).lower()
            if any(word in msg for word in (
                "insufficient_quota", "billing", "no credits remaining", "quota exceeded"
            )):
                raise
            if attempt == 1:
                raise
        except (APIConnectionError, APITimeoutError, InternalServerError):
            if attempt == 1:
                raise
        if attempt == 0:
            logging.warning("%s search temporarily unavailable; retrying once", section)
            time.sleep(10)
    raise RuntimeError("No search result")


def fetch_research_split(issue, start=None, end=None, *, query_fn=None):
    """Search 3 independent sections; retain successful sections if another times out.

    Caller MUST apply the existing bot.validate_news and validate_events.
    """
    from zoneinfo import ZoneInfo
    if start is None or end is None:
        tz = ZoneInfo("Asia/Seoul")
        end = dt.datetime.combine(issue, dt.time(6), tz)
        start = end - dt.timedelta(days=3 if issue.weekday() == 0 else 1)
    client = None if query_fn is not None else OpenAI(max_retries=0, timeout=75.0)
    results = {"domestic_industry": [], "domestic_supplement": [], "overseas": [], "events": []}
    successes = 0
    for section, keys in SECTIONS:
        prompt = _prompt(section, issue, start, end)
        try:
            part = (query_fn(section, prompt) if query_fn is not None
                    else _query(client, prompt, section))
            if not isinstance(part, dict):
                raise ValueError("Expected JSON object")
            for key in keys:
                value = part.get(key, [])
                if not isinstance(value, list):
                    raise ValueError(f"{key} must be a list")
                results[key] = value
            successes += 1
            logging.info("%s discovery complete", section)
        except (APIConnectionError, APITimeoutError, InternalServerError, RateLimitError,
                ValueError, json.JSONDecodeError) as exc:
            if isinstance(exc, RateLimitError) and any(word in str(exc).lower()
                    for word in ("insufficient_quota", "billing", "no credits remaining", "quota exceeded")):
                raise
            logging.warning("%s discovery failed (%s); retaining other sections",
                            section, type(exc).__name__)
    if successes == 0:
        raise RuntimeError("All three searches failed; newsletter generation canceled")
    return results

