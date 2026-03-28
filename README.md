# ACB Trader

### Stacy Burke ACB (Ain't Coming Back) — Automated Trading System

EOD rule-based signal engine. Scans daily closes at 5:04 PM ET, identifies
high-probability setups from the ACB playbook, and sends Telegram alerts with
entry/stop/target levels. Execution uses MT5 Python API.

**What you get each trading day:**

- Runs at 5:04 PM ET Monday–Thursday (after New York close)
- Scans 4 baskets (27 instruments) across USD majors, GBP crosses, JPY crosses, and commodities
- Detects 6 active price-action patterns, scores them 0–14
- Sends a Telegram briefing with top templates and setup alerts (entry, stop, T1)
- Places MT5 limit orders, or logs paper orders if MT5 is unavailable

---

## Architecture

```
acb_trader/
├── config.py                   ← All constants — edit here only
├── data/
│   ├── feed.py                 ← MT5 broker data abstraction
│   ├── levels.py               ← HOD/LOD/HOW/LOW/HOS/LOS live tracking + ATR/pip utils
│   └── calendar.py             ← ForexFactory news feed
├── signals/
│   ├── classify.py             ← Market state: BREAKOUT | TRENDING | RANGING
│   ├── watchlist.py            ← 6-criteria daily watchlist filter (ACB p.14)
│   ├── weekly.py               ← Weekly + monthly template mapping
│   └── setups.py               ← 6 pattern detectors → scored Setup objects
├── execution/
│   ├── coil.py                 ← EMA coil detection (mandatory entry gate)
│   ├── sizing.py               ← 1% risk, Three Levels targets, quarter-snap
│   ├── orders.py               ← MT5 order placement abstraction
│   └── state_machine.py        ← PENDING → ACTIVE → PARTIAL → CLOSED
├── guards/
│   └── checklist.py            ← Pre-trade health check + circuit breakers
├── notifications/
│   └── telegram.py             ← EOD briefing + state change alerts
└── db/
    └── models.py               ← All dataclasses (Setup, MarketState, TradeRecord…)
```

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

> **Note:** `MetaTrader5` only installs on Windows. On Mac/Linux use the CSV
> fallback mode (the feed will print paper orders instead of calling MT5).

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your MT5 credentials and Telegram bot token
```

Each variable explained:

| Variable | Where to find it |
|----------|-----------------|
| `MT5_LOGIN` | Your numeric broker account ID |
| `MT5_PASSWORD` | Your MT5 account password |
| `MT5_SERVER` | In MT5 desktop: File → Open an Account (e.g. `ICMarketsSC-Demo`) |
| `TELEGRAM_BOT_TOKEN` | Create a bot via @BotFather on Telegram |
| `TELEGRAM_CHAT_ID` | Message @userinfobot on Telegram to get your chat ID |

> **Note:** `main.py` does not call `load_dotenv()` automatically. Either set these
> variables at the OS level (System Properties → Environment Variables on Windows),
> or add `from dotenv import load_dotenv; load_dotenv()` to the top of `main.py`.
> See [Known Limitations](#known-limitations).

### 3. Run a test scan right now

```bash
python main.py --now
```

This runs the full EOD pipeline immediately. Without MT5, `BrokerFeed` returns
mock data ($10,000 balance, synthetic OHLCV, 1.0 pip spread) and alerts print
to stdout instead of Telegram. To confirm MT5 is connecting, watch for
`[main] Broker connection failed` on startup.

### 4. Run on schedule (5:04 PM ET, Mon–Thu)

```bash
python main.py
```

The process runs a blocking `while True` loop. Keep it alive — see [Deployment](#deployment).
For production, run this on a Windows machine or VPS with MT5 installed.

---

## EOD Pipeline

```
5:04 PM ET
   │
   ├── [0] EOD guard — confirm NY close has passed
   ├── [0b] System health check — circuit breakers, account state
   │
   ├── For each basket (USD_MAJORS, GBP_CROSSES, JPY_CROSSES, COMMODITY):
   │     ├── [1] classify_market_state() → MarketState
   │     │       CIB flag, HOD/LOD/HOW/LOW/HOS/LOS/HOM/LOM/HCOM/LCOM
   │     ├── [2] evaluate_watchlist() → 6-criteria filter
   │     ├── [3] build_weekly_template() → FDTM, monthly phase, 3HC/3LC countdown
   │     ├── [4] has_ema_coil_htf() → EMA coil on 4H chart
   │     └── [5] detect_setups() → scored Setup objects (6 active patterns)
   │
   └── [6] Telegram: EOD briefing + arm each valid setup
