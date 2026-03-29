"""
ACB Trader — Post-News Re-Arm Module
Runs intraday (every ~15 min during NY session) on days with paused setups.

Playbook rule: never abort a setup because of news — pause pending orders during
the spike, then re-arm the 5-min 20 EMA coil scanner once volatility settles.
The news creates the trap; the coil is the entry gate after the slippage clears.
"""

from __future__ import annotations
import json
import os
from datetime import datetime, timedelta, date
from typing import Optional

from acb_trader.config import ET
from acb_trader.data.feed import BrokerFeed
from acb_trader.data.levels import (
    snap_to_quarter, snap_stop_beyond, get_pip_size, price_to_pips,
)
from acb_trader.execution.coil import wait_for_ema_coil, check_5min_entry
from acb_trader.execution.orders import OrderManager
from acb_trader.db.models import Setup
from acb_trader.signals.patterns import get_rr_floor

# Settle window: wait this long after the last blocking event before scanning for coil
NEWS_SETTLE_MINUTES = 30

PAUSED_SETUPS_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "paused_setups.json"
)


def _load_paused_setups() -> list[dict]:
    path = os.path.normpath(PAUSED_SETUPS_PATH)
    if not os.path.exists(path):
        return []
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception as e:
        print(f"[news_rearm] Failed to load paused_setups.json: {e}")
        return []


def _save_paused_setups(entries: list[dict]) -> None:
    path = os.path.normpath(PAUSED_SETUPS_PATH)
    with open(path, "w") as fh:
        json.dump(entries, fh, indent=2, default=str)


def _last_event_time(entry: dict) -> Optional[datetime]:
    """Return the latest blocking event timestamp from a paused setup dict."""
    events = entry.get("news_events", [])
    if not events:
        return None
    timestamps = []
    for e in events:
        try:
            ts = datetime.fromisoformat(e["timestamp"]).astimezone(ET)
            timestamps.append(ts)
        except Exception:
            pass
    return max(timestamps) if timestamps else None


def check_paused_setups(feed: BrokerFeed, order_mgr: Optional[OrderManager] = None) -> list[Setup]:
    """
    Called every 15 min during the NY session on news days.
    For each paused setup:
      1. Skip if news has not yet passed + settle window.
      2. Skip if entry_date has expired.
      3. Scan 5-min bars for 20 EMA coil formation.
      4. If coil found and R:R >= 2:1 → update entry/stop and place limit order.
      5. Return list of re-armed setups (for logging/alerting).
    """
    now = datetime.now(ET)
    entries = _load_paused_setups()
    if not entries:
        return []

    re_armed: list[Setup] = []
    remaining: list[dict] = []

    for entry in entries:
        pair = entry.get("pair", "")
        pattern = entry.get("pattern", "")
        direction = entry.get("direction", "")

        # Check expiry
        try:
            entry_date = date.fromisoformat(entry["entry_date"])
        except Exception:
            continue
        if now.date() > entry_date:
            print(f"[news_rearm] {pair} {pattern}: expired — dropping")
            continue

        # Check settle window
        last_event = _last_event_time(entry)
        if last_event is None:
            remaining.append(entry)
            continue
        settle_until = last_event + timedelta(minutes=NEWS_SETTLE_MINUTES)
        if now < settle_until:
            wait_min = int((settle_until - now).total_seconds() / 60)
            print(f"[news_rearm] {pair} {pattern}: settling — {wait_min}m remaining")
            remaining.append(entry)
            continue

        # Fetch 5-min bars and scan for EMA coil
        try:
            bars_5min = feed.get_5min_bars(pair, count=60)
        except Exception as e:
            print(f"[news_rearm] {pair}: feed error — {e}")
            remaining.append(entry)
            continue

        prior_entry = float(entry.get("entry_price", 0.0))
        coil = wait_for_ema_coil(pair, prior_entry, direction, bars_5min)

        if not coil.triggered:
            print(f"[news_rearm] {pair} {pattern}: no coil yet — watching")
            remaining.append(entry)
            continue

        # Coil formed — compute new entry, stop, and validate R:R
        pip = get_pip_size(pair)
        coil_mid = (coil.coil_high + coil.coil_low) / 2

        if direction == "SHORT":
            new_entry = snap_to_quarter(coil_mid, pair)
            new_stop  = snap_stop_beyond(coil.coil_high + 2 * pip, "SHORT", pair)
            t1_raw    = float(entry.get("target_1", new_entry))
        else:
            new_entry = snap_to_quarter(coil_mid, pair)
            new_stop  = snap_stop_beyond(coil.coil_low - 2 * pip, "LONG", pair)
            t1_raw    = float(entry.get("target_1", new_entry))

        risk_pips = price_to_pips(abs(new_entry - new_stop), pair)
        t1_pips   = price_to_pips(abs(t1_raw - new_entry), pair)
        rr        = t1_pips / risk_pips if risk_pips > 0 else 0.0
        rr_floor  = get_rr_floor(pattern)

        if rr < rr_floor:
            print(f"[news_rearm] {pair} {pattern}: post-news R:R {rr:.2f} < {rr_floor} floor — skip")
            remaining.append(entry)
            continue

        # Confirm 5-min entry trigger (close through 20 EMA)
        triggered_5m, ema20 = check_5min_entry(bars_5min, direction)
        if not triggered_5m:
            print(f"[news_rearm] {pair} {pattern}: coil found, awaiting 5m EMA break (ema20={ema20:.5f})")
            remaining.append(entry)
            continue

        # Build re-armed Setup and place order
        setup = Setup(
            pair=pair,
            pattern=pattern,
            direction=direction,
            entry_price=new_entry,
            stop_price=new_stop,
            target_1=snap_to_quarter(t1_raw, pair),
            target_2=snap_to_quarter(float(entry.get("target_2", new_entry)), pair),
            target_3=None,
            risk_pips=risk_pips,
            score=int(entry.get("score", 0)),
            trade_type=entry.get("trade_type", "SESSION_TRADE"),
            signal_date=date.fromisoformat(entry["signal_date"]),
            entry_date=entry_date,
            ema_coil_confirmed=True,
            expires=entry_date,
            notes=f"{pattern} re-armed post-news | 5m coil stop | R:R={rr:.2f}",
        )

        if order_mgr is not None:
            lot_size = float(entry.get("lot_size", 0.01))
            result = order_mgr.place_limit_order(setup, lot_size)
            print(f"[news_rearm] {pair} {pattern}: ORDER PLACED ticket={result.ticket} "
                  f"entry={new_entry} stop={new_stop} R:R={rr:.2f}")
        else:
            print(f"[news_rearm] {pair} {pattern}: RE-ARMED (dry-run) "
                  f"entry={new_entry} stop={new_stop} R:R={rr:.2f}")

        re_armed.append(setup)

    _save_paused_setups(remaining)
    return re_armed
