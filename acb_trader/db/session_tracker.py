"""
ACB Trader — Session Tracker
Persists the data circuit breakers need across EOD runs.

Stores a lightweight JSON file next to this module:
    acb_trader/db/session_state.json

Fields:
  daily_open_date     — ISO date string for today
  daily_open_balance  — account balance at the start of today's session
  weekly_open_date    — ISO date string for this Monday
  weekly_open_balance — account balance at Monday's session open
  consecutive_losses  — count of consecutive losing trades (reset on any win)

Usage in main.py:
    from acb_trader.db.session_tracker import (
        get_or_set_daily_open, get_or_set_weekly_open,
        get_consecutive_losses, record_trade_result,
    )
"""

from __future__ import annotations
import json
import os
from datetime import datetime, timedelta
from acb_trader.config import ET

_SESSION_FILE = os.path.join(os.path.dirname(__file__), "session_state.json")


# ── LOW-LEVEL I/O ─────────────────────────────────────────────────────────────

def _load() -> dict:
    if os.path.exists(_SESSION_FILE):
        try:
            with open(_SESSION_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save(data: dict):
    with open(_SESSION_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# ── DATE HELPERS ──────────────────────────────────────────────────────────────

def _today_str() -> str:
    return datetime.now(ET).date().isoformat()


def _monday_str() -> str:
    d = datetime.now(ET).date()
    return (d - timedelta(days=d.weekday())).isoformat()


# ── PUBLIC API ────────────────────────────────────────────────────────────────

def get_or_set_daily_open(balance: float) -> float:
    """
    Return today's session-open balance.
    If no record exists for today, seed it with the current balance and save.
    """
    data  = _load()
    today = _today_str()
    if data.get("daily_open_date") != today:
        data["daily_open_date"]    = today
        data["daily_open_balance"] = balance
        _save(data)
    return float(data.get("daily_open_balance", balance))


def get_or_set_weekly_open(balance: float) -> float:
    """
    Return this week's Monday-open balance.
    If no record exists for this Monday, seed it and save.
    """
    data   = _load()
    monday = _monday_str()
    if data.get("weekly_open_date") != monday:
        data["weekly_open_date"]    = monday
        data["weekly_open_balance"] = balance
        _save(data)
    return float(data.get("weekly_open_balance", balance))


def get_consecutive_losses() -> int:
    """Return the current consecutive-loss count (0 if no file yet)."""
    return int(_load().get("consecutive_losses", 0))


def record_trade_result(profit_pips: float):
    """
    Call after each trade closes.
    profit_pips > 0 → win (resets counter).
    profit_pips < 0 → loss (increments counter).
    profit_pips == 0 → breakeven (treated as win — streak not extended).
    """
    data = _load()
    current = int(data.get("consecutive_losses", 0))
    data["consecutive_losses"] = 0 if profit_pips >= 0 else current + 1
    _save(data)


def compute_account_metrics(balance: float) -> tuple[float, float, int]:
    """
    Convenience function used by main.py.
    Returns (daily_pnl_pct, weekly_drawdown_pct, consecutive_losses).

    Sign convention matches AccountState fields and checklist thresholds:
      daily_pnl_pct      — negative when today is a losing session
      weekly_drawdown_pct — negative when balance is below Monday open
    Circuit breakers fire at:
      daily_pnl_pct      <= -DAILY_LOSS_HALT_PCT  (e.g. -0.02)
      weekly_drawdown_pct <= -WEEKLY_DD_HALT_PCT   (e.g. -0.05)
    """
    daily_open  = get_or_set_daily_open(balance)
    weekly_open = get_or_set_weekly_open(balance)

    daily_pnl    = (balance - daily_open)  / daily_open  if daily_open  > 0 else 0.0
    weekly_dd    = (balance - weekly_open) / weekly_open if weekly_open > 0 else 0.0
    consec_loss  = get_consecutive_losses()

    return daily_pnl, weekly_dd, consec_loss