```

---

## Six Patterns

| # | Pattern | Signal Day | Entry Day |
|---|---|---|---|
| 1 | **3HC/3LC → Pump Coil Dump** | 3 consecutive closes + coil candle | Next session — coil breakdown |
| 2 | **First Red Day / First Green Day** | First candle closing against trend (Wed/Thu) | Next session — EMA coil |
| 3 | **Inside False Break** | Inside day + false break of prior range | Next session |
| 4 | **Parabolic Reversal** | Daily close at HCOM/LCOM/HOM structural level | Next session — coil |
| 5 | **Monday False Break** | Monday breaks HOW/LOW and fails (FRD Tue–Thu) | Next session — coil |
| 6 | **Low Hanging Fruit** | Explosive prior session move | Next session 50% pullback |

> **Note:** All 6 patterns are now actively scanned. `_detect_low_hanging_fruit()` was added in the Skill 2.0 alignment.

---

## Entry Rule

**Signal Day (EOD)** → Setup detected, Telegram alert sent
**Entry Day (N+1)** → `wait_for_ema_coil()` monitors 15-min chart
**Entry Trigger** → 5-min candle closes through 20 EMA in trade direction

If no coil forms by session close on Entry Day → setup **EXPIRES**. No carry-forward.

---

## Trade Tiers

| Tier | Qualifies When | Structure | Hold |
|---|---|---|---|
| **SESSION TRADE** | Score 7–8, no EMA coil | 100% at Target 1, exit same session | Session only |
| **5-STAR SCALABLE** | Score ≥ 9 OR EMA coil confirmed | 50% T1 / 30% T2 / 20% trailer | Multi-session |

---

## Scoring System

Each detected setup is scored 0–14. Higher scores get the full tranche structure; anything below `MIN_SETUP_SCORE` (7) is discarded. This floor matches the 100-Lot Litmus Test minimum from Skill §8.

| Points | Criterion |
|--------|-----------|
| +2 | Signal day is Wednesday or Thursday |
| +2 | Market state is BREAKOUT |
| +2 | Stop ≤ 0.5× ATR14 (tight stop) |
| +2 | R:R ≥ 3:1 to Target 1 |
| +2 | Entry within 50 pips of 2+ anchor levels |
| +2 | EMA coil confirmed on 4H chart |
| +2 | Entry within 50 pips of HCOW or LCOW |
| +2 | Pattern is FIRST_RED_DAY |
| +2 | Pattern is PARABOLIC_REVERSAL at HCOW/LCOW (stacks with above row) |
| +2 | Pattern is MONDAY_FALSE_BREAK |
| +1 | Pattern is LOW_HANGING_FRUIT |
| +1 | Monthly phase is BACKSIDE |
| +1 | 3HC/3LC countdown label is SIGNAL_DAY |
| **14** | **Maximum (capped)** |

**EMA coil override:** If `has_ema_coil_htf()` returns `True`, the setup is promoted to FIVE_STAR_SCALABLE regardless of score.

**100-lot test:** `passes_100_lot_test()` in `guards/checklist.py` force-promotes to FIVE_STAR_SCALABLE when: score ≥ 7, template is REVERSAL_WEEK or NEW_MONTH_BREAKOUT, ≥2 anchor confluences within 50 pips, pattern is FIRST_RED_DAY / PUMP_COIL_DUMP / PARABOLIC_REVERSAL / INSIDE_FALSE_BREAK / MONDAY_FALSE_BREAK, and entry is at HCOW/LCOW or EMA coil confirmed.

---

## Reading Your Telegram Alerts

### EOD Briefing (~5:04 PM ET)

Sent once per day. Shows top-5 templates and top-3 setups.

```
📊 ACB EOD BRIEFING — Thu 27 Mar 2025 17:05 ET

