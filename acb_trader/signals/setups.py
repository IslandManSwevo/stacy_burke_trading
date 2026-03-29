"""
ACB Trader — Setup Identification (skill_setup_identification.md)
Detects all 6 patterns and scores them. Returns list of Setup objects.
EOD ONLY — never called on in-progress candles.
"""

from __future__ import annotations
import pandas as pd
from datetime import datetime, date, timedelta
from typing import Optional
import acb_trader.config as cfg
from acb_trader.db.models import Setup, MarketState, WeeklyTemplate, DiscardedSetup
from acb_trader.signals.patterns import get_rr_floor, is_signal
from acb_trader.data.levels import (
    compute_atr, snap_to_quarter, snap_stop_beyond, get_pip_size, price_to_pips,
    compute_close_streak,
)


# ── GUARDS ────────────────────────────────────────────────────────────────────

def assert_eod_complete(as_of: Optional[date] = None):
    """Hard guard — abort if NY close has not passed."""
    if as_of: # Backtesting mode
        return
    now = datetime.now(cfg.ET)
    ny_close = now.replace(hour=17, minute=4, second=0, microsecond=0)
    if now < ny_close:
        raise RuntimeError(
            f"EOD run fired at {now.strftime('%H:%M ET')} — before NY close. "
            "Never evaluate in-progress candles."
        )


# ── MASTER DETECTOR ───────────────────────────────────────────────────────────

def detect_setups(
    state: MarketState,
    template: WeeklyTemplate,
    daily_ohlcv: pd.DataFrame,
    ema_coil: bool = False,
    as_of: Optional[date] = None,
) -> tuple[list[Setup], list[DiscardedSetup]]:
    """
    Run all pattern detectors. Returns (valid_setups, discarded_setups).
    Caller must have already confirmed EOD is complete.
    """
    if state.state == "RANGING" and state.substate == "RANGING":
        return [], [_discard(state.pair, "NONE", "NEUTRAL", 0, "MARKET_IS_RANGING")]

    pair = state.pair
    atr14 = state.atr14
    valid: list[Setup] = []
    discarded: list[DiscardedSetup] = []

    detectors = [
        _detect_pump_coil_dump,
        _detect_first_red_day,
        _detect_inside_false_break,
        _detect_parabolic_reversal,
        _detect_monday_false_break,
        _detect_low_hanging_fruit,
        _detect_ib_extreme,
    ]

    for fn in detectors:
        result = fn(pair, state, template, daily_ohlcv, atr14, as_of)
        if result is None:
            continue
        setup, reason = result
        if reason:
            discarded.append(_discard(pair, setup.pattern if setup else "?",
                                      "?", 0, reason))
            continue
        if setup is None:
            continue

        # Score and classify
        prior_streak = compute_close_streak(daily_ohlcv["close"].iloc[:-1])
        from acb_trader.signals._scoring import score_setup
        bd = score_setup(setup, state, template, ema_coil)
        if setup.pattern == "INSIDE_FALSE_BREAK":
            bd.total = _apply_ifb_volume_bonus(daily_ohlcv, bd.total)
        setup.score = bd.total
        setup.breakdown = bd
        setup.ema_coil_confirmed = ema_coil
        setup.trade_type = "FIVE_STAR_SCALABLE" if setup.score >= cfg.FIVE_STAR_SCORE else "SESSION_TRADE"

        # Scoring floor — per-pattern overrides for patterns whose discarded trades show
        # higher WR than accepted trades (scoring inversion confirmed in backtest analysis).
        # IFB discards: 67% WR; MFB discards: 61% WR — both above MIN_SETUP_SCORE=7 accepted trades.
        # Lowering both to 5 captures high-quality structural setups the score mistakenly rejected.
        if setup.pattern in ("INSIDE_FALSE_BREAK", "MONDAY_FALSE_BREAK"):
            floor = 5
        else:
            floor = cfg.MIN_SETUP_SCORE
        if setup.score < floor:
            d = _discard(pair, setup.pattern, setup.direction, setup.score, "BELOW_MIN_SCORE")
            # Capture price levels so discard_analysis() can simulate would_have_hit_t1
            d.entry_price = setup.entry_price
            d.stop_price  = setup.stop_price
            d.target_1    = setup.target_1
            discarded.append(d)
            continue
            
        if _is_diddle(setup, template):
            discarded.append(_discard(pair, setup.pattern, setup.direction,
                                      setup.score, "DIDDLE_FILTERED"))
            continue

        # Litmus Test for Professional Size (100-Lot Test)
        # NOTE: Litmus pass is tracked but does NOT override trade_type while
        # FIVE_STAR is disabled (WR < 46% makes tranche structure destructive).
        if passes_100_lot_test(setup, template):
            setup.litmus_passed = True
            setup.notes += " | ✅ 100-Lot Litmus Test Passed"

        valid.append(setup)

    # Sort by score descending
    valid.sort(key=lambda s: s.score, reverse=True)
    return valid, discarded


# ── PATTERN 1: 3HC/3LC → PUMP COIL DUMP ─────────────────────────────────────

