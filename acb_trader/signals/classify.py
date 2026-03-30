"""
ACB Trader — Market Classification (skill_market_classification.md)
Classifies each pair as BREAKOUT | TRENDING | RANGING.
"""

from __future__ import annotations
import pandas as pd
from datetime import datetime
from acb_trader.config import ET, ATR_PERIOD
from acb_trader.db.models import MarketState, TrapAnalysis, Setup
from acb_trader.data.levels import (
    compute_atr, compute_close_streak, compute_day_break_counter,
    get_pip_size, price_to_pips
)


def classify_market_state(
    pair: str,
    daily_ohlcv: pd.DataFrame,
    intraday_1min: pd.DataFrame,
    session_1min: pd.DataFrame,
    current_week: int,
    current_month: int,
    as_of: date,
) -> MarketState:
    """
    Master classification function. Runs all steps and returns MarketState.
    Must be called AFTER NY close (4:59 PM ET) — never on in-progress candles.
    """
    # ── Step 1: Base indicators ───────────────────────────────────────────────
    atr14 = compute_atr(daily_ohlcv, ATR_PERIOD)

    range_high_10 = daily_ohlcv["high"].rolling(10).max()
    range_low_10  = daily_ohlcv["low"].rolling(10).min()
    box_size      = float(range_high_10.iloc[-1] - range_low_10.iloc[-1])

    # Named structural levels
    week_mask  = daily_ohlcv["date"].dt.isocalendar().week == current_week
    month_mask = daily_ohlcv["date"].dt.month == current_month

    week_rows  = daily_ohlcv[week_mask]
    month_rows = daily_ohlcv[month_mask]

    hod = float(intraday_1min["high"].max()) if len(intraday_1min) else 0.0
    lod = float(intraday_1min["low"].min())  if len(intraday_1min) else 0.0
    how = float(week_rows["high"].max())     if len(week_rows) else 0.0
    low_of_week = float(week_rows["low"].min()) if len(week_rows) else 0.0
    hos = float(session_1min["high"].max())  if len(session_1min) else 0.0
    los = float(session_1min["low"].min())   if len(session_1min) else 0.0
    hom  = float(month_rows["high"].max())   if len(month_rows) else 0.0
    lom  = float(month_rows["low"].min())    if len(month_rows) else 0.0
    hcom = float(month_rows["close"].max())  if len(month_rows) else 0.0
    lcom = float(month_rows["close"].min())  if len(month_rows) else 0.0

    # CIB — closed in breakout
    last_close    = float(daily_ohlcv["close"].iloc[-1])
    prior_rh      = float(range_high_10.iloc[-2]) if len(range_high_10) > 1 else 0.0
    prior_rl      = float(range_low_10.iloc[-2])  if len(range_low_10) > 1 else float("inf")
    cib_bullish   = last_close > prior_rh
    cib_bearish   = last_close < prior_rl
    cib           = cib_bullish or cib_bearish
    cib_direction = "BULLISH" if cib_bullish else ("BEARISH" if cib_bearish else "NONE")

    close_streak     = compute_close_streak(daily_ohlcv)
    day_break_counter = compute_day_break_counter(daily_ohlcv)

    contraction_ratio = compute_atr(daily_ohlcv, 3) / atr14 if atr14 > 0 else 1.0

    # ── Step 2: Classify state ────────────────────────────────────────────────
    last_high  = float(daily_ohlcv["high"].iloc[-1])
    last_low   = float(daily_ohlcv["low"].iloc[-1])
    last_range = last_high - last_low
    close_in_top30    = last_close > (last_low + 0.7 * last_range)
    close_in_bottom30 = last_close < (last_low + 0.3 * last_range)
    net_breakout = last_close > prior_rh or last_close < prior_rl

    if (
        net_breakout
        and last_range >= atr14
        and (close_in_top30 or close_in_bottom30)
        and abs(close_streak) >= 1
        and box_size <= 3.0 * atr14
    ):
        streak_abs = abs(close_streak)
        if streak_abs == 1:
            substate = "BREAKOUT_DAY_1"
        elif streak_abs == 2:
            substate = "BREAKOUT_DAY_2"
        else:
            substate = "BREAKOUT_DAY_3_PLUS"
        state = "BREAKOUT"
        direction = "BULLISH" if close_in_top30 else "BEARISH"

    elif (
        abs(close_streak) >= 2
        and price_to_pips(daily_ohlcv["close"].iloc[-1] - daily_ohlcv["close"].iloc[-abs(close_streak)-1], pair) >= 1.5 * price_to_pips(atr14, pair)
        and contraction_ratio >= 0.60
    ):
        import datetime as dt_mod
        dow = daily_ohlcv["date"].iloc[-1]
        if hasattr(dow, "weekday"):
            is_back = dow.weekday() >= 3  # Thursday or Friday
        else:
            is_back = False
        substate = "TRENDING_BACK_SIDE" if is_back else "TRENDING_FRONT_SIDE"
        state = "TRENDING"
        direction = "BULLISH" if close_streak > 0 else "BEARISH"

    else:
        # Check for Signal Days (FRD, FGD, Inside Day)
        last_row = daily_ohlcv.iloc[-1]
        prev_row = daily_ohlcv.iloc[-2] if len(daily_ohlcv) > 1 else last_row

        # Prior streak (excluding today's bar) — needed for trend prerequisite
        prior_streak = compute_close_streak(daily_ohlcv.iloc[:-1])

        is_inside = (float(last_row["high"]) < float(prev_row["high"]) and
                     float(last_row["low"])  > float(prev_row["low"]))

        # Playbook-standard FRD/FGD definition (matches setups.py):
        #   close < open  AND  close < prev_close  →  FRD
        #   close > open  AND  close > prev_close  →  FGD
        # Require abs(prior_streak) >= 2 so the signal only fires after a real trend leg.
        # (The old "outside close" requirement was far stricter than the Burke playbook.)
        frd = (last_close < float(last_row["open"]) and
               last_close < float(prev_row["close"]) and
               prior_streak >= 3)
        fgd = (last_close > float(last_row["open"]) and
               last_close > float(prev_row["close"]) and
               prior_streak <= -3)

        if is_inside:
            substate = "INSIDE_DAY"
        elif frd:
            substate = "FIRST_RED_DAY_SIGNAL"
        elif fgd:
            substate = "FIRST_GREEN_DAY_SIGNAL"
        else:
            substate = "RANGING"

        state = "RANGING"
        direction = "NEUTRAL"

    # ── Step 3: Trap analysis ─────────────────────────────────────────────────
    trap = _detect_trap(daily_ohlcv, close_streak, pair, atr14)

    return MarketState(
        pair=pair,
        state=state,
        substate=substate,
        direction=direction,
        close_streak=close_streak,
        day_break_counter=day_break_counter,
        hod=hod, lod=lod,
        how=how, low_of_week=low_of_week,
        hos=hos, los=los,
        hom=hom, lom=lom,
        hcom=hcom, lcom=lcom,
        cib=cib,
        cib_direction=cib_direction,
        atr14=atr14,
        contraction_ratio=contraction_ratio,
        box_size=price_to_pips(box_size, pair),
        trap=trap,
        basket_rank=0,          # Set by rank_basket()
        classified_at=datetime.combine(as_of, datetime.min.time()).replace(tzinfo=ET),
    )


