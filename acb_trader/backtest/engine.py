"""
ACB Trader — Backtesting Engine
Replays the full EOD signal pipeline over historical daily OHLCV data.
Simulates trade execution with realistic entry/exit logic.

Usage:
    from acb_trader.backtest.engine import BacktestEngine
    engine = BacktestEngine(data_dir="backtest_data", start="2023-01-01", end="2024-12-31")
    results = engine.run()
    engine.print_report(results)
"""

from __future__ import annotations
import math
import pandas as pd
from datetime import datetime, date, timedelta
from typing import Optional
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

from acb_trader.config import (
    BASKETS, RISK_PER_TRADE_PCT,
    FIVE_STAR_SCORE, MIN_TARGET_PIPS, MAX_STOP_PIPS, INSTRUMENT_CLASS,
    WEEKLY_DD_HALT_PCT, FIVE_STAR_TRANCHES, SESSION_TRADE_TRANCHES, TRAIL_STEP_PIPS,
    MONITOR_ONLY_PATTERNS,
    BACKTEST_HALF_SPREAD_PIPS, BACKTEST_SLIPPAGE_PIPS,
    BACKTEST_SIMULATED_STOP_PIPS, BACKTEST_NEWS_LOOKAHEAD_BARS,
)
# NOTE: MIN_SETUP_SCORE is intentionally NOT imported here.
# The floor check lives in setups.py via cfg.MIN_SETUP_SCORE (dynamic module access),
# which allows the optimizer to sweep threshold values at runtime.
from acb_trader.execution.coil import has_ema_coil_htf
from acb_trader.data.levels import (
    compute_atr, compute_close_streak, compute_day_break_counter,
    get_pip_size, get_pip_multiplier, price_to_pips, snap_to_quarter, snap_stop_beyond,
)
from acb_trader.signals.classify import classify_market_state, rank_basket
from acb_trader.signals.watchlist import evaluate_watchlist
from acb_trader.signals.weekly import build_weekly_template
from acb_trader.signals.setups import detect_setups
from acb_trader.execution.sizing import calculate_position_size, calculate_rr
from acb_trader.db.models import Setup, TradeRecord
from acb_trader.backtest.data_loader import load_basket_csvs, filter_date_range

ET = ZoneInfo("America/New_York")

# Minimum bars of history needed before classification is reliable
WARMUP_BARS = 20


@dataclass
class BacktestTrade:
    setup: Setup
    entry_bar_idx: int          # Index in daily_ohlcv when signal fired
    entry_price: float = 0.0
    exit_price: float = 0.0      # Average exit price across all tranches
    exit_bar_idx: int = -1
    terminal_state: str = "PENDING"
    pips: float = 0.0            # Average pips across all tranches
    r_multiple: float = 0.0      # Total R achieved
    lot_size: float = 0.01
    
    # Scaling state
    tranches_closed: dict[str, float] = field(default_factory=dict) # label -> exit_price
    stop_current: float = 0.0
    t1_hit: bool = False
    t2_hit: bool = False
    litmus_passed: bool = False # Track 100-Lot test
    trail_stop: float = 0.0
    notes: str = ""


@dataclass
class BacktestResults:
    trades: list[BacktestTrade] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)
    starting_balance: float = 10_000.0
    final_balance: float = 10_000.0

    @property
    def total_trades(self) -> int:
        return len([t for t in self.trades if t.terminal_state != "PENDING"])

    @property
    def wins(self) -> list[BacktestTrade]:
        return [t for t in self.trades if t.r_multiple > 0]

    @property
    def losses(self) -> list[BacktestTrade]:
        return [t for t in self.trades if t.r_multiple < 0]

    @property
    def win_rate(self) -> float:
        if not self.total_trades: return 0.0
        return len(self.wins) / self.total_trades

    @property
    def avg_win_r(self) -> float:
        if not self.wins: return 0.0
        return sum(t.r_multiple for t in self.wins) / len(self.wins)

    @property
    def avg_loss_r(self) -> float:
        if not self.losses: return 0.0
        return sum(t.r_multiple for t in self.losses) / len(self.losses)

    @property
    def expectancy(self) -> float:
        """Expected R per trade."""
        if not self.total_trades: return 0.0
        return sum(t.r_multiple for t in self.trades if t.terminal_state != "PENDING") / self.total_trades

    @property
    def max_drawdown_pct(self) -> float:
        """Maximum drawdown as % of peak equity."""
        if len(self.equity_curve) < 2: return 0.0
        peak = self.equity_curve[0]
        max_dd = 0.0
        for eq in self.equity_curve:
            if eq > peak:
                peak = eq
            dd = (peak - eq) / peak
            max_dd = max(max_dd, dd)
        return max_dd

    @property
    def total_pips(self) -> float:
        return sum(t.pips for t in self.trades if t.terminal_state != "PENDING")

    @property
    def profit_factor(self) -> float:
        gross_win  = sum(t.r_multiple for t in self.wins)
        gross_loss = abs(sum(t.r_multiple for t in self.losses))
        return round(gross_win / gross_loss, 2) if gross_loss > 0 else float("inf")