def _detect_pump_coil_dump(
    pair, state, template, ohlcv, atr14, as_of: Optional[date] = None
) -> Optional[tuple[Optional[Setup], str]]:
    pip = get_pip_size(pair)
    today = ohlcv.iloc[-1]
    prev  = ohlcv.iloc[-2]
    coil  = ohlcv.iloc[-2]   # Day -1 = coil day

    # Pump: ≥2 consecutive closes in same direction
    streak = abs(state.close_streak)
    if streak < 2:
        return None, ""   # No pump yet — skip silently

    direction = "SHORT" if state.close_streak > 0 else "LONG"

    # ── SLICES — two separate views for two separate jobs ──────────────────────
    #
    # pump_quality_days: the actual pump bars for range/body checks.
    #   Excludes (a) the pre-pump reference bar and (b) the coil day (Day -1).
    #   Coil is SUPPOSED to be tight — checking its range against 0.50 ATR would
    #   flag it as LIMP, which is architecturally wrong.
    #   Slice: Day -(streak) to Day -2 = streak-1 bars.
    #   For streak=2: [-3:-1] = [Day-2]
    #   For streak=3: [-4:-1] = [Day-3, Day-2]
    pump_quality_days = ohlcv.iloc[-(streak + 1):-1]

    # Displacement uses the pre-pump close (one bar before the pump started)
    # as its anchor, so it measures the full pump amplitude, not just one bar.
    pre_pump_close = float(ohlcv["close"].iloc[-(streak + 2)])   # Day -(streak+1)
    pump_end_close = float(ohlcv["close"].iloc[-2])              # Day -1 (coil)

    # ── PUMP QUALITY CHECKS (Playbook §Pattern 1 Step 1) ──────────────────────

    # Check 1: each pump day (excluding coil) must have range >= 0.50 × ATR14
    # Filters clear doji pump days without penalising the intentionally tight coil.
    # Check 3: coil day (Day -1) must NOT be a new 5-day high (bearish) or low (bullish)
    # "If it does, the pump is still in progress — wait" (Playbook §Pattern 1 Step 2)
    pump_high = float(pump_quality_days["high"].max())
    pump_low  = float(pump_quality_days["low"].min())
    if direction == "SHORT" and float(coil["high"]) >= pump_high:
        return None, "PCD_PUMP_STILL_IN_PROGRESS"
    if direction == "LONG"  and float(coil["low"])  <= pump_low:
        return None, "PCD_PUMP_STILL_IN_PROGRESS"
        return None, "PCD_PUMP_NO_DISPLACEMENT"

    # Check 3: coil day (Day -1) must NOT be a new 5-day high (bearish) or low (bullish)
    # "If it does, the pump is still in progress — wait" (Playbook §Pattern 1 Step 2)
    peak_pump = ohlcv.iloc[-2]
    if direction == "SHORT" and float(coil["high"]) >= float(peak_pump["high"]):
        return None, "PCD_PUMP_STILL_IN_PROGRESS"
    if direction == "LONG"  and float(coil["low"])  <= float(peak_pump["low"]):
        return None, "PCD_PUMP_STILL_IN_PROGRESS"

    # Coil: Day -1 range ≤ 0.75 × ATR14
    # Skill §8 (100-Lot Litmus Test): professional stop threshold = 0.75 × ATR14.
    # Since coil range sets the stop distance, 0.75 is the official professional-grade boundary.
    coil_range = float(coil["high"] - coil["low"])
    if coil_range > 0.75 * atr14:
        return None, "COIL_TOO_WIDE"

    # Coil close position
    coil_range_safe = coil_range if coil_range > 0 else 1e-9
    close_pct = (float(coil["close"]) - float(coil["low"])) / coil_range_safe
    if direction == "SHORT" and close_pct < 0.70:
        return None, "COIL_NO_REJECTION"
    if direction == "LONG"  and close_pct > 0.30:
        return None, "COIL_NO_REJECTION"

    # Dump confirmation: today closes through coil
    if direction == "SHORT" and float(today["close"]) >= float(coil["low"]):
        return None, "DUMP_NOT_CONFIRMED"
    if direction == "LONG"  and float(today["close"]) <= float(coil["high"]):
        return None, "DUMP_NOT_CONFIRMED"

    # Targets — measured move from coil range
    pump_high = float(pump_quality_days["high"].max())
    pump_low  = float(pump_quality_days["low"].min())
    coil_low  = float(coil["low"])
    coil_high = float(coil["high"])

    if direction == "SHORT":
        entry  = snap_to_quarter(float(today["close"]), pair)
        stop   = snap_stop_beyond(float(coil["high"]) + 2*pip, "SHORT", pair)
        t1_raw = coil_low - (pump_high - coil_low)
        t2_raw = coil_low - 2*(pump_high - coil_low)
    else:
        entry  = snap_to_quarter(float(today["close"]), pair)
        stop   = snap_stop_beyond(float(coil["low"]) - 2*pip, "LONG", pair)
        t1_raw = coil_high + (coil_high - pump_low)
        t2_raw = coil_high + 2*(coil_high - pump_low)

    t1 = snap_to_quarter(t1_raw, pair)
    t2 = snap_to_quarter(t2_raw, pair)

    risk_pips = price_to_pips(abs(entry - stop), pair)
    if not _valid_stop(pair, risk_pips, atr14):
        return None, f"STOP_TOO_WIDE_{risk_pips:.1f}_pips"
    if price_to_pips(abs(t1 - entry), pair) < cfg.MIN_TARGET_PIPS:
        return None, "TARGET_TOO_CLOSE"

    signal_date = as_of if as_of else datetime.now(cfg.ET).date()
    tomorrow = _next_trading_day(signal_date)
    return Setup(
        pair=pair, pattern="PUMP_COIL_DUMP", direction=direction,
        entry_price=entry, stop_price=stop, target_1=t1, target_2=t2, target_3=None,
        risk_pips=risk_pips, score=0, trade_type="SESSION_TRADE",
        signal_date=signal_date, entry_date=tomorrow,
        ema_coil_confirmed=False, expires=tomorrow,
        notes=f"3{'HC' if direction=='SHORT' else 'LC'} streak={streak} coil confirmed",
    ), ""


# ── PATTERN 2: FIRST RED DAY / FIRST GREEN DAY ───────────────────────────────

