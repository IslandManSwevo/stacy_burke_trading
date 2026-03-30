"""
ACB Trader — Three-Box Institutional Grid Engine
─────────────────────────────────────────────────
"Markets move in three distinct boxes before structural exhaustion."

This module implements the rigid mathematical grid logic anchored to
institutional price levels (Major Round Numbers and Quarter Levels).

Core Concepts
─────────────
1. **25-Pip Box Geometry (Currencies)**
   Price moves between the 00, 25, 50, 75 quarter levels.  Each 25-pip
   gap is one "box."  Three consecutive boxes = 75-pip expansion = the
   standard intraday exhaustion metric for FX.

2. **HTF Macro Targets (Three Levels of Rise or Fall)**
   When a daily bar closes in breakout, the macro projection is:
     L1 = 75 pips  |  L2 = 150 pips  |  L3 = 250 pips  |  L3_EXT = 300 pips
   These scale by instrument class (gold, oil, indices have wider grids).

3. **Execution Protocol**
   Once three boxes are driven through AND price pins into a Major Round
   Number or Quarter Level, the system waits for the 15-min EMA coil to
   confirm potential energy is loaded.  The 5-min 20 EMA trigger fires
   the entry.  Breakout traders trapped through three structural levels
   are forced to liquidate — the market "Ain't Coming Back."

Public API
──────────
    from acb_trader.data.three_boxes import (
        snap_to_grid,
        measure_box_expansion,
        project_three_levels,
        is_at_box_exhaustion,
        ThreeBoxAnalysis,
    )
"""

from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Optional

import pandas as pd

from acb_trader.data.levels import (
    get_pip_size, price_to_pips, snap_to_quarter,
    grid_level_above, grid_level_below
)
import acb_trader.config as cfg


# ── CONSTANTS ────────────────────────────────────────────────────────────────

# Box size in pips by instrument class.
# Currencies: 25-pip grid (institutional 00/25/50/75 levels).
# Gold: 5.0 dollar grid ($5 = 50 pips at pip=0.1), aligns to round $5 levels.
# Oil: 50-cent grid (50 pips at pip=0.01 → $0.50 levels).
# Indexes: 25-point grid (25 index-pips at pip=1.0).
BOX_SIZE_PIPS = cfg.BOX_SIZE_PIPS   # canonical source: config.py

# Number of boxes that constitute one expansion / exhaustion cycle.
BOXES_PER_EXPANSION = 3


# ── DATA MODEL ───────────────────────────────────────────────────────────────

@dataclass
class ThreeBoxAnalysis:
    """
    Complete three-box projection from a breakout anchor.

    anchor_price : float
        The nearest quarter-level grid line at the consolidation boundary
        where the breakout originated.
    direction : str
        "BULLISH" (expansion up) or "BEARISH" (expansion down).
    box_size_pips : int
        Size of each box in pips (25 for currencies).
    pip_size : float
        Raw pip value for the instrument (0.0001, 0.01, 0.1, 1.0).

    Intraday box levels (3 × box_size_pips):
        box_1, box_2, box_3 — the three structural expansion targets.
        exhaustion_level = box_3 — where the trap is FULLY built.

    HTF macro targets (from config.THREE_LEVELS):
        htf_l1, htf_l2, htf_l3, htf_l3_ext — daily-chart expansion targets.

    boxes_completed : int
        How many of the three intraday boxes price has driven through (0–3).
    at_exhaustion : bool
        True when boxes_completed == 3 (all volume trapped).
    """
    pair:             str
    anchor_price:     float
    direction:        str        # "BULLISH" | "BEARISH"
    box_size_pips:    int
    pip_size:         float

    # Intraday 3-box grid levels
    box_1:            float
    box_2:            float
    box_3:            float      # = exhaustion level
    exhaustion_level: float      # alias for box_3

    # HTF macro targets
    htf_l1:           float
    htf_l2:           float
    htf_l3:           float
    htf_l3_ext:       Optional[float]

    # Live state
    boxes_completed:  int   = 0
    at_exhaustion:    bool  = False

    def summary(self) -> str:
        """Human-readable grid for Telegram / logs."""
        arrow = "▲" if self.direction == "BULLISH" else "▼"
        lines = [
            f"Three-Box Grid {arrow} {self.pair}",
            f"  Anchor : {self.anchor_price:.5f}",
            f"  Box 1  : {self.box_1:.5f}  ({self.box_size_pips} pips)",
            f"  Box 2  : {self.box_2:.5f}  ({self.box_size_pips * 2} pips)",
            f"  Box 3  : {self.box_3:.5f}  ({self.box_size_pips * 3} pips) ← EXHAUSTION",
            f"  Boxes through: {self.boxes_completed}/3",
            f"{'  ⚠️ EXHAUSTION — wait for coil' if self.at_exhaustion else ''}",
            f"  HTF L1 : {self.htf_l1:.5f}  ({_level_pips('L1', self.pair)} pips)",
            f"  HTF L2 : {self.htf_l2:.5f}  ({_level_pips('L2', self.pair)} pips)",
            f"  HTF L3 : {self.htf_l3:.5f}  ({_level_pips('L3', self.pair)} pips)",
        ]
        if self.htf_l3_ext is not None:
            lines.append(
                f"  HTF MAX: {self.htf_l3_ext:.5f}  ({_level_pips('L3_EXT', self.pair)} pips)"
            )
        return "\n".join(lines)


