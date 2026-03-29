"""
ACB Trader — Run Backtest
Ready-to-run script. Edit the config section at the top, then run:

    python -m acb_trader.backtest.run_backtest

Step 1: Export historical data from MT5
  - Open MT5 → Tools → History Center
  - Select each pair (e.g. EURUSD) + timeframe D1
  - Right-click → Export Bars → save to backtest_data/ folder
  - Filename format: EURUSD_D1.csv, GBPUSD_D1.csv etc.

Step 2: Or download automatically (MT5 must be running):
  - Set DOWNLOAD_FROM_MT5 = True below
  - Script will pull history directly and save CSVs

Step 3: Run backtest
  - python -m acb_trader.backtest.run_backtest
"""

import os
import sys
from pathlib import Path

# ── CONFIG ────────────────────────────────────────────────────────────────────

DATA_DIR           = "backtest_data"       # Folder containing CSVs
START_DATE         = "2023-01-01"          # Backtest start
END_DATE           = "2024-12-31"          # Backtest end (None = up to latest)
STARTING_BALANCE   = 10_000.0             # USD
VERBOSE            = True                  # Print every signal and trade
EXPORT_CSV         = True                  # Save trades to backtest_results.csv
DOWNLOAD_FROM_MT5  = True                  # Set True to pull direct from MT5

# Which pairs to backtest — full basket coverage.
# CSV files must exist in backtest_data/ as {PAIR}_D1.csv
# Note: index/oil symbols in CSVs use the internal names (SP500, USOIL etc.)
# not the MT5 broker names — the SYMBOL_MAP in config.py handles the translation
# at download time.
PAIRS = [
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF",
    "AUDUSD", "NZDUSD", "USDCAD", "XAUUSD", "USOIL", "SP500",
]

# ── DOWNLOAD FROM MT5 (optional) ─────────────────────────────────────────────

if DOWNLOAD_FROM_MT5:
    from acb_trader.backtest.data_loader import download_mt5_history, pepperstone_symbol
    os.makedirs(DATA_DIR, exist_ok=True)
    print("Downloading historical data from MT5...")
    for pair in PAIRS:
        mt5_symbol = pepperstone_symbol(pair)
        try:
            download_mt5_history(
                pair=mt5_symbol,
                timeframe="D1",
                count=1000,
                output_dir=DATA_DIR,
            )
            # Rename to our internal name if different
            if mt5_symbol != pair:
                src = Path(DATA_DIR) / f"{mt5_symbol}_D1.csv"
                dst = Path(DATA_DIR) / f"{pair}_D1.csv"
                if src.exists():
                    src.rename(dst)
        except Exception as e:
            print(f"  Failed {pair}: {e}")

# ── RUN BACKTEST ──────────────────────────────────────────────────────────────

from acb_trader.backtest.engine import BacktestEngine

engine = BacktestEngine(
    data_dir=DATA_DIR,
    start=START_DATE,
    end=END_DATE,
    starting_balance=STARTING_BALANCE,
    pairs=PAIRS,
    verbose=VERBOSE,
)

print(f"Loading data from {DATA_DIR}...")
engine.load_data()

print("Running backtest...")
results = engine.run()

engine.print_report(results)

if EXPORT_CSV:
    df = engine.to_csv(results, "backtest_results.csv")
    print(f"\nTrade log saved: backtest_results.csv ({len(df)} trades)")

    # Save discard log for diagnosing why setups are filtered
    import pandas as _pd
    discard_df = _pd.DataFrame(getattr(engine, "_all_discarded", []))
    if not discard_df.empty:
        top = discard_df.groupby(["pattern", "reason"]).size().reset_index(name="count")
        top = top.sort_values("count", ascending=False)
        top.to_csv("backtest_discards_summary.csv", index=False)
        print(f"\nDiscard summary saved: backtest_discards_summary.csv")
        print(top.to_string(index=False))

    # ── DISCARD ANALYSIS: would_have_hit_t1 ───────────────────────────────────
    # For every BELOW_MIN_SCORE discard, simulate whether T1 or stop would have
    # been hit within 3 bars. Tells us objectively if our score floor is too tight.
    # Verdict key:
    #   FILTER_TOO_TIGHT  → T1 hit rate ≥ 50% (we're blocking profitable setups)
    #   FILTER_MARGINAL   → T1 hit rate 35–49% (borderline — watch but don't change yet)
    #   FILTER_WORKING    → T1 hit rate < 35% (filter is correct, keep it)
    engine.discard_analysis_csv(lookahead_bars=3, filepath="backtest_discards_would_have_hit.csv")

# ── EQUITY CURVE PLOT (optional — requires matplotlib) ────────────────────────

try:
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 8), gridspec_kw={"height_ratios": [3, 1]})
    fig.suptitle("ACB Trader Backtest — Equity Curve", fontsize=14, fontweight="bold")

    # Equity curve
    ax1.plot(results.equity_curve, color="#00F2FE", linewidth=1.5)
    ax1.axhline(STARTING_BALANCE, color="gray", linestyle="--", alpha=0.5, label="Starting balance")
    ax1.fill_between(range(len(results.equity_curve)), STARTING_BALANCE,
                     results.equity_curve,
                     where=[e >= STARTING_BALANCE for e in results.equity_curve],
                     alpha=0.2, color="#00F2FE")
    ax1.fill_between(range(len(results.equity_curve)), STARTING_BALANCE,
                     results.equity_curve,
                     where=[e < STARTING_BALANCE for e in results.equity_curve],
                     alpha=0.2, color="red")
    ax1.set_ylabel("Account Balance ($)")
    ax1.legend()
    ax1.grid(True, alpha=0.3)

    # R-multiple distribution
    r_multiples = [t.r_multiple for t in results.trades if t.terminal_state not in ("PENDING", "EXPIRED")]
    colors = ["#00F2FE" if r > 0 else "#FF4444" for r in r_multiples]
    ax2.bar(range(len(r_multiples)), r_multiples, color=colors, alpha=0.8, width=0.8)
    ax2.axhline(0, color="white", linewidth=0.5)
    ax2.set_ylabel("R-Multiple")
    ax2.set_xlabel("Trade #")
    ax2.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig("backtest_equity_curve.png", dpi=150, bbox_inches="tight",
                facecolor="#0B132B")
    print("Equity curve saved: backtest_equity_curve.png")
    plt.show()

except ImportError:
    print("\n(Install matplotlib for equity curve: pip install matplotlib)")
except Exception as e:
    print(f"\n(Chart error: {e})")