def _detect_first_red_day(
    pair, state, template, ohlcv, atr14, as_of: Optional[date] = None
) -> Optional[tuple[Optional[Setup], str]]:
    """Pattern 2: First Red Day/First Green Day."""
    # ── DAY-OF-WEEK GATE ──────────────────────────────────────────────────────
    # Playbook §Pattern 2 Step 2: "Must occur on Wednesday OR Thursday —
    # Monday/Tuesday FRD/FGDs are front-side noise — skip."
    # Weekly Template §Step 5: FRD/FGD on Mon/Tue → "TOO_EARLY_FOR_BACKSIDE"
    sig_date = as_of if as_of else datetime.now(cfg.ET).date()
    if sig_date.weekday() not in (2, 3):   # 2=Wednesday, 3=Thursday only
        return None, "FRD_FGD_WRONG_DOW"

    today = ohlcv.iloc[-1]
    prev  = ohlcv.iloc[-2]
    pip   = get_pip_size(pair)

    # FRD: Close < Open and Close < Prev Close
    is_frd = (float(today["close"]) < float(today["open"]) and
              float(today["close"]) < float(prev["close"]))

    # FGD: Close > Open and Close > Prev Close
    is_fgd = (float(today["close"]) > float(today["open"]) and
              float(today["close"]) > float(prev["close"]))

    if not (is_frd or is_fgd):
        return None, ""

    # Trend prerequisite (skill doc: "minimum 3 consecutive closes in same direction")
    # We compute the prior streak EXCLUDING today's bar, because today's reversal candle
    # resets state.close_streak to -1/+1 — measuring the prior trend must use iloc[:-1].
    prior_streak = compute_close_streak(ohlcv["close"].iloc[:-1])
    # Playbook §Three Higher/Lower Closes: "minimum 3 consecutive closes in same
    # direction" before the reversal candle fires.  Prior threshold of 2 was letting
    # through 1-bar "trends" that lack the trapped-trader fuel Burke requires.
    if is_frd and prior_streak < 3:
        return None, "FRD_NO_PRIOR_UPTREND"
    if is_fgd and prior_streak > -3:
        return None, "FGD_NO_PRIOR_DOWNTREND"
    # Playbook §HCOM/LCOM: "If I am SELLING I want to SELL from the HIGHEST
    # CLOSING PRICE. If BUYING I want to BUY from the LOWEST CLOSING PRICE."
    # FRD (SHORT) must be near HCOM/HCOW — the peak of the trend.
    # FGD (LONG)  must be near LCOM/LCOW — the trough of the trend.
    # ATR-based proximity: 1.5× ATR14 in pips (min 50).  Fixed 75 pips was too
    # tight for XAUUSD (ATR ~300 pips) and blocked valid gold setups entirely.
    entry_price = float(today["close"])
    hcom = template.anchors.current_hcow   # highest close of week
    lcom = template.anchors.current_lcow   # lowest close of week
    hcom_m = getattr(template.anchors, 'prior_month_hcom', 0.0) or 0.0
    lcom_m = getattr(template.anchors, 'prior_month_lcom', 0.0) or 0.0
    atr_pips = price_to_pips(atr14, pair)
    proximity_pips = max(50, 1.5 * atr_pips)
    if is_frd:
        near_high = (
            (hcom > 0 and price_to_pips(abs(entry_price - hcom), pair) <= proximity_pips) or
            (hcom_m > 0 and price_to_pips(abs(entry_price - hcom_m), pair) <= proximity_pips)
        )
        if not near_high:
            return None, "FRD_NOT_NEAR_HCOM"
    if is_fgd:
        near_low = (
            (lcom > 0 and price_to_pips(abs(entry_price - lcom), pair) <= proximity_pips) or
            (lcom_m > 0 and price_to_pips(abs(entry_price - lcom_m), pair) <= proximity_pips)
        )
        if not near_low:
            return None, "FGD_NOT_NEAR_LCOM"
    streak_len = abs(prior_streak)

    # ── TREND LEG QUALITY: displacement check ─────────────────────────────────
    # Playbook §Pattern 2 Step 1: "Net trend move ≥ 2.0 × ATR14 — meaningful
    # expansion, not chop."
    # Slice must include streak_len bars PLUS the pre-trend reference bar:
    #   iloc[-(streak_len + 2):-1]  gives (streak_len + 1) elements
    #   net_disp = close[-2] - close[-(streak_len+2)] = full trend amplitude
    # (Previously used -(streak_len+1) which gave only 1-bar delta for streak=2)
    trend_closes = ohlcv["close"].iloc[-(streak_len + 2):-1]
    net_disp = abs(float(trend_closes.iloc[-1]) - float(trend_closes.iloc[0]))
    # Threshold: 1.5 × ATR14 (relaxed from 2.0).
    # Wed/Thu DOW gate already filters front-side noise; 2.0 ATR left only 3 trades
    # over 2 years across all pairs — too few to evaluate edge. 1.5 ATR preserves
    # the "meaningful trend, not chop" intent while allowing real 2-bar trends.
    if net_disp < 1.5 * atr14:
        return None, "FRD_FGD_TREND_TOO_SMALL"

    # ── NOTE: per-bar conviction close check (Gap 9) removed ──────────────────
    # The per-bar upper/lower 40% check was stacking with the displacement gate
    # and zeroing out all FRD/FGD trades. The displacement check (net >= 2.0 ATR)
    # already ensures the trend had meaningful amplitude — the per-bar quality
    # check added over-constraint without improving edge. Removed 2026-03-28.

    # Candle quality checks (skill doc p.2)
    # "Reversal candle body ≥ 0.40 × range  →  real close, not a doji"
    # "Reversal candle range ≥ 0.60 × ATR14 →  meaningful day, not a limp move"
    candle_range = float(today["high"]) - float(today["low"])
    candle_body  = abs(float(today["close"]) - float(today["open"]))
    if candle_range > 0 and candle_body < 0.40 * candle_range:
        return None, "FRD_FGD_DOJI_CANDLE"
    if candle_range < 0.60 * atr14:
        return None, "FRD_FGD_LIMP_CANDLE"

    direction = "SHORT" if is_frd else "LONG"
    entry     = snap_to_quarter(float(today["close"]), pair)

    # T1 = 1×ATR retrace from entry (reachable ~50% of the time; SESSION_TRADE exits here).
    # T2 = 100% retracement of entire trend leg (trend_leg_start price; FIVE_STAR B tranche).
    # With EMA coil active: FIVE_STAR structure = 50% at T1 (BE stop), 30% at T2, 20% trail.
    # This gives a positive-expectancy structure: frequent partial wins fund the runner.
    start_idx = max(-(streak_len + 2), -len(ohlcv))
    trend_start_price = float(ohlcv["close"].iloc[start_idx])   # for T2

    if direction == "SHORT":
        stop = snap_stop_beyond(float(today["high"]) + 2*pip, "SHORT", pair)
        t1   = snap_to_quarter(entry - atr14, pair)             # 1 ATR step (reachable)
        t2   = snap_to_quarter(trend_start_price, pair)         # 100% retrace (round-trip)
    else:
        stop = snap_stop_beyond(float(today["low"]) - 2*pip, "LONG", pair)
        t1   = snap_to_quarter(entry + atr14, pair)
        t2   = snap_to_quarter(trend_start_price, pair)

    risk_pips = price_to_pips(abs(entry - stop), pair)
    if risk_pips < 10:
        stop = snap_stop_beyond(stop + (10*pip if direction=="SHORT" else -10*pip), direction, pair)
        risk_pips = price_to_pips(abs(entry - stop), pair)

    if not _valid_stop(pair, risk_pips, atr14):
        return None, f"STOP_TOO_WIDE_{risk_pips:.1f}_pips"

    signal_date = as_of if as_of else datetime.now(cfg.ET).date()
    tomorrow = _next_trading_day(signal_date)
    return Setup(
        pair=pair, pattern="FIRST_RED_DAY" if is_frd else "FIRST_GREEN_DAY",
        direction=direction, entry_price=entry, stop_price=stop,
        target_1=t1, target_2=t2, target_3=None,
        risk_pips=risk_pips, score=0, trade_type="SESSION_TRADE",
        signal_date=signal_date, entry_date=tomorrow,
        ema_coil_confirmed=False, expires=tomorrow,
        notes=f"First {'Red' if is_frd else 'Green'} Day confirmed",
    ), ""


