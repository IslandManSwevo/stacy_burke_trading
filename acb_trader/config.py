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
FIVE_STAR_SCORE      = 9       # Score threshold for FIVE_STAR_SCALABLE tier
MIN_SETUP_SCORE      = 7       # Minimum score — aligned with 100-Lot Litmus Test floor (Skill §8)
ATR_PERIOD           = 14

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
BASKETS = {
    "USD_MAJORS":  ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD"],
    "GBP_CROSSES": ["GBPJPY", "GBPAUD", "GBPCAD", "GBPCHF", "GBPNZD"],
    "JPY_CROSSES": ["EURJPY", "AUDJPY", "CADJPY", "NZDJPY", "CHFJPY"],
    "COMMODITY":   ["XAUUSD", "USOIL", "AUDUSD", "USDCAD"],
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
EMA_COIL_PERIODS     = [9, 20, 50]          # EMAs used for coil detection
EMA_COIL_TIGHT_MULT  = 0.5                  # Spread ≤ 0.5 × ATR14 = coil tight
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

# ── OPENING RANGE ────────────────────────────────────────────────────────────
MIN_OPENING_RANGE_PIPS = 40                 # Mon+Tue range < 40 pips = dead week
MIN_IB_RANGE_PIPS      = 20                # Intraday IB < 20 pips = skip session

# ── MONTHLY CYCLE ────────────────────────────────────────────────────────────
MONTHLY_RESET_DAYS     = 3                  # Trading days 1-3 = RESET phase
MONTHLY_FRONTSIDE_DAYS = 10                 # Days 4-10 = FRONTSIDE

# ── WATCHLIST ────────────────────────────────────────────────────────────────
WATCHLIST_MIN_CRITERIA = 1                  # Pairs meeting ≥1 criteria are scanned
ANCHOR_CONFLUENCE_PIPS = 50                 # Entry must be within 50 pips of anchor (Daily Close sensitivity)

# ── POSITION STRUCTURE ───────────────────────────────────────────────────────
SESSION_TRADE_TRANCHES   = {"A": 1.0}              # 100% out at T1
FIVE_STAR_TRANCHES       = {"A": 0.50, "B": 0.30, "C": 0.20}

# ── NEWS SOURCE ──────────────────────────────────────────────────────────────
FOREXFACTORY_CALENDAR_URL = "https://www.forexfactory.com/calendar"
NEWS_BLOCK_WINDOW_HOURS   = 1               # Block 1 hour before + 3 hours after MRN

# ── PATTERN FLAGS ────────────────────────────────────────────────────────────
# Patterns listed here still fire through detection & scoring but are flagged
# [MONITOR ONLY] in Telegram alerts — no trade execution intended.
# Decision log: PARABOLIC_REVERSAL added 2026-03-28.
#   Backtest (2023-2024): 4 trades, 25% WR, -2.91R net. Streak relaxation rejected
#   (tested: 8/9 additional trades were losses, +7.7R outlier was USDJPY April 2024
#   yen carry event, not pattern edge). Proximity fix applied — still negative.
#   Keeping detector live to observe live price action before deciding to remove.
MONITOR_ONLY_PATTERNS: set[str] = {"PARABOLIC_REVERSAL"}

# ── TELEGRAM ─────────────────────────────────────────────────────────────────
# Set via environment variables — never hardcode
# TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
# TELEGRAM_CHAT_ID   = os.environ["TELEGRAM_CHAT_ID"]

# ── PEPPERSTONE SYMBOL MAP ───────────────────────────────────────────────────
SYMBOL_MAP = {
    "EURUSD": "EURUSD",
    "GBPUSD": "GBPUSD",
    "USDJPY": "USDJPY",
    "XAUUSD": "XAUUSD",
    "SP500":  "SP500",      # check — may be "US500"
    "NAS100": "NAS100",     # check — may be "USTEC"
    "DJ30":   "DJ30",       # check — may be "US30"
    "USOIL":  "USOIL",      # check — may be "XTIUSD"
}
