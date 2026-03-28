"""
ACB Trader — Backtest Data Loader
Loads historical OHLCV from:
  1. CSV files exported from MT5 History Center
  2. Direct MT5 historical download (if MT5 running)
  3. Dukascopy free historical data (fallback)

MT5 History Center export format:
  File → Save As → CSV
  Columns: <DATE>,<TIME>,<OPEN>,<HIGH>,<LOW>,<CLOSE>,<TICKVOL>,<VOL>,<SPREAD>
"""

from __future__ import annotations
import os
import pandas as pd
from pathlib import Path
from datetime import datetime, date
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


# ── CSV LOADER (MT5 History Center export) ────────────────────────────────────

def load_mt5_csv(filepath: str, pair: str) -> pd.DataFrame:
    """
    Load a CSV exported from MT5 History Center.
    Handles both comma and tab-delimited formats, with or without angle-bracket headers.

    Usage:
        df = load_mt5_csv("data/EURUSD_D1.csv", "EURUSD")
    """
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"CSV not found: {filepath}")

    # Detect delimiter
    with open(filepath) as f:
        sample = f.readline()
    delimiter = "\t" if "\t" in sample else ","

    df = pd.read_csv(filepath, delimiter=delimiter, header=0)

    # Normalise column names — MT5 uses <DATE>, <TIME>, <OPEN> etc.
    df.columns = [c.strip().strip("<>").lower() for c in df.columns]

    # Rename to standard names
    rename = {
        "date": "date", "time": "time",
        "open": "open", "high": "high", "low": "low", "close": "close",
        "tickvol": "volume", "vol": "volume", "tick volume": "volume",
        "volume": "volume",
    }
    df.rename(columns={k: v for k, v in rename.items() if k in df.columns}, inplace=True)

    # Build datetime index - Keep in broker time to preserve calendar dates for D1 bars
    if "time" in df.columns:
        dt_series = pd.to_datetime(df["date"].astype(str) + " " + df["time"].astype(str))
    else:
        dt_series = pd.to_datetime(df["date"].astype(str))

    # We do not convert to ET for backtesting daily bars because MT5 D1 bars 
    # are aligned to broker midnight. Converting them to ET shifts them to the previous day.
    df["datetime"] = dt_series
    df["date"] = df["datetime"]
    df["pair"] = pair

    required = ["date", "open", "high", "low", "close"]
    for col in required:
        if col not in df.columns:
            raise ValueError(f"Missing column '{col}' in {filepath}. Columns found: {list(df.columns)}")

    if "volume" not in df.columns:
        df["volume"] = 0

    df = df[["date", "open", "high", "low", "close", "volume", "pair"]].copy()
    df = df.sort_values("date").reset_index(drop=True)

    # Cast price columns to float
    for col in ["open", "high", "low", "close"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df.dropna(subset=["open", "high", "low", "close"], inplace=True)
    print(f"[loader] {pair}: {len(df)} bars loaded from {filepath}")
    return df


# ── MT5 DIRECT HISTORICAL DOWNLOAD ───────────────────────────────────────────

def download_mt5_history(
    pair: str,
    timeframe: str = "D1",
    count: int = 500,
    output_dir: str = "backtest_data",
) -> pd.DataFrame:
    """
    Download historical bars directly from running MT5 terminal and save to CSV.
    Requires MetaTrader5 package + MT5 terminal running and logged in.
    """
    try:
        import MetaTrader5 as mt5
    except ImportError:
        raise ImportError("MetaTrader5 not installed. Run: pip install MetaTrader5")

    tf_map = {
        "M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15, "H1": mt5.TIMEFRAME_H1,
        "H4": mt5.TIMEFRAME_H4, "D1": mt5.TIMEFRAME_D1,
    }
    if timeframe not in tf_map:
        raise ValueError(f"Unknown timeframe: {timeframe}. Use: {list(tf_map.keys())}")

    if not mt5.initialize():
        raise RuntimeError(f"MT5 init failed: {mt5.last_error()}")

    bars = mt5.copy_rates_from_pos(pair, tf_map[timeframe], 0, count)
    mt5.shutdown()

    if bars is None or len(bars) == 0:
        raise ValueError(f"No data for {pair} {timeframe}")

    df = pd.DataFrame(bars)
    # MT5 daily bars are midnight broker time; we parse directly as local time to preserve the calendar date
    df["date"] = pd.to_datetime(df["time"], unit="s")
    df["pair"] = pair
    df.rename(columns={"tick_volume": "volume"}, inplace=True)
    df = df[["date", "open", "high", "low", "close", "volume", "pair"]]

    os.makedirs(output_dir, exist_ok=True)
    out_path = f"{output_dir}/{pair}_{timeframe}.csv"
    df.to_csv(out_path, index=False)
    print(f"[loader] {pair} {timeframe}: {len(df)} bars saved to {out_path}")
    return df


# ── MULTI-PAIR LOADER ─────────────────────────────────────────────────────────

def load_basket_csvs(
    data_dir: str,
    pairs: list[str],
    timeframe: str = "D1",
) -> dict[str, pd.DataFrame]:
    """
    Load all pairs from a directory of MT5 CSV exports.
    Expects files named: EURUSD_D1.csv, GBPUSD_D1.csv etc.

    Returns dict of pair → DataFrame.
    """
    result = {}
    for pair in pairs:
        filename = f"{pair}_{timeframe}.csv"
        filepath = os.path.join(data_dir, filename)
        if not os.path.exists(filepath):
            print(f"[loader] WARNING: {filepath} not found — skipping {pair}")
            continue
        try:
            result[pair] = load_mt5_csv(filepath, pair)
        except Exception as e:
            print(f"[loader] ERROR loading {pair}: {e}")
    return result


# ── DATE FILTERING ────────────────────────────────────────────────────────────

def filter_date_range(
    df: pd.DataFrame,
    start: str | None = None,
    end: str | None = None,
) -> pd.DataFrame:
    """
    Filter DataFrame to a date range.
    start/end: "YYYY-MM-DD" strings or None for open-ended.
    """
    if start:
        df = df[df["date"] >= pd.Timestamp(start)]
    if end:
        df = df[df["date"] <= pd.Timestamp(end)]
    return df.reset_index(drop=True)


# ── PEPPERSTONE-SPECIFIC HELPERS ──────────────────────────────────────────────

# Pepperstone symbol names may differ — map to our internal names
PEPPERSTONE_SYMBOL_MAP = {
    "EURUSD": "EURUSD", "GBPUSD": "GBPUSD", "USDJPY": "USDJPY",
    "USDCHF": "USDCHF", "USDCAD": "USDCAD", "AUDUSD": "AUDUSD",
    "NZDUSD": "NZDUSD", "GBPJPY": "GBPJPY", "EURJPY": "EURJPY",
    "AUDJPY": "AUDJPY", "CADJPY": "CADJPY", "XAUUSD": "XAUUSD",
    # Indices — verify in your MT5 Market Watch
    "SP500":  "US500",   # Pepperstone uses US500
    "NAS100": "USTEC",   # Pepperstone uses USTEC
    "DJ30":   "US30",    # Pepperstone uses US30
    "USOIL":  "XTIUSD",  # Pepperstone uses XTIUSD
}


def pepperstone_symbol(internal_name: str) -> str:
    """Convert our internal pair name to Pepperstone's symbol name."""
    return PEPPERSTONE_SYMBOL_MAP.get(internal_name, internal_name)