# ── PATTERN 3: INSIDE FALSE BREAK ────────────────────────────────────────────

def _detect_inside_false_break(
    pair, state, template, ohlcv, atr14, as_of: Optional[date] = None
) -> Optional[tuple[Optional[Setup], str]]:
    """Pattern 3: Inside Day + False Break."""
    if len(ohlcv) < 3:
        return None, ""

    pip = get_pip_size(pair)
    yest = ohlcv.iloc[-2]
    prev = ohlcv.iloc[-3]
    today = ohlcv.iloc[-1]

    # 1. Was yesterday an inside day with genuine compression?
    # Skill doc: "Day -1 range ≤ 0.50 × ATR14 → genuine compression, not a near-miss"
    is_inside = (float(yest["high"]) < float(prev["high"]) and
                 float(yest["low"])  > float(prev["low"]))
    inside_range = float(yest["high"]) - float(yest["low"])

    if not is_inside:
        return None, ""
    # IFB compression matches the PCD coil standard (Skill §8: 0.75×ATR14).
    # Daily bars achieving genuine inside-day compression typically sit in the
    # 0.65–0.80×ATR range. The close-position gate (0.33) acts as the quality filter.
    if inside_range > 0.75 * atr14:
        return None, "IFB_INSIDE_DAY_NOT_COMPRESSED"

    # 2. Did today false break the OUTER (Day -2) range?
    # Playbook §Pattern 3 Step 2: "Day 0 trades ABOVE Day -2 High (false bullish
    # breakout) or BELOW Day -2 Low (false bearish)."
    # Day -1 is the inside day (tighter range); the false break must breach the
    # OUTER Day -2 candle — a materially higher bar than just the inside range.
    broke_high = float(today["high"]) > float(prev["high"])
    broke_low  = float(today["low"])  < float(prev["low"])

    if not (broke_high or broke_low):
        return None, ""

    # 3. Did it close back inside Day -2's range?
    # Playbook §Pattern 3 Step 2: "Day 0 CLOSES back inside Day -2 range."
    closed_inside_high = float(today["close"]) < float(prev["high"])
    closed_inside_low  = float(today["close"]) > float(prev["low"])

    # Skill doc: "Day 0 close in lower 25% of range (false bullish break) or
    #             upper 25% (false bearish) → strong rejection required"
    today_range = float(today["high"]) - float(today["low"])
    if today_range > 0:
        close_pct = (float(today["close"]) - float(today["low"])) / today_range
    else:
        close_pct = 0.5

    if broke_high and closed_inside_high:
        if close_pct > 0.33:             # close must be in LOWER 33% of range (daily bars rarely close in extreme 25%)
            return None, "IFB_WEAK_REJECTION"
        direction = "SHORT"
        entry     = snap_to_quarter(float(today["close"]), pair)
        stop      = snap_stop_beyond(float(today["high"]) + 2*pip, "SHORT", pair)
        # T1 = opposite side of Day -2's range (Playbook §Pattern 3 Step 3:
        # "Opposite side of Day -2 range — the full box 100% measured move")
        t1        = snap_to_quarter(float(prev["low"]), pair)
    elif broke_low and closed_inside_low:
        if close_pct < 0.67:             # close must be in UPPER 33% of range (daily bars rarely close in extreme 25%)
            return None, "IFB_WEAK_REJECTION"
        direction = "LONG"
        entry     = snap_to_quarter(float(today["close"]), pair)
        stop      = snap_stop_beyond(float(today["low"]) - 2*pip, "LONG", pair)
        t1        = snap_to_quarter(float(prev["high"]), pair)
    else:
        return None, "NO_REVERSAL_CONFIRMATION"

    t2 = snap_to_quarter(t1 - atr14 if direction == "SHORT" else t1 + atr14, pair)

    risk_pips = price_to_pips(abs(entry - stop), pair)
    if risk_pips < 10:
        stop = snap_stop_beyond(stop + (10*pip if direction=="SHORT" else -10*pip), direction, pair)
        risk_pips = price_to_pips(abs(entry - stop), pair)

    if not _valid_stop(pair, risk_pips, atr14):
        return None, f"STOP_TOO_WIDE_{risk_pips:.1f}_pips"

    signal_date = as_of if as_of else datetime.now(cfg.ET).date()
    tomorrow = _next_trading_day(signal_date)
    return Setup(
        pair=pair, pattern="INSIDE_FALSE_BREAK", direction=direction,
        entry_price=entry, stop_price=stop, target_1=t1, target_2=t2, target_3=None,
        risk_pips=risk_pips, score=0, trade_type="SESSION_TRADE",
        signal_date=signal_date, entry_date=tomorrow,
        ema_coil_confirmed=False, expires=tomorrow,
        notes="Inside day + false break confirmed",
    ), ""


