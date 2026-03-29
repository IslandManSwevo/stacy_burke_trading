"""
ACB Trader — Data Models
All dataclasses used across the system.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from acb_trader.signals._scoring import ScoreBreakdown


# ── MARKET CLASSIFICATION ─────────────────────────────────────────────────────

@dataclass
class TrapAnalysis:
    trapped_side: str           # "LONGS_TRAPPED" | "SHORTS_TRAPPED" | "NONE"
    trap_level: float
    stop_cluster: float
    trap_confidence: str        # "HIGH" | "MEDIUM" | "LOW"


@dataclass
class MarketState:
    pair: str
    state: str                  # "BREAKOUT" | "TRENDING" | "RANGING"
    substate: str               # e.g. "BREAKOUT_DAY_2", "TRENDING_BACK_SIDE"
    direction: str              # "BULLISH" | "BEARISH" | "NEUTRAL"
    close_streak: int           # Consecutive closes in same direction (signed)
    day_break_counter: int      # Day 1/2/3 counter — consecutive HOD/LOD breaks
    # Named structural levels
    hod: float
    lod: float
    how: float
    low_of_week: float
    hos: float
    los: float
    hom: float                  # High of Month (intraday) — NOT HCOM
    lom: float                  # Low of Month  (intraday) — NOT LCOM
    hcom: float                 # Highest Close of Month
    lcom: float                 # Lowest Close of Month
    # CIB signal
    cib: bool
    cib_direction: str          # "BULLISH" | "BEARISH" | "NONE"
    # Indicators
    atr14: float
    contraction_ratio: float
    box_size: float
    trap: TrapAnalysis
    basket_rank: int            # 1 = cleanest in basket, 0 = excluded
    classified_at: datetime


# ── WATCHLIST ─────────────────────────────────────────────────────────────────

@dataclass
class WatchlistResult:
    pair: str
    on_watchlist: bool
    criteria_met: list[str]
    priority: int               # 0-6


# ── WEEKLY TEMPLATE ───────────────────────────────────────────────────────────

@dataclass
class WeeklyAnchors:
    prior_week_high: float
    prior_week_low: float
    prior_week_hcow: float
    prior_week_lcow: float
    prior_month_high: float
    prior_month_low: float
    prior_month_hcom: float
    prior_month_lcom: float
    week_open: float
    current_week_high: float
    current_week_low: float
    current_hcow: float
    current_lcow: float
    month_open: float
    days_into_month: int
    # FDTM
    fdtm_open: Optional[float]
    fdtm_high: Optional[float]
    fdtm_low: Optional[float]
    fdtm_close: Optional[float]
    fdtm_complete: bool
    # Monthly phase
    monthly_phase: str          # "RESET" | "FRONTSIDE" | "BACKSIDE"


@dataclass
class OpeningRange:
    high: float
    low: float
    size_pips: float
    midpoint: float
    target_100pct_up: float
    target_100pct_dn: float
    target_200pct_up: float
    target_200pct_dn: float
    complete: bool              # False until Tuesday NY close


@dataclass
class CloseCountdown:
    pair: str
    direction: str              # "3HC" | "3LC"
    count: int
    label: str                  # "DAY_1" | "DAY_2" | "SIGNAL_DAY" | "NONE"
    at_hcom_lcom: bool
    at_hom_lom: bool
    signal_ready: bool          # True when count >= 3 (strict 3-day rule)


@dataclass
class DayRole:
    primary: str
    entry_bias: str             # "FRONT_SIDE" | "BACK_SIDE" | "WAIT" | "NO_ENTRY"


@dataclass
class WeeklyTemplate:
    pair: str
    week_number: int
    template_type: str          # "BREAKOUT_WEEK" | "REVERSAL_WEEK" | "RANGING_WEEK" | "NEW_MONTH_BREAKOUT"
    anchors: WeeklyAnchors
    opening_range: Optional[OpeningRange]
    day_role: DayRole
    high_locked: bool
    low_locked: bool
    valid_directions: list[str]
    best_setup_day: str
    template_confidence: str    # "HIGH" | "MEDIUM" | "LOW"
    close_countdown: CloseCountdown
    monthly_phase: str
    notes: str
    generated_at: datetime


# ── SETUP ─────────────────────────────────────────────────────────────────────

@dataclass
class Setup:
    pair: str
    pattern: str                # "PUMP_COIL_DUMP" | "FIRST_RED_DAY" | "INSIDE_FALSE_BREAK"
                                # | "PARABOLIC_REVERSAL" | "LOW_HANGING_FRUIT"
                                # | "MONDAY_FALSE_BREAK"
    direction: str              # "LONG" | "SHORT"
    entry_price: float
    stop_price: float
    target_1: float
    target_2: float
    target_3: Optional[float]
    risk_pips: float
    score: int                  # 0-14
    trade_type: str             # "SESSION_TRADE" | "FIVE_STAR_SCALABLE"
    signal_date: date
    entry_date: date            # signal_date + 1 trading day
    ema_coil_confirmed: bool
    expires: date
    notes: str
    breakdown: Optional["ScoreBreakdown"] = field(default=None)  # ScoreBreakdown; object avoids circular import
    news_events: list = field(default_factory=list)  # NewsEvent list when setup is news-paused


# ── SESSION LEVELS (live) ─────────────────────────────────────────────────────

@dataclass
class SessionLevels:
    hod: float
    lod: float
    how: float
    low_of_week: float
    hos: float
    los: float
    prior_hod: float
    prior_lod: float
    prior_hos: float
    prior_los: float


@dataclass
class CoilState:
    triggered: bool
    coil_low: float
    coil_high: float
    ema_spread: float
    bars_sideways: int


@dataclass
class InitialBalance:
    session: str
    ib_high: float
    ib_low: float
    ib_range_pips: float
    target_100pct: float
    target_200pct: float
    target_300pct: float
    retracement_50: float


# ── ACCOUNT STATE ─────────────────────────────────────────────────────────────

@dataclass
class AccountState:
    balance: float
    equity: float
    daily_pnl_pct: float
    weekly_drawdown_pct: float
    consecutive_losses: int
    open_positions: list[dict] = field(default_factory=list)
    pending_entries: list[Setup] = field(default_factory=list)


# ── TRADE RECORD ──────────────────────────────────────────────────────────────

@dataclass
class TradeRecord:
    trade_id: str
    pair: str
    pattern: str
    direction: str
    trade_type: str
    score: int
    session: str
    entry_price: float
    entry_time: datetime
    stop_price: float
    lot_size: float
    target_1: float
    target_2: float
    target_3: Optional[float]
    exit_price: float
    exit_time: datetime
    terminal_state: str         # "STOPPED_OUT" | "EXPIRED" | "FORCE_CLOSE" |
                                # "BREAKEVEN_CLOSE" | "TRAIL_CLOSE" | "FULL_TARGET_CLOSE"
    pips: float
    r_multiple: float
    notes: str


# ── SYSTEM HEALTH ─────────────────────────────────────────────────────────────

@dataclass
class SystemHealthResult:
    passed: bool
    failures: list[str]
    warnings: list[str]
    timestamp: datetime


# ── DISCARDED SETUP ───────────────────────────────────────────────────────────

@dataclass
class DiscardedSetup:
    pair: str
    pattern: str
    direction: str
    score: int
    reason: str
    discarded_at: datetime
    would_have_hit_t1: Optional[bool] = None   # Backtest review only
    # Price levels — populated when reason == BELOW_MIN_SCORE so discard_analysis()
    # can simulate whether T1 or stop would have been hit within the 3-bar window.
    entry_price: float = 0.0
    stop_price:  float = 0.0
    target_1:    float = 0.0


# ── WEEKLY REVIEW REPORT ──────────────────────────────────────────────────────

@dataclass
class WeeklyReviewReport:
    week_start: date
    week_end: date
    total_trades: int
    wins: int
    losses: int
    win_rate: float                     # 0.0–1.0
    total_pips: float
    total_r: float
    best_trade: Optional[str]           # e.g. "EURUSD +3.2R"
    worst_trade: Optional[str]          # e.g. "GBPUSD -1.0R"
    pattern_breakdown: dict             # pattern → {trades, wins, total_r}
    discards_would_have_hit: int        # discarded setups that would have reached T1
    discards_total: int
    weekly_dd_pct: float                # (balance_friday - balance_monday) / balance_monday
    generated_at: datetime
