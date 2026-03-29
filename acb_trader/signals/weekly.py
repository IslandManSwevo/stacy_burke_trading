"""
ACB Trader — Weekly Template Mapping (skill_weekly_template_mapping.md)
Maps the weekly + monthly cycle, computes anchors, Opening Range, and 3HC/3LC countdown.
"""

from __future__ import annotations
import pandas as pd
from datetime import datetime, date, timedelta
from typing import Optional
from acb_trader.config import ET, MONTHLY_RESET_DAYS, MONTHLY_FRONTSIDE_DAYS, MIN_OPENING_RANGE_PIPS
from acb_trader.db.models import (
    WeeklyTemplate, WeeklyAnchors, OpeningRange, CloseCountdown, DayRole, WeeklyReviewReport
)
from acb_trader.data.levels import snap_to_quarter, get_pip_size, compute_close_streak, price_to_pips


def build_weekly_template(
    pair: str,
    daily_ohlcv: pd.DataFrame,
    current_week: int,
    current_month: int,
    days_into_month: int,
    atr14: float,
    close_streak: int,
    cib_direction: str,
    as_of: date,
) -> WeeklyTemplate:
    """
    Master weekly template function.
    Called at NY close after classify_market_state().
    """
    anchors = _build_anchors(daily_ohlcv, current_week, current_month, days_into_month, pair)
    opening_range = _compute_opening_range(daily_ohlcv, current_week, pair)
    monthly_phase = _get_monthly_phase(days_into_month)
    dow = as_of.weekday()   # 0=Mon … 6=Sun
    day_name = ["MON","TUE","WED","THU","FRI","SAT","SUN"][dow]

    high_locked, low_locked = _check_locked(daily_ohlcv, close_streak)
    valid_dirs: list[str] = []
    if not high_locked: valid_dirs.append("LONG")
    if not low_locked:  valid_dirs.append("SHORT")
    if not valid_dirs:  valid_dirs = ["LONG", "SHORT"]

    template_type = _classify_template(daily_ohlcv, current_week, close_streak, atr14, days_into_month)
    day_role = _get_day_role(day_name, close_streak)
    countdown = _compute_close_countdown(pair, daily_ohlcv, cib_direction, anchors)

    return WeeklyTemplate(
        pair=pair,
        week_number=current_week,
        template_type=template_type,
        anchors=anchors,
        opening_range=opening_range,
        day_role=day_role,
        high_locked=high_locked,
        low_locked=low_locked,
        valid_directions=valid_dirs,
        best_setup_day="WED" if template_type == "REVERSAL_WEEK" else "THU",
        template_confidence=_confidence(template_type, close_streak, countdown),
        close_countdown=countdown,
        monthly_phase=monthly_phase,
        notes=f"Day {days_into_month} of month | {monthly_phase} | {template_type}",
        generated_at=datetime.combine(as_of, datetime.min.time()).replace(tzinfo=ET),
    )


def _build_anchors(
    ohlcv: pd.DataFrame,
    current_week: int,
    current_month: int,
    days_into_month: int,
    pair: str,
) -> WeeklyAnchors:
    wk_mask  = ohlcv["date"].dt.isocalendar().week == current_week
    mo_mask  = ohlcv["date"].dt.month == current_month
    pw_mask  = ohlcv["date"].dt.isocalendar().week == (current_week - 1)
    pm_mask  = ohlcv["date"].dt.month == (current_month - 1 if current_month > 1 else 12)

    def safe_max(df, col): return float(df[col].max()) if len(df) else 0.0
    def safe_min(df, col): return float(df[col].min()) if len(df) else 0.0

    cw = ohlcv[wk_mask]; pw = ohlcv[pw_mask]; cm = ohlcv[mo_mask]; pm = ohlcv[pm_mask]

    # FDTM
    fdtm_row = cm.iloc[0] if len(cm) else None
    fdtm_complete = days_into_month > 1

    return WeeklyAnchors(
        prior_week_high=safe_max(pw,"high"),   prior_week_low=safe_min(pw,"low"),
        prior_week_hcow=safe_max(pw,"close"),  prior_week_lcow=safe_min(pw,"close"),
        prior_month_high=safe_max(pm,"high"),  prior_month_low=safe_min(pm,"low"),
        prior_month_hcom=safe_max(pm,"close"), prior_month_lcom=safe_min(pm,"close"),
        week_open=float(cw["open"].iloc[0]) if len(cw) else 0.0,
        current_week_high=safe_max(cw,"high"), current_week_low=safe_min(cw,"low"),
        current_hcow=safe_max(cw,"close"),     current_lcow=safe_min(cw,"close"),
        month_open=float(cm["close"].iloc[0]) if len(cm) else 0.0,
        days_into_month=days_into_month,
        fdtm_open  =float(fdtm_row["open"])  if fdtm_row is not None else None,
        fdtm_high  =float(fdtm_row["high"])  if fdtm_row is not None else None,
        fdtm_low   =float(fdtm_row["low"])   if fdtm_row is not None else None,
        fdtm_close =float(fdtm_row["close"]) if fdtm_row is not None else None,
        fdtm_complete=fdtm_complete,
        monthly_phase=_get_monthly_phase(days_into_month),
    )