📉 GBPUSD — REVERSAL_WEEK
   Phase: BACKSIDE | 3HC/3LC: SIGNAL_DAY
   High locked: ✅ | Low locked: ❌

⏸ NO SETUPS TODAY — sit on hands
```

- `High locked: ✅` — only SHORT setups are valid this week (long setups filtered)
- `Phase: FRONTSIDE` — early weekly cycle; trend extension expected
- `Phase: BACKSIDE` — late cycle; reversals more probable
- `3HC/3LC: SIGNAL_DAY` — the countdown is complete, pattern is ready to fire

### SETUP ARMED Alert

Sent for every valid setup found at EOD.

```
🔔 SETUP ARMED — EURUSD
Pattern:   FIRST_RED_DAY
Tier:      ⭐⭐⭐⭐⭐ 5-STAR
Direction: SHORT
Entry:     1.08500
Stop:      1.08750
T1:        1.08000
Score:     11/14
Expires:   2025-03-28
```

`Expires` = the entry_date. If no EMA coil forms by that session's close, the setup is cancelled automatically.

### Trade Lifecycle Alerts

Sent each time the trade changes state.

| Icon | State | Meaning |
|------|-------|---------|
| `✅` | ACTIVE | Limit order filled — trade is live |
| `💰` | PARTIAL_EXIT | T1 hit, Tranche A closed, stop moved to breakeven |
| `⚖️` | BREAKEVEN_CLOSE | Stopped out at entry price — no loss |
| `❌` | STOPPED_OUT | Full stop hit |
| `🏆` | FULL_TARGET_CLOSE | All tranches hit their targets |
| `🎯` | TRAIL_CLOSE | Trailing stop closed Tranche C |
| `🚪` | FORCE_CLOSE | Manual or time-based exit |
| `⏰` | EXPIRED | EMA coil never formed; setup lapsed |

After a trade closes you also receive a **DEBRIEF** with the final R multiple, pip result, and terminal state.

### Health & Circuit Breaker Alerts

Sent before every EOD run if any check fails.

```
🏥 SYSTEM HEALTH: ❌ FAILED
  ❌ BROKER_DISCONNECTED
  ⚠️ WIDE_SPREAD: EURUSD 4.5 pips (3.0× normal)
