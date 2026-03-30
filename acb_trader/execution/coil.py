"""
ACB Trader — EMA Coil Detection (skill_session_execution.md)
The coil is the MANDATORY entry gate. No trade fires without it.
Runs live on the intraday feed during the session window.
"""

from __future__ import annotations
import pandas as pd
import numpy as np
from datetime import datetime
from typing import Optional
from acb_trader.config import (
    ET,
    EMA_COIL_PERIODS, EMA_COIL_TIGHT_MULT, EMA_COIL_DAILY_MULT, EMA_ENTRY_PERIOD,
    COIL_SIDEWAYS_BARS, TWO_SIDED_PIPS, TWO_SIDED_CANDLES,
    COIL_SIDEWAYS_ATR_MULT, COIL_SIDEWAYS_ATR_MULT_DAILY,
)
from acb_trader.db.models import CoilState, InitialBalance, Setup
from acb_trader.data.levels import (
    compute_atr, snap_to_quarter, get_pip_size, price_to_pips
)


def compute_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def has_ema_coil_htf(
    ohlcv_htf: pd.DataFrame,
    atr14: float,
    timeframe: str = "INTRADAY",
) -> bool:
    """
    Higher time frame EMA coil confirmation (daily / 4H).
    All EMAs converging at an extreme = ALL TIME FRAMES ALIGNED.

    timeframe : str
        "DAILY"   → applies the EOD professional-boundary multiplier (0.75 × ATR14).
                    Daily compressions naturally sit between 0.6–0.9× ATR; the 0.75×
                    threshold admits valid PCD coils while rejecting expansion bars.
        "INTRADAY" → strict execution-gate multiplier (0.5 × ATR14).  On a 15-min
                    chart anything wider = chop; do not take that trade.
    """
    if len(ohlcv_htf) < max(EMA_COIL_PERIODS) + 5:
        return False

    # ── Select the correct ATR multiplier for the structural timeframe ──────
    tf_norm = timeframe.upper()
    valid_tfs = {
        "DAILY": (EMA_COIL_DAILY_MULT, COIL_SIDEWAYS_ATR_MULT_DAILY),
        "INTRADAY": (EMA_COIL_TIGHT_MULT, COIL_SIDEWAYS_ATR_MULT),
    }
    if tf_norm not in valid_tfs:
        raise ValueError(f"Invalid timeframe: '{timeframe}'. Expected 'DAILY' or 'INTRADAY'.")

    mult, sw_mult = valid_tfs[tf_norm]

    closes = ohlcv_htf["close"]
    ema_vals = {p: float(compute_ema(closes, p).iloc[-1]) for p in EMA_COIL_PERIODS}
    spread = max(ema_vals.values()) - min(ema_vals.values())
    coil_tight = spread <= mult * atr14

    coil_sideways = last3_range <= sw_mult * atr14
    return coil_tight and coil_sideways


