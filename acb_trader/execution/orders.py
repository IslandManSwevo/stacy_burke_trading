"""
ACB Trader — Order Placement (MT5 abstraction)
All order ops go through this module. Never call MT5 directly from other modules.
"""

from __future__ import annotations
from datetime import datetime
from acb_trader.config import ET
from acb_trader.data.calendar import is_in_news_settle_window
from acb_trader.db.models import Setup

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False


class OrderResult:
    def __init__(self, success: bool, ticket: int = 0, message: str = ""):
        self.success = success
        self.ticket  = ticket
        self.message = message


class MT5Client:

    def place_limit_order(
        self,
        setup: Setup,
        lot_size: float,
    ) -> OrderResult:
        """Place a limit entry order at setup.entry_price.

        Hardcoded guardrail: REFUSES to place any order while the pair
        is inside the 30-minute post-MRN settle window.  Entering during
        the news spike is Garbage Trading — 50-100 pips of slippage will
        bypass any protective stop.  The algorithm waits for the EMAs to
        re-coil, then news_rearm.py places the order after the settle.
        """
        now = datetime.now(ET)
        if is_in_news_settle_window(setup.pair, now):
            msg = (f"BLOCKED: {setup.pair} is inside the 30-min post-MRN "
                   f"settle window — order rejected to protect capital")
            print(f"[orders] {msg}")
            return OrderResult(False, message=msg)

        if not MT5_AVAILABLE:
            print(f"[orders] PAPER: LIMIT {setup.direction} {setup.pair} "
                  f"@ {setup.entry_price:.5f} SL={setup.stop_price:.5f} "
                  f"TP={setup.target_1:.5f} lots={lot_size}")
            return OrderResult(True, ticket=99999, message="paper")

        order_type = mt5.ORDER_TYPE_BUY_LIMIT if setup.direction == "LONG" \
                     else mt5.ORDER_TYPE_SELL_LIMIT

        req = {
            "action":    mt5.TRADE_ACTION_PENDING,
            "symbol":    setup.pair,
            "volume":    lot_size,
            "type":      order_type,
            "price":     setup.entry_price,
            "sl":        setup.stop_price,
            "tp":        setup.target_1,
            "deviation": 5,
            "magic":     20250327,
            "comment":   f"ACB {setup.pattern[:8]}",
            "type_time": mt5.ORDER_TIME_DAY,
        }
        result = mt5.order_send(req)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            return OrderResult(True, ticket=result.order, message="ok")
        err = result.comment if result else mt5.last_error()
        return OrderResult(False, message=str(err))

    def place_market_order(
        self,
        setup: Setup,
        lot_size: float,
    ) -> OrderResult:
        """
        Fire an immediate MARKET order at the current ask/bid.
        Used exclusively by the 5-min drop-down trigger — never for EOD pending entries.
        News settle window check is enforced identically to place_limit_order.
        """
        now = datetime.now(ET)
        if is_in_news_settle_window(setup.pair, now):
            msg = (f"BLOCKED: {setup.pair} inside 30-min MRN settle window "
                   f"— market order rejected")
            print(f"[orders] {msg}")
            return OrderResult(False, message=msg)

        if not MT5_AVAILABLE:
            print(f"[orders] PAPER: MARKET {setup.direction} {setup.pair} "
                  f"SL={setup.stop_price:.5f} TP={setup.target_1:.5f} lots={lot_size}")
            return OrderResult(True, ticket=99998, message="paper")

        tick = mt5.symbol_info_tick(setup.pair)
        if tick is None:
            return OrderResult(False, message=f"No tick data for {setup.pair}")

        if setup.direction == "LONG":
            order_type = mt5.ORDER_TYPE_BUY
            price = tick.ask
        else:
            order_type = mt5.ORDER_TYPE_SELL
            price = tick.bid

        req = {
            "action":    mt5.TRADE_ACTION_DEAL,
            "symbol":    setup.pair,
            "volume":    lot_size,
            "type":      order_type,
            "price":     price,
            "sl":        setup.stop_price,
            "tp":        setup.target_1,
            "deviation": 10,
            "magic":     20250327,
            "comment":   f"ACB 5m {setup.pattern[:6]}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(req)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            return OrderResult(True, ticket=result.order, message="ok")
        err = result.comment if result else mt5.last_error()
        return OrderResult(False, message=str(err))

    def cancel_pending(self, ticket: int) -> bool:
        if not MT5_AVAILABLE:
            print(f"[orders] PAPER: cancel ticket {ticket}")
            return True
        req = {
            "action": mt5.TRADE_ACTION_REMOVE,
            "order":  ticket,
        }
        result = mt5.order_send(req)
        return result and result.retcode == mt5.TRADE_RETCODE_DONE

    def close_position(self, ticket: int, lot_size: float, pair: str) -> bool:
        if not MT5_AVAILABLE:
            print(f"[orders] PAPER: close {ticket} {lot_size} lots")
            return True
        pos = mt5.positions_get(ticket=ticket)
        if not pos:
            return False
        p = pos[0]
        close_type = mt5.ORDER_TYPE_SELL if p.type == 0 else mt5.ORDER_TYPE_BUY
        req = {
            "action":    mt5.TRADE_ACTION_DEAL,
            "position":  ticket,
            "symbol":    pair,
            "volume":    lot_size,
            "type":      close_type,
            "price":     mt5.symbol_info_tick(pair).bid,
            "deviation": 10,
            "magic":     20250327,
            "comment":   "ACB close",
        }
        result = mt5.order_send(req)
        return result and result.retcode == mt5.TRADE_RETCODE_DONE

    def modify_stop(self, ticket: int, new_sl: float) -> bool:
        if not MT5_AVAILABLE:
            print(f"[orders] PAPER: move SL ticket {ticket} → {new_sl:.5f}")
            return True
        req = {
            "action":   mt5.TRADE_ACTION_SLTP,
            "position": ticket,
            "sl":       new_sl,
        }
        result = mt5.order_send(req)
        return result and result.retcode == mt5.TRADE_RETCODE_DONE

    def get_pending_orders(self) -> list:
        if not MT5_AVAILABLE:
            return []
        orders = mt5.orders_get()
        return list(orders) if orders else []

    def get_open_positions(self) -> list:
        if not MT5_AVAILABLE:
            return []
        pos = mt5.positions_get()
        return list(pos) if pos else []
