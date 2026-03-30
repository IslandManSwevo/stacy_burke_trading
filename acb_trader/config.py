"""
ACB Trader — Configuration
All constants derived from agent.md and skill files.
Edit here only. Never hardcode values in modules.
"""

from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

# ── RISK ──────────────────────────────────────────────────────────────────────
RISK_PER_TRADE_PCT   = 0.01    # 1% account risk per trade
MIN_TARGET_PIPS      = 50      # Minimum distance to Target 1 (all instruments)
FIVE_STAR_SCORE      = 99      # Disabled: FIVE_STAR hurts at WR<46%. All trades SESSION_TRADE.
MIN_SETUP_SCORE      = 7       # Optimizer-confirmed optimal threshold
MIN_SETUP_RR         = 2.0    # Minimum planned R:R to T1 — rejects setups below 2:1 (e.g. R:R=1.4 low-qual entries)
ATR_PERIOD           = 14

# ── IFB VOLUME EXPANSION ─────────────────────────────────────────────────────
# Addresses the R:R blocker: IFB false-break day should show expanding tick volume
# vs the compressed inside day — confirms institutional participation on rejection.
# Purely additive: stacks with R:R bonus but provides an independent path to floor.
IFB_VOL_EXPANSION_RATIO = 1.5   # false-break day vol / inside day vol threshold
IFB_VOL_EXPANSION_BONUS = 2     # score points awarded when expansion confirmed

# ── STOPS (pips) ──────────────────────────────────────────────────────────────
MAX_STOP_PIPS = {
    # Tuple: (min_pips, max_pips)
    # Min enforces a floor (avoid entries with trivially tight stops).
    # Max is the hard ceiling — setups with wider stops are discarded.
    # Values reflect EOD daily-bar stop distances (high/low + 2 pips),
    # NOT the 5-min EMA coil intraday entries which need only 15–20 pips.
    "CURRENCIES": (15, 80),    # Daily currency ranges: typically 40–80 pips
    "GOLD":       (50, 300),   # XAUUSD daily range: 100–300 pips ($1–$3)
    "OIL":        (50, 300),   # Crude daily range: 100–300 pips
    "INDEXES":    (100, 500),  # SP500/NAS/DJ daily range: 200–500 index pips
}

# ── THREE LEVELS pip targets by instrument class ──────────────────────────────
THREE_LEVELS = {
    "CURRENCIES": {"L1": 75,  "L2": 150, "L3": 250, "L3_EXT": 300},
    "GOLD":       {"L1": 150, "L2": 250, "L3_EXT": 300},
    "OIL":        {"L1": 150, "L2": 250, "L3": 300, "L4": 500, "L5": 750},
    "INDEXES":    {"L1": 250, "L2": 500, "L3": 750},
}

# 25-pip box grid for "Three Levels of Rise or Fall"
# Each box = the distance between institutional 00/25/50/75 quarter levels.
# Three consecutive boxes = one expansion cycle = intraday exhaustion.
BOX_SIZE_PIPS = {
    "CURRENCIES": 25,     # 3 × 25 = 75-pip intraday exhaustion
    "GOLD":       50,     # $5.00 increments (pip=0.1 → 50 pips = $5)
    "OIL":        50,     # $0.50 increments
    "INDEXES":    25,     # 25-point increments on S&P / Nasdaq / Dow
}

INSTRUMENT_CLASS = {
    "EURUSD": "CURRENCIES", "GBPUSD": "CURRENCIES", "USDJPY": "CURRENCIES",
    "USDCHF": "CURRENCIES", "USDCAD": "CURRENCIES", "AUDUSD": "CURRENCIES",
    "NZDUSD": "CURRENCIES", "GBPJPY": "CURRENCIES", "EURJPY": "CURRENCIES",
    "AUDJPY": "CURRENCIES", "CADJPY": "CURRENCIES", "GBPAUD": "CURRENCIES",
    "GBPNZD": "CURRENCIES", "GBPCHF": "CURRENCIES", "NZDJPY": "CURRENCIES",
    "CHFJPY": "CURRENCIES",
    "XAUUSD": "GOLD",
    "USOIL":  "OIL",   "UKOIL": "OIL",
    "SP500":  "INDEXES", "NAS100": "INDEXES", "DJ30": "INDEXES",
}