# ── GRID FUNCTIONS ───────────────────────────────────────────────────────────

def snap_to_grid(price: float, pair: str) -> float:
    """
    Snap a price to the nearest institutional quarter-level grid line.
    00 / 25 / 50 / 75 for currencies (scaled by pip size).

    This is the foundational anchor operation: your grid CANNOT start
    from arbitrary prices.  Every projection begins from the closest
    Major Round Number or Quarter Level.

    Delegates to levels.snap_to_quarter(), which uses the same grid.
    """
    return snap_to_quarter(price, pair)


def snap_to_grid_above(price: float, pair: str) -> float:
    """Snap to the quarter-level grid line ABOVE (or equal to) price."""
    return grid_level_above(price, pair)


def snap_to_grid_below(price: float, pair: str) -> float:
    """Snap to the quarter-level grid line BELOW (or equal to) price."""
    return grid_level_below(price, pair)


def get_box_size_pips(pair: str) -> int:
    """Return the institutional box size in pips for the given instrument."""
    cls = cfg.INSTRUMENT_CLASS.get(pair, "CURRENCIES")
    return BOX_SIZE_PIPS.get(cls, 25)


# ── THREE-BOX PROJECTION ────────────────────────────────────────────────────

def project_three_boxes(
    pair: str,
    anchor_price: float,
    direction: str,
    current_price: Optional[float] = None,
) -> ThreeBoxAnalysis:
    """
    Build the complete three-box expansion grid from a consolidation anchor.

    Parameters
    ----------
    pair : str
        Instrument symbol (e.g. "EURUSD").
    anchor_price : float
        The consolidation boundary / breakout level.  MUST be snapped to
        the nearest quarter-level grid (00/25/50/75) before calling, or
        this function will snap it automatically.
    direction : str
        "BULLISH" (measuring expansion UP) or "BEARISH" (measuring DOWN).
    current_price : float, optional
        If provided, computes boxes_completed and at_exhaustion.

    Returns
    -------
    ThreeBoxAnalysis
        Full grid with intraday boxes + HTF macro targets.
    """
    pip = get_pip_size(pair)
    cls = cfg.INSTRUMENT_CLASS.get(pair, "CURRENCIES")
    box_pips = BOX_SIZE_PIPS.get(cls, 25)
    box_price = box_pips * pip

    # Anchor MUST sit on the grid — snap it.
    anchor = snap_to_grid(anchor_price, pair)

    # ── Intraday 3-box projection ────────────────────────────────────────
    if direction == "BULLISH":
        box_1 = round(anchor + box_price, 5)
        box_2 = round(anchor + 2 * box_price, 5)
        box_3 = round(anchor + 3 * box_price, 5)
    else:
        box_1 = round(anchor - box_price, 5)
        box_2 = round(anchor - 2 * box_price, 5)
        box_3 = round(anchor - 3 * box_price, 5)

    # ── HTF macro targets (from config.THREE_LEVELS) ─────────────────────
    levels = cfg.THREE_LEVELS.get(cls, cfg.THREE_LEVELS["CURRENCIES"])
    if direction == "BULLISH":
        htf_l1 = round(anchor + levels["L1"] * pip, 5)
        htf_l2 = round(anchor + levels["L2"] * pip, 5)
        htf_l3 = round(anchor + levels.get("L3", levels.get("L3_EXT", 250)) * pip, 5)
        htf_l3_ext = round(anchor + levels["L3_EXT"] * pip, 5) if "L3_EXT" in levels else None
    else:
        htf_l1 = round(anchor - levels["L1"] * pip, 5)
        htf_l2 = round(anchor - levels["L2"] * pip, 5)
        htf_l3 = round(anchor - levels.get("L3", levels.get("L3_EXT", 250)) * pip, 5)
        htf_l3_ext = round(anchor - levels["L3_EXT"] * pip, 5) if "L3_EXT" in levels else None

    # ── Live state: how many boxes has price driven through? ──────────────
    boxes_completed = 0
    if current_price is not None:
        boxes_completed = count_boxes_through(
            anchor, current_price, direction, box_price
        )

    return ThreeBoxAnalysis(
        pair=pair,
        anchor_price=anchor,
        direction=direction,
        box_size_pips=box_pips,
        pip_size=pip,
        box_1=box_1,
        box_2=box_2,
        box_3=box_3,
        exhaustion_level=box_3,
        htf_l1=htf_l1,
        htf_l2=htf_l2,
        htf_l3=htf_l3,
        htf_l3_ext=htf_l3_ext,
        boxes_completed=min(boxes_completed, BOXES_PER_EXPANSION),
        at_exhaustion=(boxes_completed >= BOXES_PER_EXPANSION),
    )


