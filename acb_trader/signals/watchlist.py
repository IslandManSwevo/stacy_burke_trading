"""
ACB Trader — Daily Watchlist Filter (skill_market_classification.md §Daily Watchlist)
6-criteria check per pair. Pairs must pass ≥1 criterion to be scanned for setups.
"""

from __future__ import annotations
import pandas as pd
from datetime import datetime, date
from acb_trader.db.models import MarketState, WatchlistResult, WeeklyTemplate
from acb_trader.config import ET
from acb_trader.data.levels import get_pip_size
from typing import Optional


def evaluate_watchlist(
    state: MarketState,
    daily_ohlcv: pd.DataFrame,
    prior_week_high: float,
    prior_week_low: float,
    prior_month_high: float,
    prior_month_low: float,
    as_of: date,
    template: Optional[WeeklyTemplate] = None,
) -> WatchlistResult:
    """
    Burke's 6 watchlist criteria (ACB Manual p.14).
    Any one criterion = pair is on watchlist.
    Priority = count of criteria met.
    """
    criteria: list[str] = []
    dow = as_of.weekday()
    is_monday = (dow == 0)
    is_tuesday = (dow == 1)

    # 1. Breakout Day 1 or CIB (Most common trigger)
    if state.cib or "BREAKOUT_DAY_1" in state.substate:
        criteria.append("BREAKOUT_MOMENTUM")

    # 2. Signal Day (FRD, FGD, Inside Day, or Trending Back Side)
    # TRENDING_BACK_SIDE (Thu/Fri, streak≥2) is added here so the pair stays
    # on the watchlist as the trend peak/trough forms — setups.py will then
    # detect FRD/FGD/PARABOLIC on the reversal day.
    if state.substate in (
        "FIRST_RED_DAY_SIGNAL", "FIRST_GREEN_DAY_SIGNAL", "INSIDE_DAY",
        "TRENDING_BACK_SIDE"
    ):
        criteria.append("SIGNAL_DAY")

    # 3. Opening Range Creation (Monday/Tuesday)
    if is_monday or is_tuesday:
        criteria.append("OPENING_RANGE_WINDOW")

    # 4. Level Test (LCOW/HCOW/HOM/LOM/PWL/PWH)
    pip = get_pip_size(state.pair)
    last_close = float(daily_ohlcv["close"].iloc[-1])
    
    levels = [prior_week_high, prior_week_low, prior_month_high, prior_month_low]
    if template and template.anchors:
        levels.extend([
            template.anchors.current_week_high, template.anchors.current_week_low,
            template.anchors.current_hcow, template.anchors.current_lcow
        ])
    
    at_level = any(abs(last_close - lv) <= 20 * pip for lv in levels if lv > 0)
    if at_level:
        criteria.append("MAJOR_LEVEL_TEST")

    # 5. New Week/Month Breakout
    if is_monday:
        if state.how > prior_week_high or state.low_of_week < prior_week_low:
            criteria.append("WEEKLY_LEVEL_BREAK")
        if state.hom > prior_month_high or state.lom < prior_month_low:
            criteria.append("MONTHLY_LEVEL_BREAK")

    # 6. 3HC/3LC Countdown (Day 2 or 3)
    if template and template.close_countdown:
        if template.close_countdown.count >= 2:
            criteria.append("COUNTDOWN_MATURITY")

    return WatchlistResult(
        pair=state.pair,
        on_watchlist=len(criteria) > 0,
        criteria_met=criteria,
        priority=len(criteria),
    )


def _prior_week_closed_in_breakout(daily_ohlcv: pd.DataFrame) -> bool:
    """Check if last week's close broke beyond the prior week's range."""
    if len(daily_ohlcv) < 10:
        return False
    try:
        closes = daily_ohlcv["close"]
        highs  = daily_ohlcv["high"]
        lows   = daily_ohlcv["low"]
        # Prior week high/low = 5-10 days back
        pw_high = float(highs.iloc[-10:-5].max())
        pw_low  = float(lows.iloc[-10:-5].min())
        lw_close = float(closes.iloc[-5:-1].iloc[-1])  # last week's final close
        return lw_close > pw_high or lw_close < pw_low
    except Exception:
        return False
