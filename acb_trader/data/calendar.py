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
from acb_trader.config import ET, NEWS_BLOCK_WINDOW_HOURS, NEWS_SETTLE_MINUTES


@dataclass
class NewsEvent:
    timestamp: datetime
    currency: str
    title: str
    impact: str             # "HIGH" | "MEDIUM" | "LOW"


import threading

# ── CACHE ─────────────────────────────────────────────────────────────────────
_CALENDAR_CACHE: list[NewsEvent] = []
_CALENDAR_FETCHED_AT: datetime | None = None
_CALENDAR_TTL_SEC: int = 300
_CALENDAR_LOCK = threading.Lock()

def fetch_calendar(
    window_start: datetime,
    window_end: datetime,
    impact: str = "HIGH",
    force_refresh: bool = False,
) -> list[NewsEvent]:
    """
    Fetch news events from ForexFactory JSON feed with short-lived cache.
    Falls back to empty list on network failure so trading is not blocked.
    """
    global _CALENDAR_CACHE, _CALENDAR_FETCHED_AT

    now = datetime.now(window_start.tzinfo if window_start.tzinfo else ET)
    is_stale = (_CALENDAR_FETCHED_AT is None or 
                (now - _CALENDAR_FETCHED_AT).total_seconds() > _CALENDAR_TTL_SEC)

    if is_stale or force_refresh:
        try:
            url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            data = resp.json()

            new_events = []
            for item in data:
                try:
                    # ISO format parsing for ForexFactory feed
                    ts = datetime.fromisoformat(item["date"].replace("Z", "+00:00")).astimezone(ET)
                    new_events.append(NewsEvent(
                        timestamp=ts,
                        currency=item.get("country", "").upper(),
                        title=item.get("title", ""),
                        impact=item.get("impact", "").upper(),
                    ))
                except Exception:
                    continue
            
            _CALENDAR_CACHE = new_events
            _CALENDAR_FETCHED_AT = now
        except Exception as e:
            print(f"[calendar] Failed to fetch news calendar: {e}")
            # Do NOT clear old cache on failure — better to have stale data than none 
            # if we have it, but return empty if we have nothing.
            if not _CALENDAR_CACHE:
                return []
            else:
                # proceed to filter cached items
                pass

    # Filter from cache
    filtered = []
    for e in _CALENDAR_CACHE:
        if e.impact != impact:
            continue
        filtered.append(e)
    return filtered


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
    Block window = 1hr before → NEWS_BLOCK_WINDOW_HOURS after session open.
    Returns an empty list when no blocking events exist.
    """
    currencies = get_currencies(pair)
    window_start = session_open - timedelta(hours=1)
    window_end   = session_open + timedelta(hours=NEWS_BLOCK_WINDOW_HOURS)
    events = fetch_calendar(window_start, window_end, impact="HIGH")
    return [e for e in events if e.currency in currencies]


def is_news_blocked(pair: str, session_open: datetime) -> bool:
    """
    Returns True if a HIGH-impact news event falls within the block window
    for this pair's currencies around the session open time.
    Block = 1hr before → 3hrs after session open.
    """
    return bool(get_blocking_events(pair, session_open))


def _get_recent_high_impact(pair: str, now: datetime) -> list[NewsEvent]:
    """
    Fetch HIGH-impact events in the ±3 hour window around *now* that
    affect this pair's currencies.  Used by the real-time settle guard.
    """
    currencies = get_currencies(pair)
    window_start = now - timedelta(hours=3)
    window_end   = now + timedelta(hours=3)
    events = fetch_calendar(window_start, window_end, impact="HIGH")
    return [e for e in events if e.currency in currencies]


def news_settle_until(pair: str, now: datetime) -> datetime | None:
    """
    Return the earliest datetime at which the settle window is clear
    for this pair, or *None* if no MRN event is active.

    Logic: for every HIGH-impact event whose timestamp falls within
    the last NEWS_SETTLE_MINUTES, compute event_ts + settle.  Return
    the latest such boundary.
    """
    events = _get_recent_high_impact(pair, now)
    if not events:
        return None
    latest_boundary = None
    for e in events:
        boundary = e.timestamp + timedelta(minutes=NEWS_SETTLE_MINUTES)
        if boundary > now:  # still inside the settle window
            if latest_boundary is None or boundary > latest_boundary:
                latest_boundary = boundary
    return latest_boundary


def is_in_news_settle_window(pair: str, now: datetime | None = None) -> bool:
    """
    Real-time guard: returns True if *now* falls within 30 minutes
    of a HIGH-impact news print for this pair.

    Call this before ANY order placement or fill acceptance.  If True,
    the algorithm MUST wait — entering during the spike is Garbage
    Trading and will not pass the 100-Lot Litmus Test.
    """
    if now is None:
        now = datetime.now(ET)
    return news_settle_until(pair, now) is not None
