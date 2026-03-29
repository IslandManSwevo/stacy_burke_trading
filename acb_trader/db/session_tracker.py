"""
ACB Trader — Session Tracker
Persists the data circuit breakers need across EOD runs, plus the persistent
trade log used by the weekly review automation.

Stores lightweight files next to this module:
    acb_trader/db/session_state.json   — circuit-breaker state
    acb_trader/db/trade_log.jsonl      — one TradeRecord per line (live trades)
    acb_trader/db/discard_log.jsonl    — one DiscardedSetup per line

Fields in session_state.json:
  daily_open_date     — ISO date string for today
  daily_open_balance  — account balance at the start of today's session
  weekly_open_date    — ISO date string for this Monday
  weekly_open_balance — account balance at Monday's session open
  consecutive_losses  — count of consecutive losing trades (reset on any win)

Usage in main.py:
    from acb_trader.db.session_tracker import (
        get_or_set_daily_open, get_or_set_weekly_open,
        get_consecutive_losses, record_trade_result,
        log_trade, log_discard, get_week_trades, get_week_discards,
    )
"""

from __future__ import annotations
import json
import os
import re
import sys
import logging
from datetime import datetime, date, timedelta
from typing import TYPE_CHECKING
from acb_trader.config import ET

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from acb_trader.models import TradeRecord, DiscardedSetup

_SESSION_FILE  = os.path.join(os.path.dirname(__file__), "session_state.json")
_TRADE_LOG     = os.path.join(os.path.dirname(__file__), "trade_log.jsonl")
_DISCARD_LOG   = os.path.join(os.path.dirname(__file__), "discard_log.jsonl")


def _lock_file(f, exclusive: bool = True) -> None:
    """Acquire a cross-platform file lock."""
    try:
        if sys.platform == "win32":
            import msvcrt
            pos = f.tell()
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
            f.seek(pos)
        else:
            import fcntl
            flags = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
            fcntl.flock(f.fileno(), flags)
    except Exception as e:
        logger.warning(f"File lock warn: {e}")

def _unlock_file(f) -> None:
    """Release a cross-platform file lock."""
    try:
        if sys.platform == "win32":
            import msvcrt
            pos = f.tell()
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            f.seek(pos)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass


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


# ── TRADE LOG (weekly review persistence) ────────────────────────────────────

def _dt_to_str(dt) -> str:
    """Serialize datetime to ISO string without microseconds."""
    if dt is None:
        return ""
    if isinstance(dt, datetime):
        return dt.strftime("%Y-%m-%dT%H:%M:%S")
    if isinstance(dt, date):
        return dt.isoformat()
    return str(dt)


def _parse_dt(s: str) -> datetime:
    """Parse ISO datetime string robustly (strips tz offset before parsing)."""
    if not s:
        return datetime.min
    # Strip timezone offset (+HH:MM, -HH:MM or Z) so strptime works on Python < 3.11
    clean = re.sub(r'([+-]\d{2}:\d{2}|Z)$', '', s).strip()
    try:
        return datetime.strptime(clean, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        try:
            return datetime.strptime(clean, "%Y-%m-%d")
        except ValueError:
            return datetime.min


def _parse_date(s: str) -> date:
    if not s:
        return date.min
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return date.min


def log_trade(record: "TradeRecord") -> None:
    """
    Append a completed TradeRecord to the persistent trade log (trade_log.jsonl).
    Called from main.py after every live trade close.
    """
    row = {
        "trade_id":      record.trade_id,
        "pair":          record.pair,
        "pattern":       record.pattern,
        "direction":     record.direction,
        "trade_type":    record.trade_type,
        "score":         record.score,
        "session":       record.session,
        "entry_price":   record.entry_price,
        "entry_time":    _dt_to_str(record.entry_time),
        "stop_price":    record.stop_price,
        "lot_size":      record.lot_size,
        "target_1":      record.target_1,
        "target_2":      record.target_2,
        "target_3":      record.target_3,
        "exit_price":    record.exit_price,
        "exit_time":     _dt_to_str(record.exit_time),
        "terminal_state":record.terminal_state,
        "pips":          record.pips,
        "r_multiple":    record.r_multiple,
        "notes":         record.notes,
    }
    try:
        with open(_TRADE_LOG, "a", encoding="utf-8") as f:
            _lock_file(f, exclusive=True)
            try:
                f.write(json.dumps(row) + "\n")
            finally:
                _unlock_file(f)
    except Exception as e:
        logger.error(f"Failed to log trade {record.trade_id} to {_TRADE_LOG}: {e}", exc_info=True)


def log_discard(discard: "DiscardedSetup") -> None:
    """
    Append a DiscardedSetup to the persistent discard log (discard_log.jsonl).
    Called from main.py alongside detect_setups() discards each evening.
    """
    row = {
        "pair":              discard.pair,
        "pattern":           discard.pattern,
        "direction":         discard.direction,
        "score":             discard.score,
        "reason":            discard.reason,
        "discarded_at":      _dt_to_str(discard.discarded_at),
        "would_have_hit_t1": discard.would_have_hit_t1,
        "entry_price":       discard.entry_price,
        "stop_price":        discard.stop_price,
        "target_1":          discard.target_1,
    }
    try:
        with open(_DISCARD_LOG, "a", encoding="utf-8") as f:
            _lock_file(f, exclusive=True)
            try:
                f.write(json.dumps(row) + "\n")
            finally:
                _unlock_file(f)
    except Exception as e:
        logger.error(f"Failed to log discard for {discard.pair} to {_DISCARD_LOG}: {e}", exc_info=True)


def get_week_trades(monday: date) -> list:
    """
    Return a list of TradeRecord-like dicts for the Mon–Fri window
    starting at *monday*.  Kept as dicts to avoid a heavy import chain;
    build_weekly_review() reads the fields it needs directly.
    """
    friday = monday + timedelta(days=4)
    results = []
    if not os.path.exists(_TRADE_LOG):
        return results
    with open(_TRADE_LOG, encoding="utf-8") as f:
        _lock_file(f, exclusive=False)
        try:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    entry_date = _parse_dt(d.get("entry_time", "")).date()
                    if monday <= entry_date <= friday:
                        results.append(d)
                except Exception as e:
                    logger.warning(f"Error parsing trade log line: {e} | Line: {line}")
                    continue
        finally:
            _unlock_file(f)
    return results


def get_week_discards(monday: date) -> list:
    """
    Return a list of DiscardedSetup-like dicts for the Mon–Fri window
    starting at *monday*.
    """
    friday = monday + timedelta(days=4)
    results = []
    if not os.path.exists(_DISCARD_LOG):
        return results
    with open(_DISCARD_LOG, encoding="utf-8") as f:
        _lock_file(f, exclusive=False)
        try:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    disc_date = _parse_dt(d.get("discarded_at", "")).date()
                    if monday <= disc_date <= friday:
                        results.append(d)
                except Exception as e:
                    logger.warning(f"Error parsing discard log line: {e} | Line: {line}")
                    continue
        finally:
            _unlock_file(f)
    return results
