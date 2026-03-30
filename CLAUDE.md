# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run EOD pipeline immediately (testing, no scheduler)
python main.py --now

# Start production scheduler (fires at 5:04 PM ET Mon–Thu)
python main.py

# Run unit tests
pytest acb_trader/tests/ -v

# Run a single test file
pytest acb_trader/tests/test_patterns_and_scoring.py -v

# Run backtest on historical CSV
python -m acb_trader.backtest.run_backtest --data historical_2023_2024.csv
```

`MetaTrader5` is Windows-only. While `BrokerFeed` attempts to use it on Windows, it will also detect its absence or a failure to import and automatically fall back to paper mode (mock data, stdout alerts). On any other platform (Linux/macOS), the fallback to paper mode is also automatic.

## Architecture

The system is an end-of-day (EOD) automated trading bot implementing the Stacy Burke ACB methodology — a rule-based price-action playbook for FX, metals, oil, and indices.

### EOD Pipeline (triggered at 5:04 PM ET)

```text
DATA LAYER         MT5 broker or CSV fallback
      ↓
SIGNAL LAYER       classify → watchlist → weekly template → detect setups
      ↓
GUARDS             pre-trade checklist → circuit breakers → news blocking
      ↓
EXECUTION          position sizing → order placement → EMA coil gate → state machine
      ↓
NOTIFICATIONS      Telegram briefings, armed alerts, state changes, debriefs
```

### Key files

| File | Role |
| --- | --- |
| `main.py` | Entry point; `run_eod()` orchestrates the full pipeline |
| `acb_trader/config.py` | **Single source of truth** for all constants — never hardcode values in modules |
| `acb_trader/models.py` | All dataclasses: `Setup`, `MarketState`, `WeeklyTemplate`, `AccountState`, `TradeRecord` |
| `acb_trader/data/feed.py` | `BrokerFeed` — MT5 abstraction for OHLCV, account, spreads |
| `acb_trader/signals/classify.py` | `classify_market_state()` → BREAKOUT \| TRENDING \| RANGING |
| `acb_trader/signals/setups.py` | 6 pattern detectors → scored `Setup` objects |
| `acb_trader/signals/_scoring.py` | `score_setup()` — 0–14 point scoring with per-criterion breakdown |
| `acb_trader/execution/state_machine.py` | Trade lifecycle: PENDING_ENTRY → ACTIVE → PARTIAL_EXIT → CLOSED |
| `acb_trader/guards/checklist.py` | Pre-trade health checks + 100-lot promotion test |
| `acb_trader/backtest/engine.py` | State machine replay on historical OHLCV |

### Signal layer detail

`acb_trader/signals/` runs in this sequence per instrument:

1. **classify.py** — Computes market state and all structural levels (HOD/LOD/HOW/LOW/HOM/LOM/HCOM/LCOM). Must run after NY close on completed candles.
2. **watchlist.py** — 6-criteria filter; any 1 criterion keeps the pair in scan.
3. **weekly.py** — Maps FDTM, monthly phase (RESET/FRONTSIDE/BACKSIDE), and 3HC/3LC countdown.
4. **setups.py** — Detects 6 patterns (PCD, FRD, FGD, IFB, PARA, LHF), calls `_scoring.py`, filters at `MIN_SETUP_SCORE = 7`. Returns `(list[Setup], list[DiscardedSetup])`.

### Calibrated constants (do not revert without re-running backtest)

- `MIN_SETUP_SCORE = 7` — Optimizer confirmed (71 trades, 47.2% WR, +0.10R expectancy)
- `ANCHOR_CONFLUENCE_PIPS = 50` — Daily-bar "area of value" radius
- `EMA_COIL_DAILY_MULT = 0.75` — Professional boundary for daily-bar compression
- `FRD`/`FGD` prerequisite: `prior_streak ≥ 3` — Strict structural minimum
- `PARABOLIC_REVERSAL` — Monitor-only (25% WR, -2.91R net; disabled until 10+ live signals)

### Environment

Copy `.env.example` to `.env` and populate `MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`. Note: `main.py` does not call `load_dotenv()` — set variables at OS level or add that call manually.

### Persistent state files

- `session_state.json` — Daily P&L, weekly drawdown, consecutive loss count
- `paused_setups.json` — Setups blocked by market-relevant news; re-armed intraday via `news_rearm.py`
- `backtest_results.csv` — Backtest trade log
