"""
ACB Trader — Order Placement (MT5 abstraction)
All order ops go through this module. Never call MT5 directly from other modules.
"""

from __future__ import annotations
from datetime import datetime
from acb_trader.config import ET
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
        """Place a limit entry order at setup.entry_price."""
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