```

| Code | Meaning | Action |
|------|---------|--------|
| `BROKER_DISCONNECTED` | MT5 session expired | Restart MT5 and re-run the script |
| `DAILY_LOSS_HALT` | 2%+ loss today | No entries until tomorrow (currently inactive — see Known Limitations) |
| `WEEKLY_DD_HALT` | 5%+ drawdown from Monday open | No entries until Monday (currently inactive) |
| `CONSEC_LOSS_HALT` | 3 consecutive losses | Review manually (currently inactive) |
| `FRIDAY_NO_ENTRY` | Today is Friday | Expected — exit-only day |
| `BEFORE_NY_CLOSE` | Run fired too early | Wait for 5:04 PM ET |
| `WIDE_SPREAD: PAIR` | Spread > 3× normal | Warning only; run continues |
| `STALE_DATA` | MT5 feed not updating | Check broker connection |
| `UNREALISED_LOSS` | >1.5% of balance in open drawdown | Warning only |

---

## Configuration

All tuneable constants are in `acb_trader/config.py`. **Never hardcode values in other modules.**

### Risk

| Constant | Default | Notes |
|----------|---------|-------|
| `RISK_PER_TRADE_PCT` | `0.01` | 1% account risk per trade — never change |
| `MIN_TARGET_PIPS` | `50` | Minimum distance to T1 for any instrument |
| `MIN_SETUP_SCORE` | `7` | 100-Lot Litmus Test floor (Skill §8) |
| `FIVE_STAR_SCORE` | `9` | Score threshold for full tranche structure |

### Stops (max pips by instrument class)

These are **EOD daily-bar stop distances** (high/low of the signal day + 2 pips buffer).
They are wider than intraday entries by design — a 5-min EMA coil entry typically needs only 15–20 pips of stop, but the EOD pending limit order must accommodate daily bar structure.

| Class | Min pips | Max pips |
|-------|----------|----------|
| CURRENCIES | 15 | 80 |
| GOLD | 50 | 300 |
| OIL | 50 | 300 |
| INDEXES | 100 | 500 |

### Three Levels — pip targets by instrument class

| Class | T1 | T2 | T3 / Extension |
|-------|----|----|----------------|
| CURRENCIES | 75 | 150 | 250 (L3_EXT: 300) |
| GOLD | 150 | 250 | — (L3_EXT: 300) |
| OIL | 150 | 250 | 300 / 500 / 750 |
| INDEXES | 250 | 500 | 750 |

### Instrument Baskets

| Basket | Pairs |
|--------|-------|
| `USD_MAJORS` | EURUSD, GBPUSD, USDJPY, USDCHF, USDCAD, AUDUSD, NZDUSD |
| `GBP_CROSSES` | GBPJPY, GBPAUD, GBPCAD, GBPCHF, GBPNZD |
| `JPY_CROSSES` | EURJPY, AUDJPY, CADJPY, NZDJPY, CHFJPY |
| `COMMODITY` | XAUUSD, USOIL, AUDUSD, USDCAD |

### Session Windows (all times ET)

| Session | Open | Close |
|---------|------|-------|
| ASIA | 7:00 PM | 11:00 PM |
| LONDON | 1:00 AM | 5:00 AM |
| NEW_YORK_FX | 7:00 AM | 11:00 AM |
| NEW_YORK_EQ | 9:30 AM | 11:00 AM |

### EMA Coil Parameters

| Constant | Default | Meaning |
|----------|---------|---------|
| `EMA_COIL_PERIODS` | `[9, 20, 50]` | EMAs monitored for convergence |
| `EMA_COIL_TIGHT_MULT` | `0.5` | EMA spread ≤ 0.5× ATR14 = coil tight |
| `EMA_ENTRY_PERIOD` | `20` | 5-min 20 EMA used for entry trigger |
| `COIL_SIDEWAYS_BARS` | `3` | Min consecutive sideways 15-min bars |
| `TWO_SIDED_PIPS` | `15` | Stall threshold — exit if price oscillates ≤15 pips |
| `TWO_SIDED_CANDLES` | `2` | Candles stalling before ACB-failure exit |

### Circuit Breakers *(currently inactive — see Known Limitations)*

| Constant | Default |
|----------|---------|
| `DAILY_LOSS_HALT_PCT` | `0.02` (2%) |
| `WEEKLY_DD_HALT_PCT` | `0.05` (5%) |
| `CONSEC_LOSS_HALT` | `3` |
| `BREAKEVEN_PIPS` | `30` |

### Position Structure

| Tier | Tranche A | Tranche B | Tranche C |
|------|-----------|-----------|-----------|
| SESSION_TRADE | 100% at T1 | — | — |
| FIVE_STAR_SCALABLE | 50% at T1 | 30% at T2 | 20% trail |

---

## Module Reference

| Module | Purpose | Primary API |
|--------|---------|-------------|
| `main.py` | Scheduler + pipeline orchestrator | `run_eod(feed)`, `start_scheduler(feed)` |
| `acb_trader/config.py` | All constants — single source of truth | Imported everywhere; edit here only |
| `acb_trader/db/models.py` | All dataclasses | `Setup`, `MarketState`, `TradeRecord`, `AccountState`, `WeeklyTemplate`, `SystemHealthResult` |
| `acb_trader/data/feed.py` | MT5 data abstraction | `BrokerFeed.get_daily_ohlcv()`, `get_1min_today()`, `get_account()`, `get_spread()`, `is_connected()` |
| `acb_trader/data/levels.py` | Price math utilities | `compute_atr()`, `close_streak_count()`, `get_pip_size()`, `snap_to_quarter()`, `price_to_pips()` |
| `acb_trader/data/calendar.py` | ForexFactory news events | `fetch_calendar()`, `is_news_blocked()` — **imported but not wired into pipeline** |
| `acb_trader/signals/classify.py` | Classifies each pair as BREAKOUT / TRENDING / RANGING | `classify_market_state()` → `MarketState`; `rank_basket()` |
| `acb_trader/signals/watchlist.py` | 6-criteria ACB watchlist filter | `evaluate_watchlist()` — any 1 criterion keeps the pair in scan |
| `acb_trader/signals/weekly.py` | Weekly + monthly template | `build_weekly_template()` → `WeeklyTemplate` with anchors, 3HC/3LC, day role, monthly phase |
| `acb_trader/signals/setups.py` | 6 pattern detectors + scoring | `detect_setups()` → `(list[Setup], list[DiscardedSetup])`; `assert_eod_complete()` |
| `acb_trader/execution/coil.py` | EMA coil detection + entry trigger | `has_ema_coil_htf()` (EOD score bonus), `wait_for_ema_coil()` (live 15-min), `check_5min_entry()`, `is_two_sided()` |
| `acb_trader/execution/sizing.py` | Position size + target levels | `calculate_position_size()`, `get_three_levels_targets()`, `get_tranches()` |
| `acb_trader/execution/orders.py` | MT5 order placement | `MT5Client.place_limit_order()`, `modify_stop()`, `close_position()`, `cancel_pending()` |
| `acb_trader/execution/state_machine.py` | Trade lifecycle management | `ActiveTrade`: PENDING_ENTRY → ACTIVE → PARTIAL_EXIT → terminal |
| `acb_trader/guards/checklist.py` | Pre-trade health check | `run_pre_trade_checklist()`, `passes_100_lot_test()`, `is_diddle()` |
| `acb_trader/notifications/telegram.py` | All Telegram messaging | `send_eod_briefing()`, `send_setup_armed()`, `send_state_change()`, `send_trade_debrief()`, `send_circuit_breaker()` |

---

## Deployment

**Platform requirement:** The MT5 Python API is Windows-only. Production must run on a Windows machine or Windows cloud VM (e.g. AWS EC2 Windows). Mac/Linux will run in paper mode only.

**Python 3.11+ required.**

### Option A — Windows Task Scheduler (simplest)

1. Open Task Scheduler → Create Basic Task
2. Trigger: At startup (or daily; the script self-schedules internally)
3. Action: Start a program → `python.exe`, argument: `C:\path\to\stacy_burke_trading\main.py`
4. Check "Run whether user is logged on or not"
5. Also schedule MT5 to auto-start before the script

### Option B — NSSM (auto-restart on crash)

[NSSM](https://nssm.cc) wraps the process as a Windows service:

```
nssm install acb-trader "C:\Python311\python.exe" "C:\path\main.py"
nssm start acb-trader
```

### Keeping MT5 alive

MT5 must be running and logged in. If it auto-disconnects:

- The `BrokerFeed` falls back to paper mode silently
- A `🛑 BROKER_DISCONNECTED` Telegram alert fires and the EOD run aborts

To prevent this: set MT5 → Tools → Options → Server → "Keep connection" and disable auto-logout in your broker portal.

---

## Known Limitations

| # | Issue | Location | Status |
|---|-------|----------|--------|
| 1 | Circuit breakers were stubbed to `0.0` | `db/session_tracker.py` | ✅ Fixed — `compute_account_metrics()` reads/writes `session_state.json` to track daily P&L, weekly drawdown, and consecutive losses |
| 2 | `is_news_blocked()` was imported but never called | `main.py` | ✅ Fixed — setups with high-impact news within 1h of entry are now skipped before arming |
| 3 | `LOW_HANGING_FRUIT` had no detector function | `signals/setups.py` | ✅ Fixed — `_detect_low_hanging_fruit()` added; all 6 patterns are now actively scanned |
| 4 | `load_dotenv()` is not called | `main.py` | ⚠️ Open — `.env` is not auto-loaded; env vars must be set at the OS level, or manually add `from dotenv import load_dotenv; load_dotenv()` to `main.py` |
| 5 | Flat files in repo root don't match package layout | repo root | ⚠️ Open — `from acb_trader.*` imports require files to be organised into the `acb_trader/` package tree shown in Architecture |

---

## Extending the System

### Swapping MT5 for cTrader

Replace `MT5Client` in `execution/orders.py` with a `cTraderClient` subclass
that calls the cTrader OpenAPI. The rest of the pipeline is broker-agnostic.

Also subclass `BrokerFeed` in `data/feed.py` and override `get_daily_ohlcv()`,
`get_account()`, and `get_spread()` for the new data source.

### Adding a new instrument

1. Add the symbol string to `INSTRUMENT_CLASS` in `config.py` with the right class (`CURRENCIES`, `GOLD`, `OIL`, or `INDEXES`)
2. Add it to the appropriate basket in `BASKETS`
3. Confirm the MT5 symbol name matches exactly (broker-specific — check MT5's Market Watch)

### Adding a new pattern (Pattern 7+)

1. Add a `_detect_<name>()` function in `signals/setups.py` with this signature:

   ```python
   def _detect_mypattern(pair, state, template, ohlcv, atr14) -> Optional[tuple[Optional[Setup], str]]:
   ```

2. Register it in the `detectors` list inside `detect_setups()`
3. Add a score line in `_score()` if it warrants a bonus
4. Update the Six Patterns table above

### Upgrading the database from JSON to SQLite

The circuit breakers currently persist to `session_state.json` via `db/session_tracker.py`.
For multi-year trade history and richer reporting, upgrade to SQLite:

1. Add `sqlalchemy` to `requirements.txt`
2. Create a `db/repository.py` with `save_trade(record: TradeRecord)` and `load_trades(since: date) -> list[TradeRecord]`
3. In `db/session_tracker.py`, replace the JSON read/write with repository calls
4. `compute_account_metrics()` signature stays the same — no upstream changes needed

---

## Skill 2.0 Alignment Notes

These decisions were made during the 2023–2024 backtest calibration against the Stacy Burke Professional Skill 2.0 playbook. **Do not revert them without re-running the full backtest.**

### 1 — Diddle filter bypass for RANGING signal days

`detect_setups()` normally skips pairs in `RANGING` state (the "diddle" filter — don't trade noise). The exception: if `classify_market_state()` labelled the substate as `FIRST_RED_DAY_SIGNAL`, `FIRST_GREEN_DAY_SIGNAL`, or `INSIDE_DAY`, the pair **is** scanned. These substates mean a high-quality reversal signal fired on what is technically a ranging day — exactly the Burke playbook setup.

### 2 — R:R floor for FRD / FGD

The global R:R minimum is 2:1. For `FIRST_RED_DAY` and `FIRST_GREEN_DAY`, the floor is **1:1**. Reason: the FRD/FGD target is 1.0 × ATR14, which often produces a 1–1.5R setup against an average daily bar stop. A 2:1 requirement was mathematically discarding ~90% of valid FRD/FGD setups during backtesting. These patterns still require `MIN_SETUP_SCORE = 7` — the R:R relaxation only affects the geometric filter, not the scoring gate.

### 3 — Anchor confluence radius

`ANCHOR_CONFLUENCE_PIPS = 50`. The original value of 25 pips was calibrated for 5-min intraday entries. EOD daily-bar closes naturally settle further from precise structural levels; 50 pips reflects the "area of value" used in the Burke daily-bar playbook.

### 4 — FRD / FGD trend prerequisite (prior_streak ≥ 2)

`classify.py` and `setups.py` both require **2 prior consecutive closes** in the trend direction before a FRD/FGD fires. The Skill 2.0 manual requires "a minimum of 2–3 consecutive closes" before calling a reversal. A single prior close produced too many false signals in backtesting; 2 was the minimum that matched the manual's intent while keeping sample size viable.

### 5 — PCD coil threshold (0.75 × ATR14)

The EMA coil spread threshold for Pump Coil Dump was tightened from 0.5 to **0.75 × ATR14** (Skill §8, "professional boundary"). At 0.50, the filter was blocking every coil on a daily bar — daily compression naturally reads 0.6–0.9× ATR. The 0.75 threshold correctly admits daily coils while still rejecting wide-body expansion bars.

---

## Disclaimer

This system is for educational purposes. Past performance does not guarantee
future results. Trade at your own risk. Always test on a demo account first.
