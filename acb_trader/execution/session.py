"""
ACB Trader — Intraday Session Supervisor

Zero-poll, clock-synchronised execution engine implementing the ACB
15-min coil accumulation → 5-min drop-down entry protocol.

  Phase 1 — WATCHING  (:00/:15/:30/:45)
      Pull the CLOSED 15-min OHLCV array.
      Compute 9/20/50 EMA spread and ATR14 on the last closed candle.
      EMA spread ≤ 0.5 × ATR14 → coil_count += 1; else → reset to 0.
      coil_count ≥ COIL_SIDEWAYS_BARS (3) → transition to ARMED.

  Phase 2 — ARMED  (:00/:05/:10/...)
      Drop to 5-min polling.
      Track 5-min 20 EMA only.
      5-min candle closes THROUGH 20 EMA in HTF direction → fire MARKET order.

  Phase 3 — MANAGING  (:00/:15/:30/:45)
      State machine lifecycle: BE, T1/T2/T3, trail, two-sided exit.

No intra-candle polling. No emotional diddling. Wire the loop to the clock.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional

from acb_trader.config import (
    ET,
    COIL_SIDEWAYS_BARS,
    EMA_COIL_PERIODS,
    EMA_COIL_TIGHT_MULT,
    SESSION_WINDOWS,
)
from acb_trader.data.levels import compute_atr, get_pip_size
from acb_trader.db.models import Setup, TradeRecord
from acb_trader.execution.coil import check_5min_entry, compute_ema, is_two_sided
from acb_trader.execution.orders import MT5Client
from acb_trader.execution.sizing import calculate_position_size
from acb_trader.execution.state_machine import ActiveTrade
from acb_trader.notifications.telegram import (
    send_state_change,
    send_trade_debrief,
)

log = logging.getLogger(__name__)

# 250 ms settle buffer after the hard clock strike — guarantees the candle
# is fully closed and available in MT5 before the query fires.
_CANDLE_SETTLE_SECS = 0.25


# ── PHASE ENUM ────────────────────────────────────────────────────────────────

class Phase(Enum):
    WATCHING = "WATCHING"   # Accumulating consecutive coiled 15-min bars
    ARMED    = "ARMED"      # 3+ bars confirmed → dropped to 5-min trigger
    FILLED   = "FILLED"     # Market order live; state machine managing
    EXPIRED  = "EXPIRED"    # Session window closed — no entry made
    TERMINAL = "TERMINAL"   # Trade reached a terminal state


# ── PER-SETUP MONITOR ─────────────────────────────────────────────────────────

@dataclass
class _Monitor:
    setup: Setup
    session: str
    session_close_h: int
    session_close_m: int
    coil_count: int = 0
    phase: Phase = Phase.WATCHING
    active_trade: Optional[ActiveTrade] = None
    order_ticket: int = 0


# ── CLOCK HELPERS ─────────────────────────────────────────────────────────────

def _secs_to_next_interval(interval_minutes: int) -> float:
    """
    Exact fractional seconds until the next N-minute hard boundary.
    If we are already within the settle buffer of a boundary, skip to the next one.
    """
    now = datetime.now(ET)
    elapsed = now.minute * 60 + now.second + now.microsecond / 1e6
    interval_secs = interval_minutes * 60
    remainder = elapsed % interval_secs
    gap = interval_secs - remainder
    if gap < _CANDLE_SETTLE_SECS:
        gap += interval_secs
    return gap


def _at_15min_boundary() -> bool:
    return datetime.now(ET).minute % 15 == 0


def _session_expired(m: _Monitor) -> bool:
    now = datetime.now(ET)
    close = now.replace(
        hour=m.session_close_h, minute=m.session_close_m, second=0, microsecond=0
    )
    return now >= close


# ── COIL MATH ─────────────────────────────────────────────────────────────────

def _evaluate_15min_coil(ohlcv) -> tuple[bool, float]:
    """
    Evaluate the last CLOSED 15-min candle for EMA compression.
    Returns (coiled, ema_spread).
    coiled = True when spread ≤ EMA_COIL_TIGHT_MULT (0.5) × ATR14.
    """
    if len(ohlcv) < max(EMA_COIL_PERIODS) + 5:
        return False, 0.0
    closes = ohlcv["close"]
    atr = compute_atr(ohlcv, 14)
    if not atr:
        return False, 0.0
    ema_vals = {p: float(compute_ema(closes, p).iloc[-1]) for p in EMA_COIL_PERIODS}
    spread = max(ema_vals.values()) - min(ema_vals.values())
    return spread <= EMA_COIL_TIGHT_MULT * atr, spread


# ── PHASE HANDLERS ────────────────────────────────────────────────────────────

def _tick_15min(m: _Monitor, feed) -> None:
    """
    WATCHING phase. Runs at every :00/:15/:30/:45.
    Increments coil_count on compression; resets on expansion.
    Transitions to ARMED when coil_count >= COIL_SIDEWAYS_BARS.
    """
    ohlcv = feed.get_15min_bars(m.setup.pair, count=55)
    coiled, spread = _evaluate_15min_coil(ohlcv)

    if coiled:
        m.coil_count += 1
        log.info(
            "[session] %s 15-min coil bar #%d  spread=%.5f",
            m.setup.pair, m.coil_count, spread,
        )
    else:
        if m.coil_count > 0:
            log.info(
                "[session] %s coil streak broken (spread=%.5f) — counter reset",
                m.setup.pair, spread,
            )
        m.coil_count = 0

    if m.coil_count >= COIL_SIDEWAYS_BARS:
        log.info(
            "[session] %s ARMED — %d consecutive coiled bars. Dropping to 5-min.",
            m.setup.pair, m.coil_count,
        )
        m.phase = Phase.ARMED


def _tick_5min(m: _Monitor, feed, client: MT5Client) -> None:
    """
    ARMED phase. Runs at every :00/:05/:10/... boundary.
    Fires a MARKET order the instant a 5-min close breaks the Deathline (5-min 20 EMA).
    """
    ohlcv_5m = feed.get_ohlcv(m.setup.pair, "M5", count=40)
    triggered, ema20 = check_5min_entry(ohlcv_5m, m.setup.direction)

    if not triggered:
        return

    # ── 5-min Deathline confirmed → fire MARKET order ─────────────────────────
    acc = feed.get_account()
    lot_size = calculate_position_size(
        account_balance=acc["balance"],
        entry_price=m.setup.entry_price,
        stop_price=m.setup.stop_price,
        pair=m.setup.pair,
    )
    result = client.place_market_order(m.setup, lot_size)

    if not result.success:
        log.warning("[session] %s market order REJECTED: %s", m.setup.pair, result.message)
        return

    m.order_ticket = result.ticket
    m.active_trade = ActiveTrade(m.setup, lot_size, m.session)

    fill_price = float(ohlcv_5m["close"].iloc[-1])
    fill_time  = datetime.now(ET)
    m.active_trade.on_fill(fill_price, fill_time)
    m.phase = Phase.FILLED

    log.info(
        "[session] %s FILLED @ %.5f  SL=%.5f  T1=%.5f  lots=%.2f  ticket=%d",
        m.setup.pair, fill_price, m.setup.stop_price,
        m.setup.target_1, lot_size, result.ticket,
    )
    send_state_change(m.active_trade, "ACTIVE")


def _tick_manage(m: _Monitor, feed, client: MT5Client) -> Optional[TradeRecord]:
    """
    FILLED phase. Runs at every :00/:15/:30/:45 boundary.
    Handles stop, BE promotion, T1/T2/T3, trail, two-sided failure.
    Returns TradeRecord when the trade reaches a terminal state; None otherwise.
    """
    trade  = m.active_trade
    setup  = m.setup
    pair   = setup.pair
    ticket = m.order_ticket

    ohlcv_15m = feed.get_15min_bars(pair, count=30)
    if len(ohlcv_15m) == 0:
        return None

    current_price = float(ohlcv_15m["close"].iloc[-1])
    now = datetime.now(ET)

    def _close(terminal_fn, price, notify_state) -> TradeRecord:
        terminal_fn(price, now)
        record = trade.to_record(price, now)
        send_state_change(trade, notify_state)
        send_trade_debrief(record)
        m.phase = Phase.TERMINAL
        return record

    # ── Two-sided ACB failure ─────────────────────────────────────────────────
    if trade.check_two_sided(ohlcv_15m, trade.entry_price):
        log.warning("[session] %s TWO-SIDED — force closing", pair)
        client.close_position(ticket, trade.lot_size, pair)
        return _close(trade.on_force_close, current_price, "FORCE_CLOSE")

    # ── Stop hit ──────────────────────────────────────────────────────────────
    stop_hit = (
        (setup.direction == "SHORT" and current_price >= trade.stop_current)
        or (setup.direction == "LONG"  and current_price <= trade.stop_current)
    )
    if stop_hit:
        return _close(trade.on_stop_hit, current_price, trade.state)

    # ── Breakeven promotion ───────────────────────────────────────────────────
    if trade.should_move_to_be(current_price):
        client.modify_stop(ticket, trade.entry_price)
        trade.stop_current = trade.entry_price
        trade.be_moved = True
        log.info("[session] %s stop → breakeven @ %.5f", pair, trade.entry_price)
        send_state_change(trade, "BREAKEVEN_ARMED")

    # ── Target 1 ─────────────────────────────────────────────────────────────
    if not trade.t1_hit:
        t1_hit = (
            (setup.direction == "SHORT" and current_price <= setup.target_1)
            or (setup.direction == "LONG"  and current_price >= setup.target_1)
        )
        if t1_hit:
            tranche_a_pct = 0.5 if setup.trade_type == "FIVE_STAR_SCALABLE" else 1.0
            client.close_position(ticket, round(trade.lot_size * tranche_a_pct, 2), pair)
            new_state = trade.on_target_1_hit(current_price, now)
            send_state_change(trade, new_state or "PARTIAL_EXIT")
            if new_state == "FORCE_CLOSE":   # SESSION_TRADE fully exited at T1
                record = trade.to_record(current_price, now)
                send_trade_debrief(record)
                m.phase = Phase.TERMINAL
                return record

    # ── Target 2 (FIVE_STAR only) ─────────────────────────────────────────────
    if trade.t1_hit and not trade.t2_hit and setup.trade_type == "FIVE_STAR_SCALABLE":
        t2_hit = (
            (setup.direction == "SHORT" and current_price <= setup.target_2)
            or (setup.direction == "LONG"  and current_price >= setup.target_2)
        )
        if t2_hit:
            client.close_position(ticket, round(trade.lot_size * 0.30, 2), pair)
            trade.on_target_2_hit(current_price, now)
            send_state_change(trade, "PARTIAL_EXIT_2")

    # ── Trailing stop (Tranche C, FIVE_STAR only) ─────────────────────────────
    if trade.t2_hit:
        if trade.is_trail_stop_hit(current_price):
            client.close_position(ticket, round(trade.lot_size * 0.20, 2), pair)
            return _close(trade.on_trail_stop_hit, current_price, "TRAIL_CLOSE")
        if trade.should_advance_trail(current_price):
            trade.advance_trail(current_price)
            client.modify_stop(ticket, trade.trail_stop)

    # ── Target 3 ─────────────────────────────────────────────────────────────
    if getattr(setup, "target_3", None):
        t3_hit = (
            (setup.direction == "SHORT" and current_price <= setup.target_3)
            or (setup.direction == "LONG"  and current_price >= setup.target_3)
        )
        if t3_hit:
            return _close(trade.on_target_3_hit, current_price, "FULL_TARGET_CLOSE")

    return None


# ── MAIN SESSION RUNNER ───────────────────────────────────────────────────────

def run_intraday_session(
    setups: list[Setup],
    session: str,
    feed,
) -> list[TradeRecord]:
    """
    Clock-synchronised intraday execution supervisor.

    Parameters
    ----------
    setups  : Armed Setup objects from the EOD pipeline for this session.
    session : "ASIA" | "LONDON" | "NEW_YORK_FX" | "NEW_YORK_EQ"
    feed    : BrokerFeed instance.

    Returns
    -------
    TradeRecord list for every trade closed during this session.
    """
    if not setups:
        log.info("[session] No setups for %s — standing down", session)
        return []

    sw = SESSION_WINDOWS.get(session)
    if sw is None:
        raise ValueError(f"Unknown session: {session!r}")

    close_h, close_m = sw["close"]
    client = MT5Client()

    monitors = [
        _Monitor(
            setup=s,
            session=session,
            session_close_h=close_h,
            session_close_m=close_m,
        )
        for s in setups
    ]

    completed: list[TradeRecord] = []

    log.info(
        "[session] %s OPEN — supervising %d setup(s): %s",
        session, len(monitors), [m.setup.pair for m in monitors],
    )

    while True:
        active = [m for m in monitors if m.phase not in (Phase.EXPIRED, Phase.TERMINAL)]
        if not active:
            break

        # Drop to 5-min polling the moment any setup is ARMED or FILLED
        needs_5min = any(m.phase in (Phase.ARMED, Phase.FILLED) for m in active)
        interval = 5 if needs_5min else 15
        sleep_secs = _secs_to_next_interval(interval)

        log.debug("[session] Sleeping %.1f s to next %d-min boundary", sleep_secs, interval)
        time.sleep(sleep_secs)

        at_15 = _at_15min_boundary()

        for m in active:
            try:
                if _session_expired(m):
                    log.info(
                        "[session] %s — session window closed in phase %s",
                        m.setup.pair, m.phase.value,
                    )
                    m.phase = Phase.EXPIRED
                    continue

                if m.phase == Phase.WATCHING and at_15:
                    _tick_15min(m, feed)

                elif m.phase == Phase.ARMED:
                    _tick_5min(m, feed, client)

                elif m.phase == Phase.FILLED and at_15:
                    record = _tick_manage(m, feed, client)
                    if record is not None:
                        completed.append(record)

            except Exception:
                log.exception(
                    "[session] %s unhandled error in phase %s",
                    m.setup.pair, m.phase.value,
                )

    log.info("[session] %s closed — %d trade(s) completed", session, len(completed))
    return completed