def wait_for_ema_coil(
    pair: str,
    level: float,
    direction: str,
    bars_15min: pd.DataFrame,
    session_close: Optional[datetime] = None,
) -> CoilState:
    """
    Monitor 15-min bars for coil formation near `level`.

    Gate sequence (all must pass to set triggered=True):
      1. Session window — if session_close has passed, returns expired=True immediately.
         No carry-forward to the next session; the setup is cancelled.
      2. Data length guard.
      3. Consecutive coil counter — scans every closed 15-min bar from the most recent
         backwards; increments while EMA spread ≤ EMA_COIL_TIGHT_MULT × ATR14 (0.5×),
         resets to 0 on the first bar that exceeds the threshold.
         Requires COIL_SIDEWAYS_BARS (≥3) consecutive qualifying bars to ARM.
      4. Near-level check — last close within 10 pips of the setup's entry level.
      5. Coil range check — range of the coil bars ≤ COIL_SIDEWAYS_ATR_MULT × ATR.
      6. Breakdown confirmation — last 5-min close through the coil extreme in
         the intended trade direction.
    """
    # ── 1. Session expiration guardrail ──────────────────────────────────────
    if session_close is not None:
        now = datetime.now(ET)
        if now >= session_close:
            return CoilState(triggered=False, coil_low=0.0, coil_high=0.0,
                             ema_spread=0.0, bars_sideways=0, expired=True)

    pip = get_pip_size(pair)
    min_bars = max(EMA_COIL_PERIODS) + COIL_SIDEWAYS_BARS + 2
    if len(bars_15min) < min_bars:
        return CoilState(False, 0.0, 0.0, 0.0, 0)

    closes = bars_15min["close"]
    atr    = compute_atr(bars_15min, 14)

    # ── 2. Build per-bar EMA spread series (single pass, not per-bar recompute) ─
    ema_df     = pd.DataFrame({p: compute_ema(closes, p) for p in EMA_COIL_PERIODS})
    spread_ser = ema_df.max(axis=1) - ema_df.min(axis=1)

    # ── 3. Consecutive coiled-bar counter ────────────────────────────────────
    # Scan backwards from the most recent closed bar. Every bar where
    # EMA spread ≤ EMA_COIL_TIGHT_MULT × ATR14 adds 1 to the counter.
    # The first bar that fails the condition breaks the streak.
    consecutive_coiled = 0
    threshold = EMA_COIL_TIGHT_MULT * atr
    for i in range(len(spread_ser) - 1, -1, -1):
        if spread_ser.iloc[i] <= threshold:
            consecutive_coiled += 1
        else:
            break   # streak broken — do not continue scanning older bars

    coil_armed = consecutive_coiled >= COIL_SIDEWAYS_BARS

    # ── 4. Near level ────────────────────────────────────────────────────────
    last_close = float(closes.iloc[-1])
    near_level = abs(last_close - level) <= 10 * pip

    # ── 5. Coil range (high/low of the consecutively coiled bars) ────────────
    n = max(consecutive_coiled, COIL_SIDEWAYS_BARS)
    n = min(n, len(bars_15min))
    coil_high = float(bars_15min["high"].iloc[-n:].max())
    coil_low  = float(bars_15min["low"].iloc[-n:].min())
    sideways  = (coil_high - coil_low) <= COIL_SIDEWAYS_ATR_MULT * atr

    # ── 6. Breakdown confirmation ─────────────────────────────────────────────
    if direction == "SHORT":
        breakdown = last_close < coil_low
    else:
        breakdown = last_close > coil_high

    current_spread = float(spread_ser.iloc[-1])

    return CoilState(
        triggered     = coil_armed and near_level and sideways and breakdown,
        coil_low      = coil_low,
        coil_high     = coil_high,
        ema_spread    = current_spread,
        bars_sideways = consecutive_coiled,
    )


def check_5min_entry(
    bars_5min: pd.DataFrame,
    direction: str,
) -> tuple[bool, float]:
    """
    5-min chart entry trigger: price closes through the 20 EMA.
    Returns (triggered, ema20_value).
    SHORT: close < EMA20 | LONG: close > EMA20
    Candle must CLOSE through EMA — never enter on a wick.
    """
    if len(bars_5min) < EMA_ENTRY_PERIOD + 2:
        return False, 0.0
    ema20 = float(compute_ema(bars_5min["close"], EMA_ENTRY_PERIOD).iloc[-1])
    last_close = float(bars_5min["close"].iloc[-1])
    if direction == "SHORT":
        triggered = last_close < ema20
    else:
        triggered = last_close > ema20
    return triggered, ema20


def is_two_sided(
    bars_15min: pd.DataFrame,
    entry_price: float,
    pair: str,
) -> bool:
    """
    ACB failure detector. Returns True if price has stalled within
    TWO_SIDED_PIPS of entry for TWO_SIDED_CANDLES consecutive bars → exit.
    """
    pip = get_pip_size(pair)
    threshold = TWO_SIDED_PIPS * pip
    recent = [float(bars_15min["close"].iloc[i]) for i in range(-TWO_SIDED_CANDLES, 0)]
    return all(abs(c - entry_price) < threshold for c in recent)


def compute_initial_balance(
    session: str,
    bars_1min: pd.DataFrame,
    direction: str,
    pair: str,
) -> InitialBalance:
    """IB = first 60 minutes of the session."""
    pip = get_pip_size(pair)
    first_hour = bars_1min.iloc[:60]
    ib_high = float(first_hour["high"].max())
    ib_low  = float(first_hour["low"].min())
    ib_range_pips = price_to_pips(ib_high - ib_low, pair)

    if direction == "SHORT":
        t100 = snap_to_quarter(ib_low  - (ib_high - ib_low), pair)
        t200 = snap_to_quarter(ib_low  - 2 * (ib_high - ib_low), pair)
        t300 = snap_to_quarter(ib_low  - 3 * (ib_high - ib_low), pair)
        r50  = snap_to_quarter(ib_low  + (ib_high - ib_low) * 0.5, pair)
    else:
        t100 = snap_to_quarter(ib_high + (ib_high - ib_low), pair)
        t200 = snap_to_quarter(ib_high + 2 * (ib_high - ib_low), pair)
        t300 = snap_to_quarter(ib_high + 3 * (ib_high - ib_low), pair)
        r50  = snap_to_quarter(ib_high - (ib_high - ib_low) * 0.5, pair)

    return InitialBalance(
        session=session,
        ib_high=ib_high, ib_low=ib_low,
        ib_range_pips=ib_range_pips,
        target_100pct=t100, target_200pct=t200, target_300pct=t300,
        retracement_50=r50,
    )