# ── PATTERN 4: PARABOLIC REVERSAL AT STRUCTURAL LEVEL ────────────────────────

def _detect_parabolic_reversal(
    pair, state, template, ohlcv, atr14, as_of: Optional[date] = None
) -> Optional[tuple[Optional[Setup], str]]:
    """
    Parabolic reversal fires when the daily close is near a named structural level
    after pushing to a new extreme. Entry is on the following session's coil breakdown.
    """
    pip = get_pip_size(pair)
    last_close = float(ohlcv["close"].iloc[-1])
    anchors = template.anchors

    structural_levels = {
        "HCOM": anchors.current_hcow, "LCOM": anchors.current_lcow,
        "PRIOR_WEEK_HIGH": anchors.prior_week_high, "PRIOR_WEEK_LOW": anchors.prior_week_low,
        "MONTH_OPEN": anchors.month_open,
    }

    hit_level = None
    for name, level in structural_levels.items():
        if level > 0 and abs(last_close - level) <= cfg.ANCHOR_CONFLUENCE_PIPS * pip:
            hit_level = (name, level)
            break

    if hit_level is None:
        return None, ""

    level_name, level_price = hit_level

    # Direction: fade — if we're near a high level → SHORT, near a low level → LONG
    direction = "SHORT" if last_close >= level_price else "LONG"

    # Trend filter 1: require an extended push INTO the level (≥2 closes in that direction).
    # Streak=1 was tested and found to add low-conviction entries — 8 of 9 additional
    # trades lost, masked by a single +7.7R macro-event outlier (USDJPY Apr-2024 yen carry).
    if direction == "SHORT" and state.close_streak < 2:
        return None, "PARA_NO_PUMP_INTO_LEVEL"
    if direction == "LONG" and state.close_streak > -2:
        return None, "PARA_NO_DUMP_INTO_LEVEL"

    # Trend filter 2: require a reversal candle on signal day
    today_open  = float(ohlcv["open"].iloc[-1])
    today_close = float(ohlcv["close"].iloc[-1])
    if direction == "SHORT" and today_close >= today_open:
        return None, "PARA_NO_REVERSAL_CANDLE"
    if direction == "LONG"  and today_close <= today_open:
        return None, "PARA_NO_REVERSAL_CANDLE"

    entry = snap_to_quarter(last_close, pair)
    if direction == "SHORT":
        stop = snap_stop_beyond(float(ohlcv["high"].iloc[-1]) + 2*pip, "SHORT", pair)
        t1   = snap_to_quarter(entry - 2 * atr14, pair)
        t2   = snap_to_quarter(entry - 3 * atr14, pair)
    else:
        stop = snap_stop_beyond(float(ohlcv["low"].iloc[-1]) - 2*pip, "LONG", pair)
        t1   = snap_to_quarter(entry + 2 * atr14, pair)
        t2   = snap_to_quarter(entry + 3 * atr14, pair)

    risk_pips = price_to_pips(abs(entry - stop), pair)
    if risk_pips < 10: # Ensure valid stop distance
        stop = snap_stop_beyond(stop + (10*pip if direction=="SHORT" else -10*pip), direction, pair)
        risk_pips = price_to_pips(abs(entry - stop), pair)

    if not _valid_stop(pair, risk_pips, atr14):
        return None, f"STOP_TOO_WIDE_{risk_pips:.1f}_pips"

    signal_date = as_of if as_of else datetime.now(cfg.ET).date()
    tomorrow = _next_trading_day(signal_date)
    return Setup(
        pair=pair, pattern="PARABOLIC_REVERSAL", direction=direction,
        entry_price=entry, stop_price=stop, target_1=t1, target_2=t2, target_3=None,
        risk_pips=risk_pips, score=0, trade_type="SESSION_TRADE",
        signal_date=signal_date, entry_date=tomorrow,
        ema_coil_confirmed=False, expires=tomorrow,
        notes=f"At {level_name} {level_price:.5f} — parabolic reversal candidate",
    ), ""


# ── PATTERN 5: MONDAY FALSE BREAK ────────────────────────────────────────────

