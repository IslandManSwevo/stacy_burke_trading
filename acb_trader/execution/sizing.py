"""
ACB Trader — Position Sizing & Target Calculation
1% risk per trade. Three Levels targets. Quarter-snapped everything.
"""

from __future__ import annotations
from acb_trader.config import (
    RISK_PER_TRADE_PCT, THREE_LEVELS, INSTRUMENT_CLASS,
    SESSION_TRADE_TRANCHES, FIVE_STAR_TRANCHES,
)
from acb_trader.db.models import Setup
from acb_trader.data.levels import get_pip_size, price_to_pips, snap_to_quarter


def calculate_position_size(
    account_balance: float,
    entry_price: float,
    stop_price: float,
    pair: str,
    pip_value_per_lot: float = 10.0,   # USD per pip per standard lot — override per pair
) -> float:
    """
    Fixed 1% risk position size.
    lot_size = risk_amount / (stop_pips × pip_value)
    """
    risk_amount = account_balance * RISK_PER_TRADE_PCT
    stop_pips   = price_to_pips(abs(entry_price - stop_price), pair)
    if stop_pips == 0:
        return 0.01
    lot_size = risk_amount / (stop_pips * pip_value_per_lot)
    return round(max(lot_size, 0.01), 2)


def get_tranches(trade_type: str) -> dict[str, float]:
    """Return lot allocation proportions for this trade tier."""
    if trade_type == "FIVE_STAR_SCALABLE":
        return FIVE_STAR_TRANCHES   # {"A":0.50,"B":0.30,"C":0.20}
    return SESSION_TRADE_TRANCHES   # {"A":1.00}


def get_three_levels_targets(
    pair: str,
    entry_price: float,
    direction: str,
    ib_range_pips: float | None = None,
) -> dict[str, float]:
    """
    Return Three Levels targets snapped to nearest quarter level.
    Uses IB range if available; falls back to instrument class defaults.
    """
    cls    = INSTRUMENT_CLASS.get(pair, "CURRENCIES")
    levels = THREE_LEVELS[cls]
    pip    = get_pip_size(pair)

    base_pips = ib_range_pips if ib_range_pips else levels["L1"]

    if direction == "SHORT":
        return {
            "L1": snap_to_quarter(entry_price - base_pips * pip, pair),
            "L2": snap_to_quarter(entry_price - levels["L2"] * pip, pair),
            "L3": snap_to_quarter(entry_price - levels.get("L3", levels["L2"]*2) * pip, pair),
        }
    return {
        "L1": snap_to_quarter(entry_price + base_pips * pip, pair),
        "L2": snap_to_quarter(entry_price + levels["L2"] * pip, pair),
        "L3": snap_to_quarter(entry_price + levels.get("L3", levels["L2"]*2) * pip, pair),
    }


def calculate_rr(setup: Setup) -> float:
    """Risk:Reward ratio to Target 1."""
    if setup.risk_pips == 0:
        return 0.0
    t1_pips = price_to_pips(abs(setup.target_1 - setup.entry_price), setup.pair)
    return round(t1_pips / setup.risk_pips, 2)