def _compute_opening_range(ohlcv: pd.DataFrame, current_week: int, pair: str) -> Optional[OpeningRange]:
    """Opening Range = Monday + Tuesday combined H/L."""
    wk = ohlcv[ohlcv["date"].dt.isocalendar().week == current_week].iloc[:2]
    if len(wk) < 2:
        return None
    pip = get_pip_size(pair)
    or_high = float(wk["high"].max())
    or_low  = float(wk["low"].min())
    size    = or_high - or_low
    size_pips = size / pip
    if size_pips < MIN_OPENING_RANGE_PIPS:
        return OpeningRange(or_high, or_low, size_pips, (or_high+or_low)/2,
                            or_high+size, or_low-size, or_high+2*size, or_low-2*size,
                            complete=True)
    return OpeningRange(
        high=snap_to_quarter(or_high, pair),
        low =snap_to_quarter(or_low,  pair),
        size_pips=size_pips,
        midpoint=snap_to_quarter((or_high+or_low)/2, pair),
        target_100pct_up=snap_to_quarter(or_high+size, pair),
        target_100pct_dn=snap_to_quarter(or_low-size,  pair),
        target_200pct_up=snap_to_quarter(or_high+2*size, pair),
        target_200pct_dn=snap_to_quarter(or_low-2*size,  pair),
        complete=True,
    )


def _get_monthly_phase(days: int) -> str:
    if days <= MONTHLY_RESET_DAYS:
        return "RESET"
    if days <= MONTHLY_FRONTSIDE_DAYS:
        return "FRONTSIDE"
    return "BACKSIDE"


def _get_day_role(day: str, close_streak: int) -> DayRole:
    roles = {
        "MON": DayRole("OPENING_RANGE",    "WAIT"),
        "TUE": DayRole("FRONT_SIDE_DAY2",  "FRONT_SIDE" if abs(close_streak) >= 2 else "WAIT"),
        "WED": DayRole("PIVOT",            "FRONT_SIDE" if abs(close_streak) <= 2 else "BACK_SIDE"),
        "THU": DayRole("BACK_SIDE_DAY1",   "BACK_SIDE"),
        "FRI": DayRole("EXIT_ONLY",        "NO_ENTRY"),
    }
    return roles.get(day, DayRole("UNKNOWN", "WAIT"))


def _check_locked(ohlcv: pd.DataFrame, close_streak: int) -> tuple[bool, bool]:
    """Return (high_locked, low_locked) based on streak reversal."""
    if close_streak < 0 and abs(close_streak) >= 1:
        return True, False   # high of week is likely in — only shorts valid
    if close_streak > 0 and abs(close_streak) >= 1:
        return False, True   # low of week is likely in — only longs valid
    return False, False


def _classify_template(
    ohlcv: pd.DataFrame,
    current_week: int,
    close_streak: int,
    atr14: float,
    days_into_month: int,
) -> str:
    wk = ohlcv[ohlcv["date"].dt.isocalendar().week == current_week]
    if len(wk) == 0:
        return "RANGING_WEEK"

    pw_high = float(ohlcv[ohlcv["date"].dt.isocalendar().week == current_week-1]["high"].max()) if len(ohlcv) > 5 else 0
    pw_low  = float(ohlcv[ohlcv["date"].dt.isocalendar().week == current_week-1]["low"].min())  if len(ohlcv) > 5 else float("inf")

    breakout_this_week = (
        float(wk["high"].max()) > pw_high or float(wk["low"].min()) < pw_low
    )

    if days_into_month <= 3 and breakout_this_week:
        return "NEW_MONTH_BREAKOUT"

    if breakout_this_week and abs(close_streak) >= 2 and close_streak * -1 != 0:
        # streak has reversed = reversal week
        if len(wk) >= 2:
            first_dir = 1 if float(wk["close"].iloc[0]) > float(wk["open"].iloc[0]) else -1
            last_dir  = 1 if float(wk["close"].iloc[-1]) > float(wk["close"].iloc[-2]) else -1
            if first_dir != last_dir:
                return "REVERSAL_WEEK"
        return "BREAKOUT_WEEK"

    if abs(close_streak) >= 2:
        return "BREAKOUT_WEEK"

    box = float(wk["high"].max() - wk["low"].min())
    if box <= 1.5 * atr14:
        return "RANGING_WEEK"

    return "BREAKOUT_WEEK"