# ── INSTRUMENT BASKETS ────────────────────────────────────────────────────────
# Scan order: agent trades the top-ranked pair per basket only.
# COMMODITY now holds the FX commodity-proxy pairs (AUD, CAD) — the physical
# commodities (gold, oil, indices) each have their own dedicated baskets so
# they are ranked against their natural peers, not against FX pairs.
BASKETS = {
    "USD_MAJORS":  ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD"],
    "GBP_CROSSES": ["GBPJPY", "GBPAUD", "GBPCAD", "GBPCHF", "GBPNZD"],
    "JPY_CROSSES": ["EURJPY", "AUDJPY", "CADJPY", "NZDJPY", "CHFJPY"],
    "COMMODITY":   ["AUDUSD", "USDCAD"],        # FX commodity proxies
    "METALS":      ["XAUUSD"],                  # Gold (XAGUSD addable once data available)
    "OIL":         ["USOIL", "UKOIL"],          # WTI + Brent
    "INDICES":     ["SP500", "NAS100", "DJ30"], # US equity indices (NY equity hour only)
}

# ── SESSION TIMES (ET, 24h) ───────────────────────────────────────────────────
SESSION_WINDOWS = {
    "ASIA":       {"open": (19, 0),  "close": (23, 0),  "equity_hour": (19, 0)},
    "LONDON":     {"open": (1,  0),  "close": (5,  0),  "equity_hour": (3,  0)},
    "NEW_YORK_FX":{"open": (7,  0),  "close": (11, 0),  "equity_hour": (7,  0)},
    "NEW_YORK_EQ":{"open": (9, 30),  "close": (11, 0),  "equity_hour": (9, 30)},
}

SESSION_PAIRS = {
    "ASIA":       ["AUDJPY", "NZDJPY", "GBPJPY", "EURJPY", "USDJPY", "AUDUSD", "NZDUSD", "XAUUSD"],
    "LONDON":     ["EURUSD", "GBPUSD", "USDCHF", "USDCAD", "GBPJPY", "EURJPY", "XAUUSD"],
    "NEW_YORK_FX":["EURUSD", "GBPUSD", "USDCHF", "USDCAD", "GBPJPY", "EURJPY", "XAUUSD"],
    "NEW_YORK_EQ":["SP500", "NAS100", "DJ30", "USOIL"],
}

# ── EOD / TIMING ─────────────────────────────────────────────────────────────
NY_CLOSE_HOUR        = 17
NY_CLOSE_MINUTE      = 0
EOD_RUN_OFFSET_MIN   = 5       # Fire EOD run 5 min after NY close

# ── EMA COIL ─────────────────────────────────────────────────────────────────
# Daily-bar proxy for EMA coil. The playbook's 5-period coil [8,21,55,100,200]
# is designed for intraday (15-min) charts. On daily bars, requiring 200 EMA
# convergence within 0.5×ATR is nearly impossible — it never fires.
# The 3-period proxy [9,20,50] is practical for daily bars: fires when the
# market is genuinely coiling on the daily timeframe.
EMA_COIL_PERIODS     = [9, 20, 50]              # Daily-bar proxy (intraday uses full 5)

# Timeframe-specific EMA coil thresholds (Spread <= MULT × ATR14):
#   INTRADAY (15-min execution gate) — strict: traps volume, stalled sideways
#     before London/NY open.  Wider than 0.5× on a 15-min chart = chop.
#   DAILY ("professional boundary") — relaxed to 0.75×: daily compressions
#     naturally land between 0.6–0.9× ATR.  Using 0.5× on daily bars blocks
#     valid highly-engineered PCD coils; 0.75× admits them while still
#     rejecting wide-body expansion bars.
EMA_COIL_TIGHT_MULT  = 0.5                  # Intraday 15-min execution gate  (0.5 × ATR14)
EMA_COIL_DAILY_MULT  = 0.75                 # EOD daily scanner "professional boundary" (0.75 × ATR14)
COIL_SIDEWAYS_ATR_MULT = 2.0                # 2x ATR = bars overlapping but not expanding
EMA_ENTRY_PERIOD     = 20                   # 20 EMA on 5-min chart = entry trigger
COIL_SIDEWAYS_BARS   = 3                    # Min consecutive sideways 15-min bars
TWO_SIDED_PIPS       = 15                   # ACB failure threshold post-entry
TWO_SIDED_CANDLES    = 2                    # Candles stalling = exit signal

# ── CIRCUIT BREAKERS ─────────────────────────────────────────────────────────
DAILY_LOSS_HALT_PCT  = 0.02
WEEKLY_DD_HALT_PCT   = 0.05
CONSEC_LOSS_HALT     = 3
BREAKEVEN_PIPS       = 30                   # Pips profit before moving stop to BE
TRAIL_STEP_PIPS      = 20                   # Tranche C trail: step size in pips