class BacktestEngine:

    def __init__(
        self,
        data_dir: str = "backtest_data",
        start: str | None = None,
        end: str | None = None,
        starting_balance: float = 10_000.0,
        pairs: list[str] | None = None,
        verbose: bool = True,
    ):
        self.data_dir = data_dir
        self.start = start
        self.end = end
        self.starting_balance = starting_balance
        self.pairs = pairs or [p for basket in BASKETS.values() for p in basket]
        self.verbose = verbose
        self._data: dict[str, pd.DataFrame] = {}

    def load_data(self) -> "BacktestEngine":
        """Load all pair CSVs from data_dir."""
        raw = load_basket_csvs(self.data_dir, self.pairs, "D1")
        for pair, df in raw.items():
            self._data[pair] = filter_date_range(df, self.start, self.end)
        if not self._data:
            raise ValueError(
                f"No data loaded from {self.data_dir}. "
                "Export CSVs from MT5 History Center first.\n"
                "MT5: Tools → History Center → select pair + D1 → right-click → Export Bars"
            )
        return self

    def run(self) -> BacktestResults:
        """Run the full backtest over all loaded pairs."""
        if not self._data:
            self.load_data()

        results = BacktestResults(starting_balance=self.starting_balance)
        balance = self.starting_balance
        results.equity_curve.append(balance)
        self._all_discarded: list[dict] = []  # diagnostic log

        # Find the common date range
        all_dates = sorted(set(
            d for df in self._data.values()
            for d in df["date"].dt.date
        ))

        if self.verbose:
            print(f"\n{'='*60}")
            print(f"ACB BACKTEST: {len(self._data)} pairs | {len(all_dates)} trading days")
            print(f"Range: {all_dates[0]} to {all_dates[-1]}")
            print(f"Balance: ${balance:,.2f}")
            print(f"{'='*60}\n")

        # Track pending trades waiting for next-day entry
        pending: list[BacktestTrade] = []

        for bar_date in all_dates:
            # ── Try to fill pending entries ───────────────────────────────────
            still_pending = []
            for bt in pending:
                filled = self._try_fill(bt, bar_date)
                if filled:
                    results.trades.append(bt)
                    if self.verbose:
                        print(f"  ✅ ENTERED {bt.setup.pair} {bt.setup.direction} "
                              f"{bt.setup.pattern} @ {bt.entry_price:.5f}")
                else:
                    # Expire if past entry_date
                    # MRN first-bounce: extend fill window by N bars beyond entry_date.
                    # In live trading, news_rearm waits for settle then enters on first bounce.
                    # Here we allow the limit order to survive BACKTEST_NEWS_LOOKAHEAD_BARS
                    # extra bars before expiring.
                    _days_past = _trading_days_between(bt.setup.entry_date, bar_date)
                    if _days_past > BACKTEST_NEWS_LOOKAHEAD_BARS:
                        bt.terminal_state = "EXPIRED"
                        results.trades.append(bt)
                        if self.verbose:
                            print(f"  ⏰ EXPIRED {bt.setup.pair} {bt.setup.pattern}")
                    else:
                        still_pending.append(bt)
            pending = still_pending

            # ── Simulate exits for active trades ─────────────────────────────
            for bt in results.trades:
                if bt.terminal_state in ("ACTIVE",):
                    exit_result = self._simulate_exit(bt, bar_date)
                    if exit_result:
                        pnl_r = bt.r_multiple
                        risk_amount = balance * RISK_PER_TRADE_PCT
                        pnl_usd = risk_amount * pnl_r
                        balance += pnl_usd
                        results.equity_curve.append(balance)
                        if self.verbose:
                            icon = "💰" if pnl_r > 0 else "💸"
                            print(f"  {icon} EXIT {bt.setup.pair} {bt.terminal_state} "
                                  f"{bt.r_multiple:+.2f}R ({bt.pips:+.1f} pips) "
                                  f"Balance: ${balance:,.2f}")

            # ── Weekly Circuit Breaker ─────────────────────────────────────────
            # Calculate current week's DD
            current_week = bar_date.isocalendar().week
            current_year = bar_date.year
            week_trades = [t for t in results.trades if t.setup.signal_date.isocalendar().week == current_week 
                           and t.setup.signal_date.year == current_year and t.terminal_state != "PENDING"]
            week_pnl_r = sum(t.r_multiple for t in week_trades)
            week_dd_pct = abs(min(0, week_pnl_r)) * RISK_PER_TRADE_PCT
            
            if week_dd_pct >= WEEKLY_DD_HALT_PCT:
                if self.verbose and bar_date.weekday() == 0: # Only print once at start of scan
                    print(f"  🛑 CIRCUIT BREAKER: Weekly DD {week_dd_pct:.1%} reached. Halting scan.")
                continue

            # ── EOD signal scan ───────────────────────────────────────────────
            new_setups = self._eod_scan(bar_date)
            for setup in new_setups:
                lot = calculate_position_size(
                    balance, setup.entry_price, setup.stop_price, setup.pair
                )
                bt = BacktestTrade(
                    setup=setup,
                    entry_bar_idx=all_dates.index(bar_date),
                    lot_size=lot,
                    litmus_passed=("100-Lot Litmus Test Passed" in setup.notes)
                )
                pending.append(bt)
                if self.verbose:
                    rr = calculate_rr(setup)
                    print(f"  🔔 SIGNAL {setup.pair} {setup.direction} "
                          f"{setup.pattern} score={setup.score} R:R={rr} "
                          f"[{setup.trade_type}]")

        results.final_balance = balance
        return results

    # ── PRIVATE: EOD SCAN ─────────────────────────────────────────────────────

    def _eod_scan(self, bar_date: date) -> list[Setup]:
        """Run the full signal pipeline for a given historical date."""
        all_setups: list[Setup] = []
        states: dict[str, object] = {}

        for pair, full_df in self._data.items():
            # Slice data up to and including this bar_date (simulate EOD)
            df_slice = full_df[full_df["date"].dt.date <= bar_date].copy()
            if len(df_slice) < WARMUP_BARS:
                continue

            # Build dummy intraday data from daily OHLC
            # In backtest mode: we use the daily bar's high/low as intraday proxy
            last_bar = df_slice.iloc[-1]
            intraday_proxy = pd.DataFrame([{
                "date": last_bar["date"],
                "high": last_bar["high"],
                "low": last_bar["low"],
                "close": last_bar["close"],
            }])

            try:
                state = classify_market_state(
                    pair=pair,
                    daily_ohlcv=df_slice,
                    intraday_1min=intraday_proxy,
                    session_1min=intraday_proxy,
                    current_week=bar_date.isocalendar().week,
                    current_month=bar_date.month,
                    as_of=bar_date,
                )
                states[pair] = state

                days_into_month = _count_trading_days_this_month(df_slice, bar_date)
                template = build_weekly_template(
                    pair=pair,
                    daily_ohlcv=df_slice,
                    current_week=bar_date.isocalendar().week,
                    current_month=bar_date.month,
                    days_into_month=days_into_month,
                    atr14=state.atr14,
                    close_streak=state.close_streak,
                    cib_direction=state.cib_direction,
                    as_of=bar_date,
                )

                wl = evaluate_watchlist(
                    state=state,
                    daily_ohlcv=df_slice,
                    prior_week_high=state.how,
                    prior_week_low=state.low_of_week,
                    prior_month_high=state.hom,
                    prior_month_low=state.lom,
                    as_of=bar_date,
                    template=template,
                )
                if not wl.on_watchlist:
                    continue

                # Daily EMA coil proxy — reuses has_ema_coil_htf() from execution/coil.py.
                # Fires when all [9, 20, 50] EMAs converge AND last 3 daily bars are sideways.
                # timeframe="DAILY" applies the 0.75 × ATR14 professional-boundary multiplier;
                # the tighter 0.5 × multiplier is reserved for the intraday 15-min execution gate.
                ema_coil = has_ema_coil_htf(df_slice, state.atr14, timeframe="DAILY")

                setups, discarded = detect_setups(
                    state=state,
                    template=template,
                    daily_ohlcv=df_slice,
                    as_of=bar_date,
                    ema_coil=ema_coil,
                    skip_stop_gate=True,        # lift MAX_STOP_PIPS ceiling
                    sim_stop_pips={},           # disabled: scoring-only sim distorts selection
                )
                # Filter out "Monitor Only" patterns
                setups = [s for s in setups if s.pattern not in MONITOR_ONLY_PATTERNS]

                # Fix entry_date: setups.py uses datetime.now() for live trading,
                # but in backtest we need the next historical trading day.
                next_bar = _next_historical_trading_day(bar_date)
                for s in setups:
                    s.entry_date = next_bar
                    s.expires = next_bar

                all_setups.extend(setups)

                for d in discarded:
                    if d.reason not in ("MARKET_IS_RANGING",):
                        self._all_discarded.append({
                            "date":         bar_date,
                            "pair":         pair,
                            "pattern":      d.pattern,
                            "reason":       d.reason,
                            "direction":    d.direction,
                            "score":        d.score,
                            "entry_price":  d.entry_price,
                            "stop_price":   d.stop_price,
                            "target_1":     d.target_1,
                        })
                        if self.verbose:
                            print(f"  [discard] {pair} {d.pattern} — {d.reason}")

            except Exception as e:
                if self.verbose:
                    print(f"  [scan] {pair} {bar_date}: {e}")
                continue

        all_setups.sort(key=lambda s: s.score, reverse=True)
        return all_setups[:3]  # Max 3 signals per day across all pairs

    # ── PRIVATE: FILL SIMULATION ─────────────────────────────────────────────

    def _try_fill(self, bt: BacktestTrade, bar_date: date) -> bool:
        """
        Simulate entry fill on entry_date.
        Uses next day's open as fill price if it's within range of entry.
        """
        if bar_date != bt.setup.entry_date:
            return False

        df = self._data.get(bt.setup.pair)
        if df is None:
            return False

        bar = df[df["date"].dt.date == bar_date]
        if len(bar) == 0:
            return False

        bar = bar.iloc[0]
        pair = bt.setup.pair

        # Transaction cost: half-spread + slippage, applied against the trader
        pip_size = get_pip_size(pair)
        txn_cost = (BACKTEST_HALF_SPREAD_PIPS + BACKTEST_SLIPPAGE_PIPS) * pip_size

        # Limit order simulation: fills if price trades through entry_price
        entry = bt.setup.entry_price
        if bt.setup.direction == "SHORT":
            # Short limit: fills if today's high reaches entry
            if float(bar["high"]) >= entry:
                bt.entry_price = entry - txn_cost  # worse fill for short
                bt.terminal_state = "ACTIVE"
                return True
        else:
            # Long limit: fills if today's low reaches entry
            if float(bar["low"]) <= entry:
                bt.entry_price = entry + txn_cost  # worse fill for long
                bt.terminal_state = "ACTIVE"
                return True

        # Stop entry simulation: fills if price breaks through entry
        # (fallback: use open as fill if it gaps through entry)
        if bt.setup.direction == "SHORT" and float(bar["open"]) <= entry:
            bt.entry_price = float(bar["open"]) - txn_cost
            bt.terminal_state = "ACTIVE"
            return True
        if bt.setup.direction == "LONG" and float(bar["open"]) >= entry:
            bt.entry_price = float(bar["open"]) + txn_cost
            bt.terminal_state = "ACTIVE"
            return True

        return False

    # ── PRIVATE: EXIT SIMULATION ──────────────────────────────────────────────

    def _simulate_exit(self, bt: BacktestTrade, bar_date: date) -> bool:
        """
        Simulate 3-tranche exit using daily high/low range.
        Handles Scaling (T1 -> BE), T2, and Tranche C Trailing.
        Fair fill: on bars touching both stop and target, uses open→extreme
        distance to determine which price was likely reached first.
        """
        if bt.terminal_state != "ACTIVE":
            return False

        df = self._data.get(bt.setup.pair)
        if df is None:
            return False

        bar = df[df["date"].dt.date == bar_date]
        if len(bar) == 0:
            return False

        bar = bar.iloc[0]
        high = float(bar["high"])
        low  = float(bar["low"])
        pair = bt.setup.pair
        direction = bt.setup.direction
        pip_size = get_pip_size(pair)
        
        # Current stops/targets
        if bt.stop_current == 0:
            bt.stop_current = bt.setup.stop_price

        t1 = bt.setup.target_1
        t2 = bt.setup.target_2

        # Exit transaction cost (spread + slippage against the trader)
        exit_txn = (BACKTEST_HALF_SPREAD_PIPS + BACKTEST_SLIPPAGE_PIPS) * pip_size

        # Tranche config
        tranche_map = FIVE_STAR_TRANCHES if bt.setup.trade_type == "FIVE_STAR_SCALABLE" else SESSION_TRADE_TRANCHES

        def close_tranche(label: str, price: float):
            # Apply exit transaction cost against the trader
            if direction == "SHORT":
                price = price + exit_txn   # buying back: worse (higher) price
            else:
                price = price - exit_txn   # selling: worse (lower) price
            if label not in bt.tranches_closed:
                bt.tranches_closed[label] = price
                if label == "A":
                    bt.t1_hit = True
                    # Move stop to BE (Five Star only)
                    if bt.setup.trade_type == "FIVE_STAR_SCALABLE":
                        bt.stop_current = bt.entry_price
                if label == "B":
                    bt.t2_hit = True
                    # Init trailing stop for C
                    bt.trail_stop = price

        def finalize_trade(state: str, final_exit: float):
            # Apply exit cost to remaining tranches closed at final_exit
            if direction == "SHORT":
                adj_exit = final_exit + exit_txn
            else:
                adj_exit = final_exit - exit_txn
            for label in tranche_map:
                if label not in bt.tranches_closed:
                    bt.tranches_closed[label] = adj_exit
            
            # Weighted average calculations
            total_r = 0.0
            total_pips = 0.0
            total_exit = 0.0
            
            pip_mult = get_pip_multiplier(pair)
            pip_sign = 1 if direction == "LONG" else -1
            
            for label, weight in tranche_map.items():
                exit_p = bt.tranches_closed[label]
                pips = (exit_p - bt.entry_price) * pip_mult * pip_sign
                r_mult = pips / bt.setup.risk_pips if bt.setup.risk_pips else 0
                
                total_r += r_mult * weight
                total_pips += pips * weight
                total_exit += exit_p * weight
                
            bt.r_multiple = round(total_r, 2)
            bt.pips = round(total_pips, 1)
            bt.exit_price = total_exit
            bt.terminal_state = state
            return True

        # ── EXITS ─────────────────────────────────────────────────────────────
        
        # Determine intra-bar order: which extreme (high/low) was likely
        # reached first?  Use distance from open as a proxy — the closer
        # extreme to the open is assumed to have been hit first.
        bar_open = float(bar["open"])
        high_first = abs(high - bar_open) < abs(bar_open - low)
        # (If equidistant, side with target — Burke's ACB trades *should* work.)

        if direction == "SHORT":
            stop_hit   = high >= bt.stop_current
            t1_hit_bar = low <= t1
            t2_hit_bar = low <= t2
            trail_hit  = bt.t2_hit and high >= bt.trail_stop

            # If both stop and target hit on same bar, award whichever extreme came first
            if stop_hit and (t1_hit_bar or t2_hit_bar):
                if not high_first:  # Low (target side) hit first
                    if t1_hit_bar:
                        close_tranche("A", t1)
                    if t2_hit_bar:
                        close_tranche("B", t2)
                    return finalize_trade("PARTIAL_STOP", bt.stop_current)
                # else: high (stop side) hit first — fall through to normal stop

            # 1. Check Stop
            if stop_hit:
                return finalize_trade("STOPPED_OUT" if not bt.t1_hit else "PARTIAL_STOP", bt.stop_current)

            # 2. Check Tranche C Trail
            if trail_hit:
                return finalize_trade("TRAIL_CLOSE", bt.trail_stop)

            # 3. Check Targets
            if t1_hit_bar:
                close_tranche("A", t1)
            if t2_hit_bar:
                close_tranche("B", t2)

            # 4. Update Trail for C
            if bt.t2_hit:
                new_trail = low + TRAIL_STEP_PIPS * pip_size
                if bt.trail_stop == 0 or new_trail < bt.trail_stop:
                    bt.trail_stop = new_trail

        else:  # LONG
            stop_hit   = low <= bt.stop_current
            t1_hit_bar = high >= t1
            t2_hit_bar = high >= t2
            trail_hit  = bt.t2_hit and low <= bt.trail_stop

            # If both stop and target hit on same bar, award whichever extreme came first
            if stop_hit and (t1_hit_bar or t2_hit_bar):
                if high_first:  # High (target side) hit first
                    if t1_hit_bar:
                        close_tranche("A", t1)
                    if t2_hit_bar:
                        close_tranche("B", t2)
                    return finalize_trade("PARTIAL_STOP", bt.stop_current)
                # else: low (stop side) hit first — fall through to normal stop

            # 1. Check Stop
            if stop_hit:
                return finalize_trade("STOPPED_OUT" if not bt.t1_hit else "PARTIAL_STOP", bt.stop_current)

            # 2. Check Tranche C Trail
            if trail_hit:
                return finalize_trade("TRAIL_CLOSE", bt.trail_stop)

            # 3. Check Targets
            if t1_hit_bar:
                close_tranche("A", t1)
            if t2_hit_bar:
                close_tranche("B", t2)

            # 4. Update Trail for C
            if bt.t2_hit:
                new_trail = high - TRAIL_STEP_PIPS * pip_size
                if new_trail > bt.trail_stop:
                    bt.trail_stop = new_trail

        # Check if all tranches are hit (e.g. T2 hit on a SESSION_TRADE with no C)
        if all(label in bt.tranches_closed for label in tranche_map):
            return finalize_trade("FULL_TARGET_CLOSE", t2 if "B" in tranche_map else t1)

        # Force close after max hold (5 trading days)
        days_held = _trading_days_between(bt.setup.entry_date, bar_date)
        if days_held >= 5:
            return finalize_trade("FORCE_CLOSE", float(bar["close"]))

        return False

    # ── REPORT ────────────────────────────────────────────────────────────────

    def print_report(self, results: BacktestResults):
        r = results
        from collections import Counter
        closed = [t for t in r.trades if t.terminal_state != "PENDING"]

        print(f"\n{'='*60}")
        print(f"  ACB BACKTEST RESULTS")
        print(f"{'='*60}")
        print(f"  Starting balance:  ${r.starting_balance:>10,.2f}")
        print(f"  Final balance:     ${r.final_balance:>10,.2f}")
        net = r.final_balance - r.starting_balance
        pct = net / r.starting_balance * 100
        print(f"  Net P&L:           ${net:>+10,.2f}  ({pct:+.1f}%)")
        print(f"{'─'*60}")
        print(f"  Total trades:      {r.total_trades}")
        print(f"  Win rate:          {r.win_rate:.1%}")
        print(f"  Avg win:           {r.avg_win_r:+.2f}R")
        print(f"  Avg loss:          {r.avg_loss_r:+.2f}R")
        print(f"  Expectancy:        {r.expectancy:+.2f}R per trade")

        # Core Expectancy — strip all outlier trades (r_multiple > 3R)
        outliers = [t for t in closed if t.r_multiple > 3.0]
        core_trades = [t for t in closed if t.r_multiple <= 3.0]
        if core_trades:
            core_exp = sum(t.r_multiple for t in core_trades) / len(core_trades)
            print(f"  Core Expectancy:   {core_exp:+.2f}R per trade  "
                  f"({len(outliers)} outlier(s) >3R removed: "
                  f"{', '.join(f'{o.setup.pair} {o.r_multiple:+.1f}R' for o in outliers)})")
        else:
            print(f"  Core Expectancy:   n/a")

        print(f"  Profit factor:     {r.profit_factor:.2f}")
        print(f"  Total pips:        {r.total_pips:+.0f}")
        print(f"  Max drawdown:      {r.max_drawdown_pct:.1%}")
        print(f"{'─'*60}")

        # Breakdown by pattern
        pattern_counts = Counter(t.setup.pattern for t in closed)
        print(f"  Trades by pattern:")
        for pat, count in pattern_counts.most_common():
            pat_trades = [t for t in closed if t.setup.pattern == pat]
            pat_wins   = [t for t in pat_trades if t.r_multiple > 0]
            pat_r      = sum(t.r_multiple for t in pat_trades)
            wr = len(pat_wins) / len(pat_trades) if pat_trades else 0
            print(f"    {pat:<30} {count:>3} trades  {wr:.0%} WR  {pat_r:+.2f}R net")

        print(f"{'─'*60}")

        # Trade tier breakdown — FIVE_STAR (EMA coil confirmed) vs SESSION_TRADE
        five_star = [t for t in closed if t.setup.trade_type == "FIVE_STAR_SCALABLE"]
        session   = [t for t in closed if t.setup.trade_type == "SESSION_TRADE"]
        if five_star:
            fs_wr  = len([t for t in five_star if t.r_multiple > 0]) / len(five_star)
            fs_exp = sum(t.r_multiple for t in five_star) / len(five_star)
            print(f"  FIVE_STAR:         {len(five_star):>3} trades  {fs_wr:.0%} WR  {fs_exp:+.2f}R/trade  (EMA coil confirmed)")
        if session:
            s_wr  = len([t for t in session if t.r_multiple > 0]) / len(session)
            s_exp = sum(t.r_multiple for t in session) / len(session)
            print(f"  SESSION_TRADE:     {len(session):>3} trades  {s_wr:.0%} WR  {s_exp:+.2f}R/trade")

        print(f"{'─'*60}")

        # FORCE_CLOSE breakdown
        fc_trades = [t for t in closed if t.terminal_state == "FORCE_CLOSE"]
        if fc_trades:
            fc_wins   = [t for t in fc_trades if t.r_multiple > 0]
            fc_losses = [t for t in fc_trades if t.r_multiple < 0]
            fc_flat   = [t for t in fc_trades if t.r_multiple == 0]
            fc_avg    = sum(t.r_multiple for t in fc_trades) / len(fc_trades)
            print(f"  FORCE_CLOSE:       {len(fc_trades)} total  "
                  f"({len(fc_wins)} wins / {len(fc_losses)} losses / {len(fc_flat)} flat)  "
                  f"avg {fc_avg:+.2f}R")

        # Best/worst trades
        if closed:
            best  = max(closed, key=lambda t: t.r_multiple, default=None)
            worst = min(closed, key=lambda t: t.r_multiple, default=None)
            if best:
                print(f"  Best trade:        {best.setup.pair} {best.setup.pattern} "
                      f"{best.r_multiple:+.2f}R  [{best.terminal_state}]")
            if worst:
                print(f"  Worst trade:       {worst.setup.pair} {worst.setup.pattern} "
                      f"{worst.r_multiple:+.2f}R  [{worst.terminal_state}]")
        print(f"{'='*60}\n")

    def discard_analysis(self, lookahead_bars: int = 3) -> pd.DataFrame:
        """
        For every BELOW_MIN_SCORE discard that has price levels, simulate whether
        T1 or the stop would have been hit within `lookahead_bars` trading days.

        Outcome per discard:
          T1_HIT   — price reached target_1 before stop (filter was too tight)
          STOP_HIT — price hit stop before target_1 (filter was correct)
          EXPIRED  — neither level reached within the window (inconclusive)

        Returns a DataFrame with per-row outcomes AND a summary grouped by
        (pattern, reason) with hit-rate, so you can identify over-filtering.
        """
        rows = []
        for rec in self._all_discarded:
            if rec["reason"] != "BELOW_MIN_SCORE":
                continue
            entry  = rec.get("entry_price", 0.0)
            stop   = rec.get("stop_price",  0.0)
            t1     = rec.get("target_1",    0.0)
            if not (entry and stop and t1):
                continue

            direction = rec.get("direction", "")
            pair      = rec["pair"]
            sig_date  = rec["date"]

            df = self._data.get(pair)
            if df is None:
                continue

            future = df[df["date"].dt.date > sig_date].head(lookahead_bars)
            outcome = "EXPIRED"
            for _, bar in future.iterrows():
                high = float(bar["high"])
                low  = float(bar["low"])
                bar_open = float(bar["open"])
                high_first = abs(high - bar_open) < abs(bar_open - low)
                
                if direction == "SHORT":
                    t1_hit = low <= t1
                    stop_hit = high >= stop
                    if t1_hit and stop_hit:
                        outcome = "T1_HIT" if not high_first else "STOP_HIT"
                        break
                    if t1_hit:
                        outcome = "T1_HIT"
                        break
                    if stop_hit:
                        outcome = "STOP_HIT"
                        break
                else:
                    t1_hit = high >= t1
                    stop_hit = low <= stop
                    if t1_hit and stop_hit:
                        outcome = "T1_HIT" if high_first else "STOP_HIT"
                        break
                    if t1_hit:
                        outcome = "T1_HIT"
                        break
                    if stop_hit:
                        outcome = "STOP_HIT"
                        break

            rows.append({
                "date":      sig_date,
                "pair":      pair,
                "pattern":   rec["pattern"],
                "direction": direction,
                "score":     rec["score"],
                "entry":     entry,
                "stop":      stop,
                "t1":        t1,
                "outcome":   outcome,
            })

        if not rows:
            print("[discard_analysis] No BELOW_MIN_SCORE discards with price levels found.")
            return pd.DataFrame()

        detail = pd.DataFrame(rows)

        # Summary: group by pattern, count outcomes, compute T1 hit rate
        summary_rows = []
        for pattern, grp in detail.groupby("pattern"):
            total    = len(grp)
            t1_hits  = (grp["outcome"] == "T1_HIT").sum()
            s_hits   = (grp["outcome"] == "STOP_HIT").sum()
            expired  = (grp["outcome"] == "EXPIRED").sum()
            hit_rate = t1_hits / total if total else 0.0
            verdict  = (
                "FILTER_TOO_TIGHT"  if hit_rate >= 0.50 else
                "FILTER_MARGINAL"   if hit_rate >= 0.35 else
                "FILTER_WORKING"
            )
            summary_rows.append({
                "pattern":        pattern,
                "discards":       total,
                "t1_hit":         t1_hits,
                "stop_hit":       s_hits,
                "expired":        expired,
                "t1_hit_rate":    f"{hit_rate:.0%}",
                "hit_rate_raw":   hit_rate,
                "verdict":        verdict,
            })

        summary = pd.DataFrame(summary_rows).sort_values("hit_rate_raw", ascending=False)

        print("\n── Discard Analysis (BELOW_MIN_SCORE) ──")
        print(f"  Lookahead window : {lookahead_bars} bars")
        print(f"  Total evaluated  : {len(detail)}")
        print()
        print(summary.to_string(index=False))
        print()
        return detail

    def discard_analysis_csv(self,
                             lookahead_bars: int = 3,
                             filepath: str = "backtest_discards.csv") -> pd.DataFrame:
        """Run discard_analysis() and export the detail rows to CSV."""
        detail = self.discard_analysis(lookahead_bars=lookahead_bars)
        if not detail.empty:
            detail.to_csv(filepath, index=False)
            print(f"[backtest] Discard analysis exported to {filepath}")
        return detail

    def to_csv(self, results: BacktestResults, filepath: str = "backtest_results.csv"):
        """Export all trades to CSV for further analysis in Excel/Sheets."""
        rows = []
        for t in results.trades:
            if t.terminal_state == "PENDING":
                continue
            rows.append({
                "pair":           t.setup.pair,
                "pattern":        t.setup.pattern,
                "direction":      t.setup.direction,
                "trade_type":     t.setup.trade_type,
                "score":          t.setup.score,
                "ema_coil_confirmed": t.setup.ema_coil_confirmed,
                "signal_date":    t.setup.signal_date,
                "entry_date":     t.setup.entry_date,
                "entry_price":    t.entry_price,
                "stop_price":     t.setup.stop_price,
                "target_1":       t.setup.target_1,
                "exit_price_avg": t.exit_price,
                "tranche_a":      t.tranches_closed.get("A"),
                "tranche_b":      t.tranches_closed.get("B"),
                "tranche_c":      t.tranches_closed.get("C"),
                "terminal_state": t.terminal_state,
                "pips":           t.pips,
                "r_multiple":     t.r_multiple,
                "lot_size":       t.lot_size,
                "litmus_passed":  t.litmus_passed,
                "notes":          t.setup.notes,
            })
        df = pd.DataFrame(rows)
        df.to_csv(filepath, index=False)
        print(f"[backtest] Results exported to {filepath}")
        return df


# ── HELPERS ───────────────────────────────────────────────────────────────────

def _next_historical_trading_day(d: date) -> date:
    """Return the next Monday–Friday from d, skipping weekends."""
    nxt = d + timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    return nxt


def _count_trading_days_this_month(df: pd.DataFrame, as_of: date) -> int:
    month_start = date(as_of.year, as_of.month, 1)
    month_bars = df[(df["date"].dt.date >= month_start) & (df["date"].dt.date <= as_of)]
    return max(1, len(month_bars))


def _trading_days_between(start: date, end: date) -> int:
    count = 0
    d = start
    while d <= end:
        if d.weekday() < 5:
            count += 1
        d += timedelta(days=1)
    return count