def _compute_close_countdown(
    pair: str,
    ohlcv: pd.DataFrame,
    cib_direction: str,
    anchors: WeeklyAnchors,
) -> CloseCountdown:
    streak = compute_close_streak(ohlcv["close"])
    direction_matches = (
        (cib_direction == "BULLISH" and streak > 0) or
        (cib_direction == "BEARISH" and streak < 0)
    )
    count = abs(streak) if direction_matches else 0
    label = (
        "DAY_1" if count == 1 else
        "DAY_2" if count == 2 else
        "SIGNAL_DAY" if count >= 3 else "NONE"
    )
    last_close = float(ohlcv["close"].iloc[-1])
    pip = get_pip_size(pair)
    at_hcom = abs(last_close - anchors.current_hcow) <= 25 * pip
    at_lcom = abs(last_close - anchors.current_lcow) <= 25 * pip
    at_hom  = abs(last_close - anchors.current_week_high) <= 25 * pip
    at_lom  = abs(last_close - anchors.current_week_low) <= 25 * pip

    return CloseCountdown(
        pair=pair,
        direction="3HC" if (cib_direction == "BULLISH") else "3LC",
        count=count,
        label=label,
        at_hcom_lcom=at_hcom or at_lcom,
        at_hom_lom=at_hom or at_lom,
        signal_ready=count >= 2,
    )


def _confidence(template_type: str, close_streak: int, countdown: CloseCountdown) -> str:
    if template_type == "NEW_MONTH_BREAKOUT":
        return "HIGH"
    if template_type == "REVERSAL_WEEK" and countdown.at_hcom_lcom:
        return "HIGH"
    if template_type in ("REVERSAL_WEEK", "BREAKOUT_WEEK") and abs(close_streak) >= 2:
        return "MEDIUM"
    return "LOW"


# ── WEEKLY REVIEW ────────────────────────────────────────────────────────────

def build_weekly_review(monday: date, weekly_dd_pct: float = 0.0) -> WeeklyReviewReport:
    """
    Aggregate completed trades and discards for the Mon–Fri window starting at
    *monday* into a WeeklyReviewReport.  Called by run_weekly_review() in main.py
    every Friday after the EOD run.

    Parameters
    ----------
    monday          : date — Monday of the week to aggregate (auto-computed in main.py)
    weekly_dd_pct  : float — (balance_friday - balance_monday) / balance_monday,
                      fetched from session_tracker in the caller.
    """
    from acb_trader.db.session_tracker import get_week_trades, get_week_discards
    from acb_trader.config import ET

    friday = monday + timedelta(days=4)
    trades   = get_week_trades(monday)    # list of raw dicts
    discards = get_week_discards(monday)  # list of raw dicts

    total  = len(trades)
    wins   = sum(1 for t in trades if float(t.get("r_multiple", 0)) > 0)
    losses = sum(1 for t in trades if float(t.get("r_multiple", 0)) < 0)
    win_rate   = wins / total if total > 0 else 0.0
    total_pips = sum(float(t.get("pips", 0)) for t in trades)
    total_r    = sum(float(t.get("r_multiple", 0)) for t in trades)

    def _safe_r(t_dict):
        try:
            return float(t_dict.get("r_multiple", 0))
        except (TypeError, ValueError):
            return 0.0

    best  = max(trades, key=_safe_r, default=None)
    worst = min(trades, key=_safe_r, default=None)

    best_str  = f"{best.get('pair', '<unknown>')} {_safe_r(best):+.2f}R"  if best  else None
    worst_str = f"{worst.get('pair', '<unknown>')} {_safe_r(worst):+.2f}R" if worst else None

    # Per-pattern breakdown
    breakdown: dict = {}
    for t in trades:
        p = t.get("pattern", "UNKNOWN")
        if p not in breakdown:
            breakdown[p] = {"trades": 0, "wins": 0, "total_r": 0.0}
        breakdown[p]["trades"] += 1
        r = float(t.get("r_multiple", 0))
        if r > 0:
            breakdown[p]["wins"] += 1
        breakdown[p]["total_r"] = round(breakdown[p]["total_r"] + r, 2)

    # Discard hindsight: how many discarded setups would have hit T1
    discards_hit = sum(
        1 for d in discards
        if d.get("would_have_hit_t1") is True
    )

    return WeeklyReviewReport(
        week_start=monday,
        week_end=friday,
        total_trades=total,
        wins=wins,
        losses=losses,
        win_rate=win_rate,
        total_pips=round(total_pips, 1),
        total_r=round(total_r, 2),
        best_trade=best_str,
        worst_trade=worst_str,
        pattern_breakdown=breakdown,
        discards_would_have_hit=discards_hit,
        discards_total=len(discards),
        weekly_dd_pct=weekly_dd_pct,
        generated_at=datetime.now(ET),
    )