# ── BACKTEST TRANSACTION COSTS ──────────────────────────────────────────────
BACKTEST_HALF_SPREAD_PIPS = 1.5             # Half-spread per fill (entry + exit)
BACKTEST_SLIPPAGE_PIPS    = 0.5             # Execution slippage per fill

# ── BACKTEST INTRADAY STOP SIMULATION ────────────────────────────────────────
# In live trading the 15-min EMA coil gives tight 15–20 pip stops (currencies).
# The backtester only has daily bars, so stops are set from daily high/low + 2 pips
# (typically 40–80 pips for currencies).  This inflates risk_pips, deflates R:R,
# and blocks the tight_stop (+2) and rr_3to1 (+2) scoring bonuses.
# When a setup's daily-bar stop exceeds 1.5× the simulated value, override it to
# model what a live coil entry would achieve.  Values per instrument class:
BACKTEST_SIMULATED_STOP_PIPS = {
    "CURRENCIES": 20,    # Live coil stops: 15–25 pips
    "GOLD":       80,    # Live coil stops: 50–100 pips
    "OIL":        80,    # Live coil stops: 50–100 pips
    "INDEXES":    150,   # Live coil stops: 100–200 pips
}

# ── BACKTEST NEWS FIRST-BOUNCE ───────────────────────────────────────────────
# MRN events (NFP, CPI, FOMC) can delay entry by 1 session. In live trading the
# news_rearm module waits for the post-news settle, then enters on the first
# bounce.  In backtesting we simulate this by extending the fill window by N bars
# beyond entry_date — if the limit order isn't filled on entry_date, it survives
# one more bar before expiring.  Set to 0 to disable.
BACKTEST_NEWS_LOOKAHEAD_BARS = 1

# ── COIL SIDEWAYS MULTIPLIER (DAILY) ─────────────────────────────────────────
# has_ema_coil_htf() requires the last 3 daily bars to be sideways (range ≤ N×ATR).
# The intraday multiplier (2.0) is correct for 15-min data, but daily bars have
# naturally wider ranges.  Using 2.0 on daily data blocks valid compressions where
# the EMAs have converged but the 3-bar range is 2.0–2.5× ATR.
# 2.5× admits genuine daily compressions while still rejecting expansion phases.
COIL_SIDEWAYS_ATR_MULT_DAILY = 2.5

# ── OPENING RANGE ────────────────────────────────────────────────────────────
MIN_OPENING_RANGE_PIPS = 40                 # Mon+Tue range < 40 pips = dead week
MIN_IB_RANGE_PIPS      = 20                # Intraday IB < 20 pips = skip session

# ── MONTHLY CYCLE ────────────────────────────────────────────────────────────
MONTHLY_RESET_DAYS     = 3                  # Trading days 1-3 = RESET phase
MONTHLY_FRONTSIDE_DAYS = 10                 # Days 4-10 = FRONTSIDE

# ── WATCHLIST ────────────────────────────────────────────────────────────────
WATCHLIST_MIN_CRITERIA = 1                  # Pairs meeting ≥1 criteria are scanned
ANCHOR_CONFLUENCE_PIPS = 50                 # Restored to 50 for USD Major precision (Final Calibration)

# ── POSITION STRUCTURE ───────────────────────────────────────────────────────
SESSION_TRADE_TRANCHES   = {"A": 1.0}              # 100% out at T1
FIVE_STAR_TRANCHES       = {"A": 0.50, "B": 0.30, "C": 0.20}

# ── NEWS SOURCE ──────────────────────────────────────────────────────────────
FOREXFACTORY_CALENDAR_URL = "https://www.forexfactory.com/calendar"
NEWS_BLOCK_WINDOW_HOURS   = 1               # Block 1 hour before + 3 hours after MRN
NEWS_SETTLE_MINUTES      = 30              # Post-MRN settle: no entry within 30 min of news print

# ── THREE-DAY RULE ───────────────────────────────────────────────────────────
# Playbook §Three Higher/Lower Closes: "minimum 3 consecutive closes in same
# direction" before a reversal signal (FRD/FGD) is valid.  Prior threshold of
# 2 was a compromise that stepped in front of the trend before the trap was
# built — guaranteed to chop equity.  This is the ONE constant that controls
# the prerequisite everywhere: weekly countdown, watchlist, PCD, FRD/FGD.
MIN_STREAK_DAYS          = 3               # Uncompromising 3-day structural minimum

