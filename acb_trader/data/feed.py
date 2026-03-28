"""
ACB Trader — Data Feed Abstraction
Supports MT5 (via MetaTrader5 pip package) and a CSV fallback for backtesting.
Install: pip install MetaTrader5 pandas
"""

from __future__ import annotations
import pandas as pd
from datetime import datetime, timedelta
from acb_trader.config import ET

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False
    print("[feed] MetaTrader5 not installed — running in CSV/backtest mode")


# ── TIMEFRAME CONSTANTS ───────────────────────────────────────────────────────
TF_M1  = "M1"
TF_M5  = "M5"
TF_M15 = "M15"
TF_H1  = "H1"
TF_H4  = "H4"
TF_D1  = "D1"

_MT5_TF = {
    TF_M1:  mt5.TIMEFRAME_M1  if MT5_AVAILABLE else 1,
    TF_M5:  mt5.TIMEFRAME_M5  if MT5_AVAILABLE else 5,
    TF_M15: mt5.TIMEFRAME_M15 if MT5_AVAILABLE else 15,
    TF_H1:  mt5.TIMEFRAME_H1  if MT5_AVAILABLE else 60,
    TF_H4:  mt5.TIMEFRAME_H4  if MT5_AVAILABLE else 240,
    TF_D1:  mt5.TIMEFRAME_D1  if MT5_AVAILABLE else 1440,
}


class BrokerFeed:
    """Abstract broker interface — swap MT5 for cTrader by subclassing."""

    def __init__(self, login: int = 0, password: str = "", server: str = ""):
        self._login = login
        self._password = password
        self._server = server
        self._connected = False

    def connect(self) -> bool:
        if not MT5_AVAILABLE:
            self._connected = True   # backtest mode
            return True
        if not mt5.initialize(login=self._login, password=self._password, server=self._server):
            print(f"[feed] MT5 init failed: {mt5.last_error()}")
            return False
        self._connected = True
        return True

    def disconnect(self):
        if MT5_AVAILABLE and self._connected:
            mt5.shutdown()
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def get_ohlcv(
        self,
        pair: str,
        timeframe: str,
        count: int = 100,
    ) -> pd.DataFrame:
        """Fetch OHLCV bars. Returns DataFrame with columns: date,open,high,low,close,volume."""
        if not MT5_AVAILABLE:
            raise RuntimeError("MT5 not available — provide CSV data for backtesting")

        bars = mt5.copy_rates_from_pos(pair, _MT5_TF[timeframe], 0, count)
        if bars is None or len(bars) == 0:
            raise ValueError(f"[feed] No data for {pair} {timeframe}: {mt5.last_error()}")

        df = pd.DataFrame(bars)
        df["date"] = pd.to_datetime(df["time"], unit="s", utc=True).dt.tz_convert(ET)
        df = df.rename(columns={"tick_volume": "volume"})
        return df[["date", "open", "high", "low", "close", "volume"]].copy()

    def get_daily_ohlcv(self, pair: str, count: int = 60) -> pd.DataFrame:
        return self.get_ohlcv(pair, TF_D1, count)

    def get_1min_today(self, pair: str) -> pd.DataFrame:
        """All 1-min bars since midnight ET today."""
        df = self.get_ohlcv(pair, TF_M1, count=1440)
        today = datetime.now(ET).date()
        return df[df["date"].dt.date == today].copy()

    def get_session_bars(self, pair: str, session_open: datetime) -> pd.DataFrame:
        """1-min bars from session open to now."""
        df = self.get_ohlcv(pair, TF_M1, count=360)
        return df[df["date"] >= session_open].copy()

    def get_5min_bars(self, pair: str, count: int = 60) -> pd.DataFrame:
        return self.get_ohlcv(pair, TF_M5, count)

    def get_15min_bars(self, pair: str, count: int = 48) -> pd.DataFrame:
        return self.get_ohlcv(pair, TF_M15, count)

    def get_spread(self, pair: str) -> float:
        """Current spread in pips."""
        if not MT5_AVAILABLE:
            return 1.0
        info = mt5.symbol_info(pair)
        if info is None:
            return 0.0
        return float(info.spread) * info.point / 0.0001  # normalise to pips

    def get_account(self) -> dict:
        """Return account balance, equity."""
        if not MT5_AVAILABLE:
            return {"balance": 10000.0, "equity": 10000.0}
        acc = mt5.account_info()
        if acc is None:
            return {"balance": 0.0, "equity": 0.0}
        return {"balance": acc.balance, "equity": acc.equity}

    def last_update_age_minutes(self) -> int:
        """Minutes since last tick on EURUSD — staleness check."""
        if not MT5_AVAILABLE:
            return 0
        tick = mt5.symbol_info_tick("EURUSD")
        if tick is None:
            return 999
        last = datetime.fromtimestamp(tick.time, tz=ET)
        return int((datetime.now(ET) - last).total_seconds() / 60)
