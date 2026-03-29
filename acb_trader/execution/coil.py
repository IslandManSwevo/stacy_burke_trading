"""
ACB Trader — EMA Coil Detection (skill_session_execution.md)
The coil is the MANDATORY entry gate. No trade fires without it.
Runs live on the intraday feed during the session window.
"""

from __future__ import annotations
import pandas as pd
import numpy as np
from acb_trader.config import (
    EMA_COIL_PERIODS, EMA_COIL_TIGHT_MULT, EMA_ENTRY_PERIOD,
    COIL_SIDEWAYS_BARS, TWO_SIDED_PIPS, TWO_SIDED_CANDLES
)
from acb_trader.db.models import CoilState, InitialBalance, Setup
from acb_trader.data.levels import (
    compute_atr, snap_to_quarter, get_pip_size, price_to_pips
)


def compute_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def has_ema_coil_htf(ohlcv_htf: pd.DataFrame, atr14: float) -> bool:
    """
    Higher time frame EMA coil confirmation (daily / 4H).
    All EMAs converging at an extreme = ALL TIME FRAMES ALIGNED.
    """
    if len(ohlcv_htf) < max(EMA_COIL_PERIODS) + 5:
        return False
    closes = ohlcv_htf["close"]
    ema_vals = {p: float(compute_ema(closes, p).iloc[-1]) for p in EMA_COIL_PERIODS}
    spread = max(ema_vals.values()) - min(ema_vals.values())
    coil_tight = spread <= EMA_COIL_TIGHT_MULT * atr14

    # Last 3 bars sideways
    last3_range = float(ohlcv_htf["high"].iloc[-3:].max() - ohlcv_htf["low"].iloc[-3:].min())
    coil_sideways = last3_range <= 2.0 * atr14   # 2×ATR: bars overlapping but not expanding
    return coil_tight and coil_sideways


def wait_for_ema_coil(
    pair: str,
    level: float,
    direction: str,
    bars_15min: pd.DataFrame,
) -> CoilState:
    """
    Monitor 15-min bars for coil formation near `level`.
    Returns CoilState with triggered=True when:
      1. Price within 10 pips of level
      2. EMA spread ≤ 0.5 × ATR14 on 15-min
      3. Price sideways ≥ 3 consecutive 15-min bars
      4. Breakdown bar closes through coil extreme in trade direction
    """
    pip = get_pip_size(pair)
    if len(bars_15min) < max(EMA_COIL_PERIODS) + COIL_SIDEWAYS_BARS + 2:
        return CoilState(False, 0.0, 0.0, 0.0, 0)

    closes = bars_15min["close"]
    atr    = compute_atr(bars_15min, 14)

    ema_vals = {p: float(compute_ema(closes, p).iloc[-1]) for p in EMA_COIL_PERIODS}
    spread   = max(ema_vals.values()) - min(ema_vals.values())

    # Near level check
    last_close = float(closes.iloc[-1])
    near_level = abs(last_close - level) <= 10 * pip

    # Coil tightness
    emas_coiled = spread <= EMA_COIL_TIGHT_MULT * atr

    # Sideways check — last N bars in tight range
    n = COIL_SIDEWAYS_BARS
    recent_high = float(bars_15min["high"].iloc[-n:].max())
    recent_low  = float(bars_15min["low"].iloc[-n:].min())
    sideways    = (recent_high - recent_low) <= 1.0 * atr

    # Breakdown confirmation
    coil_low  = float(bars_15min["low"].iloc[-(n+1):-1].min())
    coil_high = float(bars_15min["high"].iloc[-(n+1):-1].max())

    if direction == "SHORT":
        breakdown = last_close < coil_low
    else:
        breakdown = last_close > coil_high

    bars_sw = sum(
        1 for i in range(-n, 0)
        if abs(float(bars_15min["close"].iloc[i]) - float(bars_15min["close"].iloc[i-1])) <= atr * 0.3
    )

    return CoilState(
        triggered   = near_level and emas_coiled and sideways and breakdown,
        coil_low    = coil_low,
        coil_high   = coil_high,
        ema_spread  = spread,
        bars_sideways = bars_sw,
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