# ── TRAP CONFIDENCE GATE (Mistake §2: "Diddling in the Middle") ──────────────
# TrapAnalysis.trap_confidence = "HIGH" (streak >= 3) | "MEDIUM" (streak == 2)
# | "LOW" (streak <= 1).  LOW confidence means the market has no directional
# conviction — volume is NOT pinned at extremes.  Forcing setups from noise
# is a 50/50 coin flip that destroys capital ("diddling for dollars").
# Trapped volume only fuels explosive ACB moves when it accumulates at
# Yesterday's H/L, HOW/LOW, HCOM/LCOM, or a Deathline at 00/50 levels.
MIN_TRAP_CONFIDENCE      = frozenset({"HIGH", "MEDIUM"})  # LOW = reject

# ── WEEKLY PHASE ENFORCEMENT ─────────────────────────────────────────────────
# Front Side (Mon → Wed before structural break): range expansion, trap building.
#   Only CONTINUATION scalps allowed — "Low Hanging Fruit", session-specific plays.
#   Taking reversals on the Front Side = stepping in front of a freight train.
# Back Side (Wed after signal → Fri): trend exhaustion, ACB liquidation.
#   Reversal patterns fire HERE — PCD, FRD/FGD, Parabolic.
#   This is where trapped breakout traders are forced to liquidate.
BACK_SIDE_PATTERNS = frozenset({
    "PUMP_COIL_DUMP",       # 3HC/3LC reversal — needs Back Side confirmation
    "FIRST_RED_DAY",        # Structural break — Back Side by definition
    "FIRST_GREEN_DAY",      # Structural break — Back Side by definition
    "PARABOLIC_REVERSAL",   # Major-level reversal — Back Side only
})
FRONT_SIDE_PATTERNS = frozenset()   # Empty: LHF now allowed all week (Back Side continuation after reversal)
# IFB, MFB, IB_EXTREME are structurally self-gating (DOW + setup logic)

# ── EMA COIL FORCE-PROMOTE ────────────────────────────────────────────────────
# Patterns that bypass MIN_SETUP_SCORE when a confirmed 15-min EMA coil is
# detected at the weekly extreme.  The coil IS the quality gate — potential
# energy fully loaded means the system executes, not re-scores.
COIL_FORCE_PROMOTE_PATTERNS = frozenset({
    "FIRST_RED_DAY",
    "FIRST_GREEN_DAY",
    "MONDAY_FALSE_BREAK",
})

# Patterns that fire through detection & scoring but are flagged [MONITOR ONLY] in Telegram.
# Single source of truth: acb_trader/signals/patterns.py (PatternDef.monitor_only=True).
# To gate/ungate a pattern: edit monitor_only in patterns.py only — this re-export keeps
# all existing imports (engine.py, setups.py, etc.) working unchanged.
from acb_trader.signals.patterns import MONITOR_ONLY_PATTERNS  # noqa: E402

# ── TELEGRAM ─────────────────────────────────────────────────────────────────
# Set via environment variables — never hardcode
# TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
# TELEGRAM_CHAT_ID   = os.environ["TELEGRAM_CHAT_ID"]

# ── PEPPERSTONE SYMBOL MAP ───────────────────────────────────────────────────
# Internal name → Pepperstone MT5 symbol name.
# Verify index/oil symbols in MT5 under View → Symbols → CFDs.
SYMBOL_MAP = {
    # FX — USD Majors
    "EURUSD": "EURUSD",
    "GBPUSD": "GBPUSD",
    "USDJPY": "USDJPY",
    "USDCHF": "USDCHF",
    "USDCAD": "USDCAD",
    "AUDUSD": "AUDUSD",
    "NZDUSD": "NZDUSD",
    # FX — Crosses
    "GBPJPY": "GBPJPY",
    "GBPAUD": "GBPAUD",
    "GBPCAD": "GBPCAD",
    "GBPCHF": "GBPCHF",
    "GBPNZD": "GBPNZD",
    "EURJPY": "EURJPY",
    "AUDJPY": "AUDJPY",
    "CADJPY": "CADJPY",
    "NZDJPY": "NZDJPY",
    "CHFJPY": "CHFJPY",
    # Metals
    "XAUUSD": "XAUUSD",        # Gold vs USD — standard across all brokers
    # Oil  (Pepperstone: check View → Symbols → CFDs → Energy)
    "USOIL":  "XTIUSD",        # WTI Crude — Pepperstone uses XTIUSD
    "UKOIL":  "XBRUSD",        # Brent Crude — Pepperstone uses XBRUSD
    # Indices  (Pepperstone: check View → Symbols → CFDs → Indices)
    "SP500":  "US500",         # S&P 500 — Pepperstone uses US500
    "NAS100": "US100",         # Nasdaq 100 — Pepperstone uses US100
    "DJ30":   "US30",          # Dow Jones — Pepperstone uses US30
}
