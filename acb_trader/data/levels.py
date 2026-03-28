"""
ACB Trader — Named Structural Level Tracking
HOD/LOD/HOW/LOW/HOS/LOS/HOM/LOM/HCOM/LCOM — all updated live from intraday feed.
"""

from __future__ import annotations
import math
import pandas as pd
from datetime import datetime
from acb_trader.db.models import SessionLevels
from acb_trader.config import ET, INSTRUMENT_CLASS


# ── PIP UTILITIES ─────────────────────────────────────────────────────────────

def get_pip_size(pair: str) -> float:
    """Return the pip size for a given pair."""
    jpy_pairs = ["USDJPY", "GBPJPY", "EURJPY", "AUDJPY", "CADJPY", "NZDJPY", "CHFJPY"]
    index_pairs = ["SP500", "NAS100", "DJ30", "USOIL", "UKOIL"]
    gold = ["XAUUSD"]
    if pair in jpy_pairs:
        return 0.01
    if pair in index_pairs:
        return 1.0
    if pair in gold:
        return 0.1
    return 0.0001


def get_pip_multiplier(pair: str) -> float:
    """Multiply price diff by this to get pip distance."""
    return 1.0 / get_pip_size(pair)


def price_to_pips(price_diff: float, pair: str) -> float:
    return abs(price_diff) * get_pip_multiplier(pair)


# ── QUARTER LEVEL SNAPPING ────────────────────────────────────────────────────

def snap_to_quarter(price: float, pair: str) -> float:
    """Snap price to nearest 00/25/50/75 quarter level."""
    pip = get_pip_size(pair)
    level_size = 25 * pip
    return round(round(price / level_size) * level_size, 5)


def snap_stop_beyond(price: float, direction: str, pair: str) -> float:
    """
    Snap stop to the quarter level BEYOND the wick.
    SHORT stop → next quarter ABOVE. LONG stop → next quarter BELOW.
    """
    pip = get_pip_size(pair)
    level_size = 25 * pip
    if direction == "SHORT":
        return math.ceil(price / level_size) * level_size
    return math.floor(price / level_size) * level_size


# ── ATR ───────────────────────────────────────────────────────────────────────

def compute_atr(ohlcv: pd.DataFrame, period: int = 14) -> float:
    """Wilder smoothing ATR using True Range."""
    high = ohlcv["high"]
    low  = ohlcv["low"]
    close_prev = ohlcv["close"].shift(1)
    tr = pd.concat([
        high - low,
        (high - close_prev).abs(),
        (low  - close_prev).abs()
    ], axis=1).max(axis=1)
    # Wilder smoothing
    atr = tr.ewm(alpha=1/period, adjust=False).mean()
    return float(atr.iloc[-1])


# ── CLOSE STREAK ─────────────────────────────────────────────────────────────

def compute_close_streak(closes: pd.Series) -> int:
    """
    Count consecutive closes in the same direction from most recent backwards.
    +3 = 3 higher closes | -2 = 2 lower closes | 0 = unchanged/doji
    """
    streak = 0
    direction = None
    vals = closes.values
    for i in range(len(vals) - 1, 0, -1):
        diff = vals[i] - vals[i - 1]
        if diff == 0:
            break
        d = 1 if diff > 0 else -1
        if direction is None:
            direction = d
        elif d != direction:
            break
        streak += direction
    return streak


# ── DAY BREAK COUNTER ─────────────────────────────────────────────────────────

def compute_day_break_counter(ohlcv: pd.DataFrame) -> int:
    """
    Count consecutive days that BROKE the prior day's HOD or LOD intraday.
    +2 = two consecutive days above prior HOD | -2 = two below prior LOD
    Outside days use close direction as tiebreaker.
    """
    days = ohlcv.iloc[-5:].reset_index(drop=True)
    counter = 0
    direction = None

    for i in range(len(days) - 1, 0, -1):
        today     = days.iloc[i]
        yesterday = days.iloc[i - 1]
        broke_high = today["high"]  > yesterday["high"]
        broke_low  = today["low"]   < yesterday["low"]

        if broke_high and not broke_low:
            day_dir = 1
        elif broke_low and not broke_high:
            day_dir = -1
        elif broke_high and broke_low:
            day_dir = 1 if today["close"] > yesterday["close"] else -1
        else:
            break

        if direction is None:
            direction = day_dir
        elif day_dir != direction:
            break
        counter += direction

    return counter


# ── SESSION LEVEL MANAGEMENT ──────────────────────────────────────────────────

def build_session_levels(
    intraday_1min: pd.DataFrame,
    session_1min: pd.DataFrame,
    prior_session: SessionLevels | None,
    current_week_rows: pd.DataFrame,
    current_month_rows: pd.DataFrame,
) -> SessionLevels:
    """Build a fresh SessionLevels snapshot from intraday feeds."""
    hod = float(intraday_1min["high"].max())
    lod = float(intraday_1min["low"].min())
    how = float(current_week_rows["high"].max())
    low_of_week = float(current_week_rows["low"].min())
    hos = float(session_1min["high"].max()) if len(session_1min) else hod
    los = float(session_1min["low"].min())  if len(session_1min) else lod

    prior_hos = prior_session.hos if prior_session else hos
    prior_los = prior_session.los if prior_session else los
    prior_hod = prior_session.hod if prior_session else hod
    prior_lod = prior_session.lod if prior_session else lod

    return SessionLevels(
        hod=hod, lod=lod,
        how=how, low_of_week=low_of_week,
        hos=hos, los=los,
        prior_hod=prior_hod, prior_lod=prior_lod,
        prior_hos=prior_hos, prior_los=prior_los,
    )


def update_session_levels(levels: SessionLevels, bar: dict) -> SessionLevels:
    """Update rolling highs/lows on every new 1-min bar."""
    levels.hod = max(levels.hod, bar["high"])
    levels.lod = min(levels.lod, bar["low"])
    levels.how = max(levels.how, bar["high"])
    levels.low_of_week = min(levels.low_of_week, bar["low"])
    levels.hos = max(levels.hos, bar["high"])
    levels.los = min(levels.los, bar["low"])
    return levels


def reset_hos_los(levels: SessionLevels) -> SessionLevels:
    """Called at each new session open — preserves HOD/HOW, resets HOS/LOS."""
    levels.prior_hos = levels.hos
    levels.prior_los = levels.los
    levels.prior_hod = levels.hod
    levels.prior_lod = levels.lod
    levels.hos = 0.0
    levels.los = float("inf")
    return levels