# ── BOX COUNTING ─────────────────────────────────────────────────────────────

def count_boxes_through(
    anchor: float,
    current_price: float,
    direction: str,
    box_price: float,
) -> int:
    """
    Count how many complete boxes price has traversed from the anchor.

    A box is "completed" when price has moved a full box_price distance
    past the grid line.  Partial penetration does NOT count.

    Example (BULLISH, anchor=1.0800, box=0.0025):
      price 1.0820 → 0 boxes (not past 1.0825)
      price 1.0826 → 1 box   (past 1.0825)
      price 1.0855 → 2 boxes (past 1.0850, not past 1.0875)
      price 1.0876 → 3 boxes → EXHAUSTION
    """
    if box_price <= 0:
        return 0
    if direction == "BULLISH":
        distance = current_price - anchor
    else:
        distance = anchor - current_price

    if distance <= 0:
        return 0
    return int(distance / box_price)


def measure_box_expansion(
    pair: str,
    ohlcv: pd.DataFrame,
    direction: str,
    lookback: int = 10,
) -> tuple[float, int]:
    """
    Measure the current box expansion from recent price action.

    Identifies the consolidation boundary (the grid level price broke out
    from) and counts how many boxes the expansion has covered.

    Parameters
    ----------
    pair : str
        Instrument symbol.
    ohlcv : pd.DataFrame
        Daily OHLCV data (at least `lookback` rows).
    direction : str
        "BULLISH" or "BEARISH" — which side of the consolidation to measure.
    lookback : int
        Number of bars to scan for the consolidation low/high.

    Returns
    -------
    (anchor_price, boxes_completed)
        anchor_price : The grid-snapped consolidation boundary.
        boxes_completed : Number of full 25-pip boxes driven through.
    """
    pip = get_pip_size(pair)
    cls = cfg.INSTRUMENT_CLASS.get(pair, "CURRENCIES")
    box_pips = BOX_SIZE_PIPS.get(cls, 25)
    box_price = box_pips * pip

    recent = ohlcv.iloc[-lookback:]

    if direction == "BULLISH":
        # Anchor = lowest low of the consolidation, snapped to grid below
        consol_low = float(recent["low"].min())
        anchor = snap_to_grid_below(consol_low, pair)
        current = float(ohlcv["high"].iloc[-1])
    else:
        # Anchor = highest high of the consolidation, snapped to grid above
        consol_high = float(recent["high"].max())
        anchor = snap_to_grid_above(consol_high, pair)
        current = float(ohlcv["low"].iloc[-1])

    boxes = count_boxes_through(anchor, current, direction, box_price)
    return anchor, min(boxes, BOXES_PER_EXPANSION + 1)  # cap at 4 for parabolic


def is_at_box_exhaustion(
    pair: str,
    anchor: float,
    current_price: float,
    direction: str,
) -> bool:
    """
    Returns True if price has completed 3 full boxes from the anchor.

    This is the critical gate: when True, the institutional trap is BUILT.
    The system must sit on its hands and wait for the 15-minute EMA coil.
    Taking a trade IN THE MIDDLE of these boxes is Garbage Trading.
    """
    pip = get_pip_size(pair)
    cls = cfg.INSTRUMENT_CLASS.get(pair, "CURRENCIES")
    box_pips = BOX_SIZE_PIPS.get(cls, 25)
    box_price = box_pips * pip

    boxes = count_boxes_through(anchor, current_price, direction, box_price)
    return boxes >= BOXES_PER_EXPANSION


