"""
ACB Trader — Economic Calendar
Fetches high-impact news events from ForexFactory.
No entry is placed within the news block window.
"""

from __future__ import annotations
import os
import requests
from datetime import datetime, timedelta
from dataclasses import dataclass
from acb_trader.config import ET, NEWS_BLOCK_WINDOW_HOURS


@dataclass
class NewsEvent:
    timestamp: datetime
    currency: str
    title: str
    impact: str             # "HIGH" | "MEDIUM" | "LOW"


def fetch_calendar(
    window_start: datetime,
    window_end: datetime,
    impact: str = "HIGH",
) -> list[NewsEvent]:
    """
    Fetch news events from ForexFactory JSON feed.
    Falls back to empty list on network failure so trading is not blocked.
    """
    try:
        url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[calendar] Failed to fetch news calendar: {e}")
        return []

    events = []
    for item in data:
        try:
            ts = datetime.fromisoformat(item["date"]).astimezone(ET)
        except Exception:
            continue
        if ts < window_start or ts > window_end:
            continue
        if impact == "HIGH" and item.get("impact", "").upper() != "HIGH":
            continue
        events.append(NewsEvent(
            timestamp=ts,
            currency=item.get("country", "").upper(),
            title=item.get("title", ""),
            impact=item.get("impact", "").upper(),
        ))
    return events


def get_currencies(pair: str) -> list[str]:
    """Extract the two ISO currency codes from a pair string."""
    mapping = {
        "EURUSD": ["EUR", "USD"], "GBPUSD": ["GBP", "USD"],
        "USDJPY": ["USD", "JPY"], "USDCHF": ["USD", "CHF"],
        "USDCAD": ["USD", "CAD"], "AUDUSD": ["AUD", "USD"],
        "NZDUSD": ["NZD", "USD"], "GBPJPY": ["GBP", "JPY"],
        "EURJPY": ["EUR", "JPY"], "AUDJPY": ["AUD", "JPY"],
        "CADJPY": ["CAD", "JPY"], "NZDJPY": ["NZD", "JPY"],
        "GBPAUD": ["GBP", "AUD"], "GBPNZD": ["GBP", "NZD"],
        "GBPCHF": ["GBP", "CHF"], "CHFJPY": ["CHF", "JPY"],
        "XAUUSD": ["XAU", "USD"], "USOIL": ["USD", "OIL"],
        "SP500":  ["USD"],         "NAS100": ["USD"], "DJ30": ["USD"],
    }
    return mapping.get(pair.upper(), ["USD"])


def get_blocking_events(pair: str, session_open: datetime) -> list[NewsEvent]:
    """
    Return the HIGH-impact news events that block this pair around session_open.
    Block window = 1hr before → 3hrs after session open.
    Returns an empty list when no blocking events exist.
    """
    currencies = get_currencies(pair)
    window_start = session_open - timedelta(hours=1)
    window_end   = session_open + timedelta(hours=3)
    events = fetch_calendar(window_start, window_end, impact="HIGH")
    return [e for e in events if e.currency in currencies]


def is_news_blocked(pair: str, session_open: datetime) -> bool:
    """
    Returns True if a HIGH-impact news event falls within the block window
    for this pair's currencies around the session open time.
    Block = 1hr before → 3hrs after session open.
    """
    return bool(get_blocking_events(pair, session_open))
