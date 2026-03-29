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
MIN_SETUP_SCORE      = 7       # Optimizer-confirmed optimal threshold
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
EMA_COIL_TIGHT_MULT  = 0.3                  # Spread ≤ 0.3 × ATR14 = coil tight (tighter = more selective)
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
ANCHOR_CONFLUENCE_PIPS = 50                 # Restored to 50 for USD Major precision (Final Calibration)

# ── POSITION STRUCTURE ───────────────────────────────────────────────────────
SESSION_TRADE_TRANCHES   = {"A": 1.0}              # 100% out at T1
FIVE_STAR_TRANCHES       = {"A": 0.50, "B": 0.30, "C": 0.20}

# ── NEWS SOURCE ──────────────────────────────────────────────────────────────
FOREXFACTORY_CALENDAR_URL = "https://www.forexfactory.com/calendar"
NEWS_BLOCK_WINDOW_HOURS   = 1               # Block 1 hour before + 3 hours after MRN

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