def find_breakout_anchor(
    pair: str,
    ohlcv: pd.DataFrame,
    direction: str,
    streak_len: int,
) -> float:
    """
    Find the grid-snapped consolidation boundary from which the current
    trend broke out.

    For a BEARISH move (3HC → dump), the anchor is the grid level ABOVE
    the pre-trend high (where breakout longs entered).
    For a BULLISH move (3LC → rally), the anchor is the grid level BELOW
    the pre-trend low (where breakout shorts entered).

    This is the price where the institutional trap was SET — the level
    from which we measure three boxes of expansion.
    """
    # Pre-trend bar: the bar before the streak started
    pre_trend_idx = max(-(streak_len + 2), -len(ohlcv))

    if direction == "BEARISH":
        # The pump drove price UP through 3 levels → find the pre-pump LOW
        # as the consolidation base, then the expansion drives DOWN from
        # the pump high.
        high_slice = ohlcv["high"].iloc[pre_trend_idx:-1]
        pump_high = float(high_slice.max() if not high_slice.empty else ohlcv["high"].iloc[-2])
        return snap_to_grid_above(pump_high, pair)
    else:
        # The dump drove price DOWN through 3 levels → find the pre-dump HIGH
        # as the consolidation base, then the expansion drives UP from
        # the dump low.
        low_slice = ohlcv["low"].iloc[pre_trend_idx:-1]
        dump_low = float(low_slice.min() if not low_slice.empty else ohlcv["low"].iloc[-2])
        return snap_to_grid_below(dump_low, pair)


def compute_three_box_targets(
    pair: str,
    entry_price: float,
    direction: str,
    anchor_price: float,
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """
    Compute HTF target_3, and optionally refine target_1/target_2 using
    the three-level macro projection.

    When a setup fires at box exhaustion, the REVERSAL targets are
    measured as the trapped-volume liquidation driving price back
    through those same three boxes:
      T1 = 1 box retrace   (25-pip snap level = quick profit)
      T2 = 2 boxes retrace (50-pip snap level = runner)
      T3 = HTF L1           (75-pip full retrace through all 3 boxes)

    Returns (t1_refined, t2_refined, t3_htf) — all grid-snapped.
    Caller should use these to override or supplement pattern-specific targets.
    """
    pip = get_pip_size(pair)
    cls = cfg.INSTRUMENT_CLASS.get(pair, "CURRENCIES")
    levels = cfg.THREE_LEVELS.get(cls, cfg.THREE_LEVELS["CURRENCIES"])

    # HTF T3: the L1 macro target from the entry (full 3-box retrace)
    l1_pips = levels["L1"]
    if direction == "LONG":   # fading a bearish exhaustion
        t3 = snap_to_grid(entry_price + l1_pips * pip, pair)
    else:                     # fading a bullish exhaustion
        t3 = snap_to_grid(entry_price - l1_pips * pip, pair)

    # Refined intraday targets using the box grid
    box_pips = BOX_SIZE_PIPS.get(cls, 25)
    box_price = box_pips * pip
    if direction == "LONG":
        t1_ref = snap_to_grid(entry_price + box_price, pair)
        t2_ref = snap_to_grid(entry_price + 2 * box_price, pair)
    else:
        t1_ref = snap_to_grid(entry_price - box_price, pair)
        t2_ref = snap_to_grid(entry_price - 2 * box_price, pair)

    return t1_ref, t2_ref, t3


# ── DIAGNOSTICS ──────────────────────────────────────────────────────────────

def _level_pips(level_key: str, pair: str) -> int:
    """Return the pip distance for a named THREE_LEVELS key."""
    cls = cfg.INSTRUMENT_CLASS.get(pair, "CURRENCIES")
    levels = cfg.THREE_LEVELS.get(cls, cfg.THREE_LEVELS["CURRENCIES"])
    return levels.get(level_key, 0)


def annotate_setup_notes(notes: str, analysis: ThreeBoxAnalysis) -> str:
    """Append three-box grid info to setup notes for diagnostics."""
    arrow = "▲" if analysis.direction == "BULLISH" else "▼"
    grid_note = (
        f" | 📐 3-Box {arrow}: anchor={analysis.anchor_price:.5f}, "
        f"boxes={analysis.boxes_completed}/{BOXES_PER_EXPANSION}"
    )
    if analysis.at_exhaustion:
        grid_note += " ⚠️ EXHAUSTION"
    return notes + grid_note
