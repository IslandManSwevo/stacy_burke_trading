"""
ACB Trader — Backtest Parameter Optimizer
Sweeps key config parameters to find optimal thresholds.
Run after initial backtest to calibrate MIN_SETUP_SCORE, BREAKEVEN_PIPS, etc.

Usage:
    python -m acb_trader.backtest.optimizer
"""

from __future__ import annotations
import copy
import pandas as pd
from dataclasses import dataclass
from acb_trader.backtest.engine import BacktestEngine
from acb_trader.backtest.data_loader import load_basket_csvs

# ── CONFIG ────────────────────────────────────────────────────────────────────
DATA_DIR         = "backtest_data"
START_DATE       = "2023-01-01"
END_DATE         = "2024-12-31"
STARTING_BALANCE = 10_000.0
PAIRS = [
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF",
    "AUDUSD", "NZDUSD", "USDCAD", "XAUUSD", "USOIL", "SP500"
]


@dataclass
class OptResult:
    min_score: int
    win_rate: float
    expectancy: float
    profit_factor: float
    total_trades: int
    max_dd: float
    net_pct: float


def run_score_sweep() -> pd.DataFrame:
    """
    Sweep MIN_SETUP_SCORE from 4 to 9 and compare results.
    Helps find the sweet spot between signal frequency and quality.
    """
    import acb_trader.config as cfg
    rows = []

    data = load_basket_csvs(DATA_DIR, PAIRS, "D1")
    if not data:
        print("No data — run from backtest_data directory with CSVs loaded.")
        return pd.DataFrame()

    print(f"\nSweeping MIN_SETUP_SCORE 4–9...\n{'─'*60}")
    print(f"{'Score':>6} {'Trades':>7} {'WinRate':>8} {'Expect':>8} {'PF':>6} {'MaxDD':>7} {'Net%':>7}")
    print(f"{'─'*60}")

    for score in range(4, 10):
        original = cfg.MIN_SETUP_SCORE
        cfg.MIN_SETUP_SCORE = score

        engine = BacktestEngine(
            data_dir=DATA_DIR, start=START_DATE, end=END_DATE,
            starting_balance=STARTING_BALANCE, pairs=PAIRS, verbose=False,
        )
        engine._data = {k: v.copy() for k, v in data.items()}  # defensive copy per run
        try:
            results = engine.run()
        except Exception as e:
            print(f"  {score:>4}   [ERROR: {e}]")
            cfg.MIN_SETUP_SCORE = original
            continue

        row = OptResult(
            min_score=score,
            win_rate=results.win_rate,
            expectancy=results.expectancy,
            profit_factor=results.profit_factor,
            total_trades=results.total_trades,
            max_dd=results.max_drawdown_pct,
            net_pct=(results.final_balance - STARTING_BALANCE) / STARTING_BALANCE,
        )
        rows.append(row)

        marker = " ◄ BEST" if row.expectancy == max(r.expectancy for r in rows) else ""
        pf_str = f"{row.profit_factor:>5.2f}" if row.profit_factor != float("inf") else "  ∞  "
        print(f"  {score:>4}   {row.total_trades:>6}   {row.win_rate:>7.1%}   "
              f"{row.expectancy:>+7.2f}R  {pf_str}  "
              f"{row.max_dd:>6.1%}  {row.net_pct:>+6.1%}{marker}")

        cfg.MIN_SETUP_SCORE = original

    df = pd.DataFrame([vars(r) for r in rows])
    df.to_csv("optimizer_score_sweep.csv", index=False)
    print(f"\nResults saved: optimizer_score_sweep.csv")

    best = max(rows, key=lambda r: r.expectancy)
    print(f"\n▶ Recommended MIN_SETUP_SCORE: {best.min_score} "
          f"({best.expectancy:+.2f}R expectancy, {best.win_rate:.0%} WR)")
    return df


def run_pattern_analysis() -> pd.DataFrame:
    """
    Disable patterns one at a time to see which contribute most to edge.
    """
    import acb_trader.backtest.engine as engine_module
    from acb_trader.signals.setups import detect_setups as real_detect

    all_patterns = [
        "PUMP_COIL_DUMP", "FIRST_RED_DAY", "INSIDE_FALSE_BREAK",
        "PARABOLIC_REVERSAL", "MONDAY_FALSE_BREAK",
    ]

    data = load_basket_csvs(DATA_DIR, PAIRS, "D1")
    if not data:
        return pd.DataFrame()

    rows = []
    print(f"\nPattern contribution analysis...\n{'─'*60}")

    for exclude_pattern in ["NONE"] + all_patterns:
        # Create fresh engine instance
        engine = BacktestEngine(
            data_dir=DATA_DIR, start=START_DATE, end=END_DATE,
            starting_balance=STARTING_BALANCE, pairs=PAIRS, verbose=False,
        )
        engine._data = data

        pat = exclude_pattern

        def patched_detect(state, template, daily_ohlcv, ema_coil=False, as_of=None):
            setups, discarded = real_detect(state, template, daily_ohlcv, ema_coil, as_of)
            if pat != "NONE":
                setups = [s for s in setups if s.pattern != pat]
            return setups, discarded

        # Patch in the engine module namespace
        engine_module.detect_setups = patched_detect
        results = engine.run()
        engine_module.detect_setups = real_detect

        label = f"Without {exclude_pattern}" if exclude_pattern != "NONE" else "ALL patterns"
        rows.append({
            "config": label,
            "trades": results.total_trades,
            "win_rate": f"{results.win_rate:.0%}",
            "expectancy": f"{results.expectancy:+.2f}R",
            "profit_factor": f"{results.profit_factor:.2f}",
            "net_pct": f"{(results.final_balance-STARTING_BALANCE)/STARTING_BALANCE:+.1%}",
        })
        print(f"  {label:<35} {results.total_trades:>4} trades  "
              f"{results.expectancy:+.2f}R expectancy")

    df = pd.DataFrame(rows)
    df.to_csv("optimizer_pattern_analysis.csv", index=False)
    print(f"\nResults saved: optimizer_pattern_analysis.csv")
    return df


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--score",   action="store_true", help="Run score threshold sweep")
    parser.add_argument("--pattern", action="store_true", help="Run pattern contribution analysis")
    parser.add_argument("--all",     action="store_true", help="Run all optimizations")
    args = parser.parse_args()

    if args.all or args.score:
        run_score_sweep()
    if args.all or args.pattern:
        run_pattern_analysis()
    if not any(vars(args).values()):
        run_score_sweep()  # default