def _detect_trap(
    ohlcv: pd.DataFrame,
    close_streak: int,
    pair: str,
    atr14: float,
) -> TrapAnalysis:
    """Identify trapped traders from recent streak reversal."""
    if close_streak > 0:
        # Recent pump — longs may be trapped if now rolling over
        trap_level   = float(ohlcv["close"].iloc[-abs(close_streak)-1:-1].max())
        stop_cluster = float(ohlcv["high"].iloc[-abs(close_streak)-1:-1].max()) + 2 * get_pip_size(pair)
        trapped_side = "LONGS_TRAPPED"
    elif close_streak < 0:
        trap_level   = float(ohlcv["close"].iloc[-abs(close_streak)-1:-1].min())
        stop_cluster = float(ohlcv["low"].iloc[-abs(close_streak)-1:-1].min()) - 2 * get_pip_size(pair)
        trapped_side = "SHORTS_TRAPPED"
    else:
        return TrapAnalysis("NONE", 0.0, 0.0, "LOW")

    confidence = "HIGH" if abs(close_streak) >= 3 else ("MEDIUM" if abs(close_streak) == 2 else "LOW")
    return TrapAnalysis(trapped_side, trap_level, stop_cluster, confidence)


def rank_basket(
    classifications: dict[str, MarketState],
    basket: list[str],
    setups: list[Setup] | None = None,
) -> list[str]:
    """
    Rank basket pairs. Primary sort key: 14-point setup score (when setups are
    provided). Tie-break: state priority (BREAKOUT > TRENDING) then close_streak
    magnitude. RANGING pairs with no setup are excluded.

    basket_rank == 1 is the apex instrument — the caller must discard every
    other correlated setup in the basket to enforce the 1% risk mandate.
    """
    PRIORITY = {"BREAKOUT": 3, "TRENDING": 2, "RANGING": 0}
    SUBSTATE_BONUS = {"BREAKOUT_DAY_2": 1, "TRENDING_BACK_SIDE": 1}

    # Best 14-point score per pair — 0 when no valid setup was detected
    score_map: dict[str, int] = {}
    if setups:
        for s in setups:
            if s.pair in basket:
                score_map[s.pair] = max(score_map.get(s.pair, 0), s.score)

    ranked = []
    for pair in basket:
        ms = classifications.get(pair)
        if ms is None or ms.state == "RANGING":
            continue
        state_priority = PRIORITY.get(ms.state, 0) + SUBSTATE_BONUS.get(ms.substate, 0)
        setup_score = score_map.get(pair, 0)
        ranked.append((pair, setup_score, state_priority, abs(ms.close_streak)))

    # Primary: 14-point setup score; tie-break: state priority then streak depth
    ranked.sort(key=lambda x: (x[1], x[2], x[3]), reverse=True)

    result = []
    for i, (pair, _, _, _) in enumerate(ranked):
        classifications[pair].basket_rank = i + 1
        result.append(pair)
    return result