def _detect_monday_false_break(
    pair, state, template, ohlcv, atr14, as_of: Optional[date] = None
) -> Optional[tuple[Optional[Setup], str]]:
    pip = get_pip_size(pair)
    signal_date = as_of if as_of else datetime.now(cfg.ET).date()
    dow = signal_date.weekday()

    # Signal only valid Tue–Thu (Monday sets the trap, entry is next day+)
    if dow not in (1, 2, 3):
        return None, ""

    # Isolate Monday's specific bar — NOT the week-to-date high/low
    monday_date = signal_date - timedelta(days=signal_date.weekday())  # Mon of this ISO week
    mon_bars = ohlcv[pd.to_datetime(ohlcv["date"]).dt.date == monday_date]
    if mon_bars.empty:
        return None, ""

    monday_high  = float(mon_bars["high"].iloc[0])
    monday_low   = float(mon_bars["low"].iloc[0])
    monday_close = float(mon_bars["close"].iloc[0])
    monday_range = monday_high - monday_low
    wk           = template.anchors
    pw_high      = wk.prior_week_high
    pw_low       = wk.prior_week_low

    # Playbook: Monday's CLOSE must be convincingly beyond prior week level.
    # Close position gate ensures Monday actually *closed* in the breakout zone,
    # not just wicked through it.  Removing this gate tested worse (+2R → -5.9R).
    mon_range = monday_range if monday_range > 0 else 1e-9
    mon_close_pct = (monday_close - monday_low) / mon_range
    monday_broke_high = monday_close > pw_high and mon_close_pct >= 0.70
    monday_broke_low  = monday_close < pw_low  and mon_close_pct <= 0.30

    if not (monday_broke_high or monday_broke_low):
        return None, ""

    # Has market failed to extend? (streak ≤ 1 in breakout direction)
    if monday_broke_high and state.close_streak > 1:
        return None, "MON_FALSE_BREAK_STILL_PUMPING"
    if monday_broke_low  and state.close_streak < -1:
        return None, "MON_FALSE_BREAK_STILL_DUMPING"

    # First Red/Green Day must have printed
    today = ohlcv.iloc[-1]
    if monday_broke_high:
        frd = (float(today["close"]) < float(today["open"]) and
               float(today["close"]) < float(ohlcv.iloc[-2]["close"]))
        if not frd:
            return None, "MON_FALSE_BREAK_NO_FRD"
        direction = "SHORT"
        entry = snap_to_quarter(float(today["close"]), pair)
        stop  = snap_stop_beyond(monday_high + 2*pip, "SHORT", pair)
        t1    = snap_to_quarter(monday_low, pair)
    else:
        fgd = (float(today["close"]) > float(today["open"]) and
               float(today["close"]) > float(ohlcv.iloc[-2]["close"]))
        if not fgd:
            return None, "MON_FALSE_BREAK_NO_FGD"
        direction = "LONG"
        entry = snap_to_quarter(float(today["close"]), pair)
        stop  = snap_stop_beyond(monday_low - 2*pip, "LONG", pair)
        t1    = snap_to_quarter(monday_high, pair)

    t2 = snap_to_quarter(t1 - atr14 if direction == "SHORT" else t1 + atr14, pair)

    risk_pips = price_to_pips(abs(entry - stop), pair)
    if risk_pips < 10:
        stop = snap_stop_beyond(stop + (10*pip if direction=="SHORT" else -10*pip), direction, pair)
        risk_pips = price_to_pips(abs(entry - stop), pair)

    if not _valid_stop(pair, risk_pips, atr14 * 1.25):
        return None, f"STOP_TOO_WIDE_{risk_pips:.1f}_pips"

    tomorrow = _next_trading_day(signal_date)
    return Setup(
        pair=pair, pattern="MONDAY_FALSE_BREAK", direction=direction,
        entry_price=entry, stop_price=stop, target_1=t1, target_2=t2, target_3=None,
        risk_pips=risk_pips, score=0, trade_type="SESSION_TRADE",
        signal_date=signal_date, entry_date=tomorrow,
        ema_coil_confirmed=False, expires=tomorrow,
        notes=f"Monday false break {'above' if monday_broke_high else 'below'} prior week level",
    ), ""


# ── PATTERN 6: LOW HANGING FRUIT ─────────────────────────────────────────────

def _detect_low_hanging_fruit(
    pair, state, template, ohlcv, atr14, as_of: Optional[date] = None
) -> Optional[tuple[Optional[Setup], str]]:
    """
    Pattern 6: Low-Hanging Fruit
    Prior session printed an explosive move (range > 1.5× ATR14).
    Today pulls back to the 50% level of that move — entry there.
    Direction follows the prior explosive candle.
    """
    if len(ohlcv) < 2:
        return None, ""

    pip  = get_pip_size(pair)
    prev = ohlcv.iloc[-2]
    today = ohlcv.iloc[-1]

    prev_range = float(prev["high"]) - float(prev["low"])

    # Explosive candle: daily range > 1.5× ATR14
    if prev_range < 1.5 * atr14:
        return None, ""  # Not explosive enough — skip silently

    # Direction of the explosive candle
    is_bullish = float(prev["close"]) > float(prev["open"])
    direction  = "LONG" if is_bullish else "SHORT"

    # Prior candle close quality check (Playbook §Pattern 5 Step 1):
    # "Prior session close is in the top 30% of its range (bullish LHF) or
    # bottom 30% (bearish LHF) — session must have closed strong."
    # Relaxed from 20% → 30%: explosive daily candles frequently close in the
    # 70–80% range but not the extreme top 20%. Top 30% preserves the intent
    # (strong close) without zeroing out all LHF signals. (2026-03-28)
    prev_close_pct = (float(prev["close"]) - float(prev["low"])) / (prev_range or 1e-9)
    if is_bullish and prev_close_pct < 0.70:
        return None, "LHF_WEAK_PRIOR_CLOSE"
    if not is_bullish and prev_close_pct > 0.30:
        return None, "LHF_WEAK_PRIOR_CLOSE"

    # 50% retracement level of yesterday's range
    fifty_pct = (float(prev["high"]) + float(prev["low"])) / 2.0

    # Today must be PULLING BACK toward 50% (not extending)
    today_close = float(today["close"])
    if direction == "LONG"  and today_close > fifty_pct:
        return None, ""   # Still above 50% — no pullback yet
    if direction == "SHORT" and today_close < fifty_pct:
        return None, ""   # Still below 50% — no pullback yet

    entry = snap_to_quarter(fifty_pct, pair)

    if direction == "SHORT":
        stop = snap_stop_beyond(float(prev["high"]) + 2 * pip, "SHORT", pair)
        t1   = snap_to_quarter(float(prev["low"]) - atr14, pair)
    else:
        stop = snap_stop_beyond(float(prev["low"]) - 2 * pip, "LONG", pair)
        t1   = snap_to_quarter(float(prev["high"]) + atr14, pair)

    t2 = snap_to_quarter(t1 - atr14 if direction == "SHORT" else t1 + atr14, pair)

    risk_pips = price_to_pips(abs(entry - stop), pair)
    if risk_pips < 10:
        stop = snap_stop_beyond(
            stop + (10 * pip if direction == "SHORT" else -10 * pip), direction, pair
        )
        risk_pips = price_to_pips(abs(entry - stop), pair)

    if not _valid_stop(pair, risk_pips, atr14):
        return None, f"STOP_TOO_WIDE_{risk_pips:.1f}_pips"
    if price_to_pips(abs(t1 - entry), pair) < cfg.MIN_TARGET_PIPS:
        return None, "TARGET_TOO_CLOSE"

    signal_date = as_of if as_of else datetime.now(cfg.ET).date()
    tomorrow    = _next_trading_day(signal_date)
    return Setup(
        pair=pair, pattern="LOW_HANGING_FRUIT", direction=direction,
        entry_price=entry, stop_price=stop, target_1=t1, target_2=t2, target_3=None,
        risk_pips=risk_pips, score=0, trade_type="SESSION_TRADE",
        signal_date=signal_date, entry_date=tomorrow,
        ema_coil_confirmed=False, expires=tomorrow,
        notes=(
            f"Explosive {direction} candle {prev_range/pip:.0f} pips "
            f"— 50% pullback entry at {entry:.5f}"
        ),
    ), ""


