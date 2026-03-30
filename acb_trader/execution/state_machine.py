"""
ACB Trader — Trade State Machine (skill_session_execution.md)
Every active trade moves through states exactly once — no backwards transitions.
"""

from __future__ import annotations
import uuid
from datetime import datetime, date
from acb_trader.config import ET, BREAKEVEN_PIPS, TRAIL_STEP_PIPS
from acb_trader.db.models import Setup, TradeRecord, AccountState
from acb_trader.data.levels import get_pip_size, price_to_pips
from acb_trader.data.calendar import is_in_news_settle_window
from acb_trader.execution.coil import is_two_sided

# Terminal states
TERMINAL = frozenset([
    "STOPPED_OUT", "EXPIRED", "FORCE_CLOSE",
    "BREAKEVEN_CLOSE", "TRAIL_CLOSE", "FULL_TARGET_CLOSE",
])


class ActiveTrade:
    """Wraps a Setup with live state tracking."""

    def __init__(self, setup: Setup, lot_size: float, session: str):
        self.trade_id     = str(uuid.uuid4())
        self.setup        = setup
        self.lot_size     = lot_size
        self.session      = session
        self.state        = "PENDING_ENTRY"
        self.entry_price  = 0.0
        self.entry_time   = None
        self.tranches     = {}          # tranche_label → lot_size_closed
        self.stop_current = setup.stop_price
        self.be_moved     = False
        self.t1_hit       = False
        self.t2_hit       = False
        self.trail_stop   = 0.0   # Set when T2 is hit; Tranche C trails from here

    # ── STATE TRANSITIONS ─────────────────────────────────────────────────────

    def on_fill(self, fill_price: float, fill_time: datetime) -> bool:
        """Accept a fill.  Returns False (and stays PENDING) if the pair
        is inside the 30-min post-MRN settle window — the fill must be
        rejected or the pending order cancelled to avoid slippage."""
        if self.state != "PENDING_ENTRY":
            raise ValueError(f"on_fill called in invalid state: {self.state}")
        if is_in_news_settle_window(self.setup.pair, fill_time):
            print(f"[state_machine] FILL BLOCKED: {self.setup.pair} inside "
                  f"30-min MRN settle window at {fill_time} — staying PENDING")
            return False
        self.state       = "ACTIVE"
        self.entry_price = fill_price
        self.entry_time  = fill_time
        return True

    def on_target_1_hit(self, price: float, time: datetime) -> str | None:
        """Close Tranche A. Move stop to BE if FIVE_STAR."""
        if self.state not in ("ACTIVE",):
            return None
        self.t1_hit  = True
        self.state   = "PARTIAL_EXIT" if self.setup.trade_type == "FIVE_STAR_SCALABLE" else "FORCE_CLOSE"
        return self.state

    def on_target_2_hit(self, price: float, time: datetime):
        if self.state == "PARTIAL_EXIT":
            self.t2_hit    = True
            self.state     = "PARTIAL_EXIT_2"
            # Initialise trail stop at T2 hit price; Tranche C now trails from here
            self.trail_stop = price

    def on_stop_hit(self, price: float, time: datetime) -> str:
        if self.be_moved:
            self.state = "BREAKEVEN_CLOSE"
        else:
            self.state = "STOPPED_OUT"
        return self.state

    def on_trail_stop_hit(self, price: float, time: datetime) -> str:
        self.state = "TRAIL_CLOSE"
        return self.state

    # ── TRAILING STOP HELPERS ─────────────────────────────────────────────────

    def should_advance_trail(self, current_price: float) -> bool:
        """
        True when price has moved TRAIL_STEP_PIPS beyond the current trail level,
        meaning the trail stop should be advanced.
        Only relevant once T2 is hit (PARTIAL_EXIT_2 state).
        """
        if self.state != "PARTIAL_EXIT_2" or self.trail_stop == 0.0:
            return False
        pip = get_pip_size(self.setup.pair)
        if self.setup.direction == "LONG":
            return current_price >= self.trail_stop + TRAIL_STEP_PIPS * pip
        else:
            return current_price <= self.trail_stop - TRAIL_STEP_PIPS * pip

    def advance_trail(self, current_price: float):
        """
        Move trail stop forward by TRAIL_STEP_PIPS to lock in more profit.
        Trail is always TRAIL_STEP_PIPS behind current price in trade direction.
        """
        pip = get_pip_size(self.setup.pair)
        if self.setup.direction == "LONG":
            new_level = current_price - TRAIL_STEP_PIPS * pip
            if new_level > self.trail_stop:
                self.trail_stop = new_level
        else:
            new_level = current_price + TRAIL_STEP_PIPS * pip
            if self.trail_stop == 0.0 or new_level < self.trail_stop:
                self.trail_stop = new_level

    def is_trail_stop_hit(self, current_price: float) -> bool:
        """True when current price has touched or crossed the trailing stop."""
        if self.state != "PARTIAL_EXIT_2" or self.trail_stop == 0.0:
            return False
        if self.setup.direction == "LONG":
            return current_price <= self.trail_stop
        else:
            return current_price >= self.trail_stop

    def on_target_3_hit(self, price: float, time: datetime) -> str:
        self.state = "FULL_TARGET_CLOSE"
        return self.state

    def on_force_close(self, price: float, time: datetime) -> str:
        self.state = "FORCE_CLOSE"
        return self.state

    def on_expired(self) -> str:
        self.state = "EXPIRED"
        return self.state

    # ── LIVE CHECKS ───────────────────────────────────────────────────────────

    def should_move_to_be(self, current_price: float) -> bool:
        """
        Move stop to breakeven when a 15-min candle has closed
        BREAKEVEN_PIPS in our favour.
        """
        if self.be_moved or self.entry_price == 0:
            return False
        pip = get_pip_size(self.setup.pair)
        profit_pips = price_to_pips(
            abs(current_price - self.entry_price), self.setup.pair
        )
        return profit_pips >= BREAKEVEN_PIPS

    def check_two_sided(self, bars_15min, entry_price: float) -> bool:
        return is_two_sided(bars_15min, entry_price, self.setup.pair)

    def is_terminal(self) -> bool:
        return self.state in TERMINAL

    # ── BUILD RECORD ──────────────────────────────────────────────────────────

    def to_record(self, exit_price: float, exit_time: datetime) -> TradeRecord:
        if self.entry_price == 0:
            raise ValueError("Trade never filled — cannot create record")
        pip = get_pip_size(self.setup.pair)
        direction_sign = 1 if self.setup.direction == "LONG" else -1
        pips = price_to_pips(exit_price - self.entry_price, self.setup.pair) * direction_sign
        r_mult = round(pips / (self.setup.risk_pips or 1), 2)

        return TradeRecord(
            trade_id     = self.trade_id,
            pair         = self.setup.pair,
            pattern      = self.setup.pattern,
            direction    = self.setup.direction,
            trade_type   = self.setup.trade_type,
            score        = self.setup.score,
            session      = self.session,
            entry_price  = self.entry_price,
            entry_time   = self.entry_time,
            stop_price   = self.setup.stop_price,
            lot_size     = self.lot_size,
            target_1     = self.setup.target_1,
            target_2     = self.setup.target_2,
            target_3     = self.setup.target_3,
            exit_price   = exit_price,
            exit_time    = exit_time,
            terminal_state = self.state,
            pips         = round(pips, 1),
            r_multiple   = r_mult,
            notes        = self.setup.notes,
        )