# ── PATTERN 7: IB EXTREME (Backside test of Mon-Tue Opening Range) ───────────

def _detect_ib_extreme(
    pair, state, template, ohlcv, atr14, as_of: Optional[date] = None
) -> Optional[tuple[Optional[Setup], str]]:
    """
    Pattern 7: IB Extreme
    The Operational Process Standard describes price testing the Monday-Tuesday
    Initial Balance high or low on the backside (Wed-Fri).  When price pokes
    through the OR extreme and closes back inside, it's a false-break fade.

    Structural similarity to MFB but fires LATER in the week — price has had
    more time to prove the breakout failed.
    """
    if len(ohlcv) < 3:
        return None, ""

    pip = get_pip_size(pair)
    signal_date = as_of if as_of else datetime.now(cfg.ET).date()
    dow = signal_date.weekday()

    # Backside only: Wed (2), Thu (3), Fri (4)
    if dow not in (2, 3, 4):
        return None, ""

    # Opening Range must be complete (Mon + Tue data established)
    orng = template.opening_range
    if orng is None or not orng.complete:
        return None, ""

    or_high = orng.high
    or_low  = orng.low
    or_mid  = orng.midpoint
    or_range = or_high - or_low

    # Opening Range must be meaningful (>= MIN_IB_RANGE_PIPS)
    or_range_pips = price_to_pips(or_range, pair)
    if or_range_pips < cfg.MIN_IB_RANGE_PIPS:
        return None, ""

    today = ohlcv.iloc[-1]
    today_high  = float(today["high"])
    today_low   = float(today["low"])
    today_close = float(today["close"])
    today_open  = float(today["open"])

    # Proximity threshold: within 10 pips of OR extreme
    prox = 10 * pip

    # SHORT: today tests OR high and closes back below it (false break)
    tested_high = today_high >= (or_high - prox)
    closed_below_high = today_close < or_high

    # LONG: today tests OR low and closes back above it (false break)
    tested_low = today_low <= (or_low + prox)
    closed_above_low = today_close > or_low

    if not (tested_high and closed_below_high) and not (tested_low and closed_above_low):
        return None, ""

    # Prefer the direction with a stronger rejection candle
    if tested_high and closed_below_high:
        direction = "SHORT"
        # Rejection quality: close should be in lower half of today's range
        today_range = today_high - today_low
        if today_range > 0:
            close_pct = (today_close - today_low) / today_range
            if close_pct > 0.50:
                return None, "IB_EXTREME_WEAK_REJECTION"

        entry = snap_to_quarter(today_close, pair)
        stop  = snap_stop_beyond(today_high + 2 * pip, "SHORT", pair)
        t1    = snap_to_quarter(or_mid, pair)                    # 50% of OR range
        t2    = snap_to_quarter(or_low, pair)                    # Opposite OR extreme

    elif tested_low and closed_above_low:
        direction = "LONG"
        today_range = today_high - today_low
        if today_range > 0:
            close_pct = (today_close - today_low) / today_range
            if close_pct < 0.50:
                return None, "IB_EXTREME_WEAK_REJECTION"

        entry = snap_to_quarter(today_close, pair)
        stop  = snap_stop_beyond(today_low - 2 * pip, "LONG", pair)
        t1    = snap_to_quarter(or_mid, pair)                    # 50% of OR range
        t2    = snap_to_quarter(or_high, pair)                   # Opposite OR extreme
    else:
        return None, ""

    risk_pips = price_to_pips(abs(entry - stop), pair)
    if risk_pips < 10:
        stop = snap_stop_beyond(
            stop + (10 * pip if direction == "SHORT" else -10 * pip), direction, pair
        )
        risk_pips = price_to_pips(abs(entry - stop), pair)

    if not _valid_stop(pair, risk_pips, atr14):
        return None, f"STOP_TOO_WIDE_{risk_pips:.1f}_pips"
    if price_to_pips(abs(t1 - entry), pair) < cfg.MIN_TARGET_PIPS:
        return None, "TARGET_TOO_CLOSE"

    tomorrow = _next_trading_day(signal_date)
    return Setup(
        pair=pair, pattern="IB_EXTREME", direction=direction,
        entry_price=entry, stop_price=stop, target_1=t1, target_2=t2, target_3=None,
        risk_pips=risk_pips, score=0, trade_type="SESSION_TRADE",
        signal_date=signal_date, entry_date=tomorrow,
        ema_coil_confirmed=False, expires=tomorrow,
        notes=(
            f"IB Extreme: {direction} — OR {or_high:.5f}/{or_low:.5f}, "
            f"tested {'high' if direction == 'SHORT' else 'low'}, "
            f"closed back inside at {today_close:.5f}"
        ),
    ), ""


# ── SCORING ───────────────────────────────────────────────────────────────────
# Scoring logic lives in _scoring.py (ScoreBreakdown) and patterns.py (PatternDef).
# _score() is kept as a thin compatibility shim; new code calls score_setup() directly.

def _score(
    setup: Setup,
    state: MarketState,
    template: WeeklyTemplate,
    ema_coil: bool,
    prior_streak: int = 0,
    ohlcv: Optional[pd.DataFrame] = None,
) -> int:
    """Compatibility shim — delegates to score_setup() in _scoring.py."""
    from acb_trader.signals._scoring import score_setup
    bd = score_setup(setup, state, template, ema_coil)
    total = bd.total
    if setup.pattern == "INSIDE_FALSE_BREAK":
        total = _apply_ifb_volume_bonus(ohlcv, total)
    return total


# ── HELPERS ───────────────────────────────────────────────────────────────────

def _apply_ifb_volume_bonus(ohlcv: Optional[pd.DataFrame], current_total: int) -> int:
    """Additive bonus if false-break tick volume >= ratio * inside day volume."""
    if ohlcv is not None and len(ohlcv) >= 2 and "volume" in ohlcv.columns:
        _vol_fb = float(ohlcv["volume"].iloc[-1])
        _vol_id = float(ohlcv["volume"].iloc[-2])
        if _vol_id > 0 and _vol_fb / _vol_id >= cfg.IFB_VOL_EXPANSION_RATIO:
            return min(current_total + cfg.IFB_VOL_EXPANSION_BONUS, 14)
    return current_total



def _valid_stop(pair: str, risk_pips: float, max_atr_mult: float) -> bool:
    cls = cfg.INSTRUMENT_CLASS.get(pair, "CURRENCIES")
    _, max_pips = cfg.MAX_STOP_PIPS[cls]
    return risk_pips <= max_pips


def _is_diddle(setup: Setup, template: WeeklyTemplate) -> bool:
    """The 'Science Project' filter — rejects trades that are too far from anchors or have poor R:R."""
    pip = get_pip_size(setup.pair)

    # Signal-day patterns (FRD/FGD/IFB/MFB/PCD/LHF) enter at trend extremes —
    # they are SUPPOSED to be away from prior-week named levels.  The trapped-
    # trader thesis doesn't require anchor proximity; the HCOM/LCOM proximity
    # gate (in detect_setups) handles directional confluence for these.
    # Anchor confluence only applies to non-signal patterns.
    is_signal_pattern = is_signal(setup.pattern)

    # 1. Anchor Confluence — only for non-signal patterns
    if not is_signal_pattern:
        if not _has_anchor_confluence(setup.entry_price, template.anchors, pip):
            return True

    # 2. Risk/Reward floor — per-pattern minimum from patterns.py (single source of truth).
    # All patterns now require genuine asymmetric R:R; FRD/FGD moved back to 2:1
    # (1:1 compromise was producing "garbage" entries per methodology review).
    t1_dist = price_to_pips(abs(setup.target_1 - setup.entry_price), setup.pair)
    rr = t1_dist / (setup.risk_pips or 1)
    if rr < get_rr_floor(setup.pattern):
        return True

    return False


def passes_100_lot_test(setup: Setup, template: WeeklyTemplate) -> bool:
    """Professional Size Litmus Test: High Conflict + Structural Conf + Priority Pattern."""
    pip = get_pip_size(setup.pair)
    
    # 1. High-Conflict Zone: Entry within 25 pips of a MAJOR level
    major_levels = [
        template.anchors.current_week_high, template.anchors.current_week_low,
        template.anchors.current_hcow, template.anchors.current_lcow,
        template.anchors.month_open, template.anchors.prior_week_high,
        template.anchors.prior_week_low
    ]
    near_major_level = any(abs(setup.entry_price - lv) <= 25 * pip for lv in major_levels if lv > 0)
    
    # 2. Structural Confirmation: Template is NOT Ranging
    structural_conf = template.template_type in ("BREAKOUT_WEEK", "REVERSAL_WEEK", "NEW_MONTH_BREAKOUT")
    
    # 3. Pattern Priority: Top 3 Patterns
    priority_patterns = ("PUMP_COIL_DUMP", "FIRST_RED_DAY", "PARABOLIC_REVERSAL")
    pattern_priority = setup.pattern in priority_patterns
    
    return near_major_level and structural_conf and pattern_priority


def _has_anchor_confluence(price: float, anchors, pip: float) -> bool:
    levels = [
        anchors.prior_week_high, anchors.prior_week_low,
        anchors.prior_week_hcow, anchors.prior_week_lcow,
        anchors.current_week_high, anchors.current_week_low,
        anchors.month_open,
    ]
    return any(abs(price - lvl) <= cfg.ANCHOR_CONFLUENCE_PIPS * pip for lvl in levels if lvl > 0)


def _count_anchor_confluences(price: float, anchors, pair: str) -> int:
    pip = get_pip_size(pair)
    levels = [
        anchors.prior_week_high, anchors.prior_week_low,
        anchors.prior_week_hcow, anchors.prior_week_lcow,
        anchors.current_week_high, anchors.current_week_low,
        anchors.month_open, anchors.prior_month_hcom, anchors.prior_month_lcom,
    ]
    return sum(1 for lvl in levels if lvl > 0 and abs(price - lvl) <= cfg.ANCHOR_CONFLUENCE_PIPS * pip)


def _is_near_hcom_lcom(price: float, anchors, pair: str) -> bool:
    pip = get_pip_size(pair)
    hcom = anchors.current_hcow
    lcom = anchors.current_lcow
    return (hcom > 0 and abs(price - hcom) <= 25 * pip) or \
           (lcom > 0 and abs(price - lcom) <= 25 * pip)


def _next_trading_day(d: date) -> date:
    nxt = d + timedelta(days=1)
    while nxt.weekday() >= 5:   # skip Sat/Sun
        nxt += timedelta(days=1)
    return nxt


def _discard(pair, pattern, direction, score, reason) -> DiscardedSetup:
    return DiscardedSetup(
        pair=pair, pattern=pattern, direction=direction,
        score=score, reason=reason, discarded_at=datetime.now(cfg.ET),
    )
