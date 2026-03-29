# agent.md

## The High-Performance Trader Agent

**Version:** 2.1
**Framework:** Stacey Burke Trading — ACB Scalable Trading Setups (2025) + Best Trade Setups Playbook (2022)
**Updated:** 2026-03-28

---

## Identity & Philosophy

This agent is a rule-based automated trading system built on the Stacey Burke ACB (Ain't Coming Back) methodology. It does not improvise. It does not override its rules based on news, emotion, or market commentary. It executes one clearly defined process, repeatedly, with discipline.

**Core truth the agent operates from:**
> All markets only do three things: break out, continue to trend, or stay in a trading range.

The agent's entire job is to identify which of those three states a market is in, determine whether a high-probability setup exists within that state, and execute it with asymmetrical risk/reward. Nothing else.

---

## Skill Dependencies

The agent is composed of five skill modules executed in strict order at NY close (4:59 PM ET) daily:

```
EOD Run Order:
  0. GUARD: Confirm NY close (4:59 PM ET) has passed — no in-progress candles evaluated
  1. skill_market_classification.md    → Classify pairs: BREAKOUT | TRENDING | RANGING
                                         Compute: CIB flag, HOM/LOM, HCOM/LCOM, HOD/LOD
                                         Filter:  Daily Watchlist (6-criteria check)
  2. skill_weekly_template_mapping.md  → Map weekly + monthly cycle
                                         Compute: FDTM anchors, monthly phase (FRONTSIDE/BACKSIDE)
                                         Track:   3HC/3LC countdown, signal day detection
  3. skill_setup_identification.md     → Detect valid setups, score them, output Setup objects
                                         Confirm: EMA Coil on higher TF → upgrade to 5-STAR
  4. skill_session_execution.md        → Place orders, manage trades, enforce circuit breakers
                                         Classify: SESSION_TRADE vs 5-STAR_SCALABLE tier
  5. skill_psychology_guardrails.md    → System health check, pre-trade checklist, discard log
```

No skill is skipped. If Skill 1 returns RANGING for all pairs in a basket, the run stops there — no setups are hunted, no orders placed.

---

## Primary Directives

### 1. Never Force a Trade

If no setup scores ≥ 5 across the full basket, the agent outputs: `NO_TRADE_TODAY` and halts. Sitting on your hands IS a position. The agent does not manufacture setups to fill time.

### 2. One Setup With Size Over Many Small Trades

The agent trades one setup per basket per day maximum. It does not scatter across 10 pairs simultaneously. A single high-conviction setup executed with proper size is the business model.

### 3. Instrument Agnosticism

The agent has no favourite pair. It scans all pairs in all configured baskets and trades whichever offers the cleanest template and highest score. Attachment to a specific instrument is a human flaw — not a system flaw.

### 4. Asymmetrical Risk/Reward Only

The agent never enters a trade where Target 1 (100% measured move) is less than 50 pips away, or where the stop is wider than 1.25 × ATR14. Every trade risked must be capable of returning at least 2R at Target 1.

### 5. The ACB Standard

If the trade is not the type that moves immediately and strongly in the entry direction — it is not an ACB trade. The agent monitors for two-sided price behaviour post-entry and exits without hesitation.

---

## System Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                    DATA LAYER                           │
│  Broker feed / Dukascopy / MT5 Python API               │
│  Daily OHLCV (NY close) + 1-min intraday per session    │
└────────────────────────┬────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────┐
│                  SIGNAL LAYER (EOD, 5 PM ET)            │
│  skill_market_classification → MarketState[]            │
│  skill_weekly_template_mapping → WeeklyTemplate[]       │
│  skill_setup_identification → Setup[]                   │
└────────────────────────┬────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────┐
│              AI CLASSIFICATION LAYER (optional)         │
│  One Claude API call per EOD run                        │
│  Input: top-ranked Setup objects + WeeklyTemplate       │
│  Output: natural language briefing + validation flag    │
│  Cost: ~$0.01–0.05 per day                             │
└────────────────────────┬────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────┐
│                EXECUTION LAYER (Intraday)               │
│  skill_session_execution → Orders via MT5/cTrader API   │
│  15-min candle trigger within equity hour window        │
│  Quarter level snapping, IB computation, ACB filter     │
│  State machine: PENDING → ACTIVE → PARTIAL → CLOSED     │
└────────────────────────┬────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────┐
│              MONITORING & ALERTS                        │
│  Telegram: EOD briefing + state transitions             │
│  Sentry: errors, connectivity failures                  │
│  Database: TradeRecord on every terminal state          │
│  Daily P&L email: automated EOD summary                 │
└─────────────────────────────────────────────────────────┘
```

---

## Instrument Baskets

The agent scans these baskets in priority order. Trade the top-ranked pair per basket only.

```python
BASKETS = {
    "USD_MAJORS":   ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD"],
    "GBP_CROSSES":  ["GBPJPY", "GBPAUD", "GBPCAD", "GBPCHF", "GBPNZD"],
    "JPY_CROSSES":  ["EURJPY", "AUDJPY", "CADJPY", "NZDJPY", "CHFJPY"],
    "COMMODITY":    ["XAUUSD", "USOIL", "AUDUSD", "USDCAD"],
}
```

---

## AI Layer Usage

The AI (Claude API) is called **once per EOD run** after the signal layer completes. It receives a structured JSON payload and returns a natural language briefing plus a binary validation flag.

```python
AI_PROMPT_TEMPLATE = """
You are a senior trading analyst reviewing setups generated by an automated rule-based system
using the Stacey Burke ACB methodology.

Today's date: {date}
Day of week: {day}
Week template type: {template_type}
Top setup: {setup_json}
Weekly anchors: {anchors_json}

Your tasks:
1. Confirm the setup aligns with the weekly template (yes/no + one sentence reason)
2. Flag any obvious conflict (e.g. setup is LONG but high is locked)
3. Write the Telegram briefing message using the format in skill_weekly_template_mapping.md
4. Output confidence: HIGH | MEDIUM | LOW

Respond in JSON only. Schema: {validation_schema}
"""

MAX_TOKENS = 500        # Briefing is short — no need for more
MODEL = "claude-sonnet-4-20250514"
ESTIMATED_COST_PER_DAY = "$0.01–0.05"
```

The AI does **not** place trades. It does not override circuit breakers. It does not have access to the execution layer. It is a validation and communication tool only.

---

## Configuration

```python
# config.py
RISK_PER_TRADE_PCT   = 0.01          # 1% account risk per trade
MIN_IB_RANGE_PIPS    = 20            # Minimum initial balance range
MIN_TARGET_PIPS      = 50            # Minimum distance to Target 1
MAX_STOP_ATR_MULT    = 1.25          # Maximum stop as ATR multiple
MIN_SETUP_SCORE      = 7             # Minimum score to arm an entry (optimizer sweep confirmed: score=7 → +0.10R/trade)
FIVE_STAR_SCORE  = 9             # Score threshold for full tranche structure
ATR_PERIOD           = 14            # ATR lookback
BREAKEVEN_PIPS       = 30            # Pips profit before moving stop to BE
TWO_SIDED_PIPS       = 15            # ACB failure threshold
TWO_SIDED_CANDLES    = 2             # Number of 15-min candles to confirm stall
DAILY_LOSS_HALT_PCT  = 0.02          # 2% daily loss → halt entries
WEEKLY_DD_HALT_PCT   = 0.05          # 5% weekly drawdown → halt until Monday
CONSEC_LOSS_HALT     = 3             # 3 consecutive losses → 24hr pause
# Session open times (ET)
ASIA_OPEN_HOUR       = 19            # 7:00 PM ET
LONDON_OPEN_HOUR     = 1             # 1:00 AM ET
LONDON_EQUITY_HOUR   = 3             # 3:00 AM ET (primary equity hour)
NY_OPEN_HOUR         = 7             # 7:00 AM ET (FX)
NY_EQUITY_HOUR       = 9             # 9:30 AM ET (indexes only)
NY_CLOSE_HOUR        = 17            # 5:00 PM ET
NY_CLOSE_MINUTE      = 0             # Adjusted for 4:59 PM close
EOD_RUN_OFFSET_MIN   = 5             # Run EOD analysis 5 min after NY close
EMA_COIL_PERIODS     = [8, 21, 55, 100, 200]  # Multi-EMA periods for coil detection
EMA_COIL_TIGHT_MULT  = 0.5           # EMA spread ≤ 0.5 × ATR14 = coil confirmed
MONTHLY_FRONTSIDE_DAYS = 10          # Trading days 1–10 = FRONTSIDE
WATCHLIST_MIN_CRITERIA = 1           # Minimum watchlist criteria to scan a pair
# Patterns that fire through detection & scoring but are flagged [MONITOR ONLY] in Telegram.
# No live execution until sufficient backtest sample exists.
# Decision log: PARABOLIC_REVERSAL added 2026-03-28.
#   Backtest (2023-2024): 4 trades, 25% WR, -2.91R net. Architecturally limited —
#   PARA is a true intraday 15-min false-break pattern; EOD proxy unreliable.
MONITOR_ONLY_PATTERNS = {"PARABOLIC_REVERSAL"}
```

---

## Official Glossary (Burke 2022 Playbook)

All abbreviations used across skill files — canonical reference.

```
HOW    high of week              LOW    low of week
HOD    high of day               LOD    low of day
HOS    high of session           LOS    low of session
HOM    high of month (intraday)  LOM    low of month (intraday)
HCOM   highest close of month    LCOM   lowest close of month
HCOW   highest close of week     LCOW   lowest close of week

OR     opening range (Monday ONLY — single day high/low)
In B   initial balance (Monday + Tuesday combined — two day high/low)
FDTM   first day of trading month

MRN    major red news (ForexFactory red events)
BIS    break in structure
FB     false break
PFH    peak formation high
PFL    peak formation low
HB     high bull
LB     low bear
MR     major resistance
MS     major support
IB/ID  inside bar / inside day
PnD    pump and dump
DnP    dump and pump
LHF    low hanging fruit
CIB    closed in breakout
FRD    first red day
FGD    first green day
3HC    three higher closes
3LC    three lower closes
ACB    ain't coming back
BTS    best trade setup
SSB    self-sabotaging behaviour

Session colors (chart markup):
  Asia = CYAN | Europe/London = GOLD | New York = YELLOW
```

---

## Source Material

This agent was built from two primary sources, both read in full:

| Source | Edition | Pages | Key Contribution |
|--------|---------|-------|-----------------|
| *ACB Scalable Trading Setups* — Stacey Burke | 2025 | 49 | Core methodology, 3HC/3LC, EMA coil, HCOM/LCOM, FDTM, session structure |
| *Best Trade Setups Playbook* — Stacey Burke | 2022 | 155 | Full codeable criteria for all 6 patterns, scoring table, weekly template types, discard conditions |

**Direct quotes encoded in skill files:**

> *"I must EMPHASIZE again to all of you that these SETUPS are based on the CLOSING PRICE.
> The SETUPS will present AFTER the previous day has CLOSED, NOT WHILE IT'S TRADING."*
> — ACB Manual p.32 (enforced via `assert_eod_complete()` guard in `setups.py`)

> *"These SETUPS should ALL BE COILED into Higher Time Frame EMAs. The EXCEPTIONS will be
> a HIGH OF DAY or LOW OF DAY REVERSAL at an EXTREME."*
> — ACB Manual p.40 (EMA coil is mandatory for all patterns; PARA/HOD-LOD reversals are the
> documented exception, but are currently MONITOR_ONLY pending sufficient backtest data)

> *"HUNT THE SETUP — DON'T BE ATTACHED TO THE INSTRUMENT."*
> — ACB Manual p.15 (encoded in §Primary Directive 3: Instrument Agnosticism)

---

## What This Agent Is NOT

- It is not a scalping bot running on tick data
- It is not an HFT system — it makes 1–2 trades per week per basket
- It is not a black-box ML model — every decision is traceable to a rule in a skill file
- It is not fully autonomous — circuit breakers and daily review are required
- It does not guarantee profit — it executes a framework with historical edge, not a certainty

---

## Weekly Review Protocol

Every Friday at NY close, the agent generates a weekly report:

```
Week {N} Summary
─────────────────
Setups identified:     {n}
Setups executed:       {n}
Setups expired:        {n}
Win rate:              {pct}%
Average R-multiple:    {r}R
Total pips:            {pips}
Circuit breakers hit:  {n}
Best trade:            {pair} {pattern} +{pips} pips ({r}R)
Worst trade:           {pair} {pattern} -{pips} pips (-1R)
Next week watch:       {pair} — {template_note}
```

This report feeds directly into the next week's configuration review. Adjust nothing mid-week. Review only on weekends.

---

## Changelog

### v2.1 — 2026-03-28 — Full Playbook Audit (both PDFs read in full)

**Original 4 alignment gaps fixed (engine.py + setups.py + config.py):**
1. EMA coil proxy wired into backtest engine (`has_ema_coil_htf()` → `ema_coil` arg in `detect_setups()`)
2. FGD +2 score bonus added (was missing, only FRD had it)
3. FGD special `floor=6` hardcode removed (now uses standard `MIN_SETUP_SCORE`)
4. PARA moved to `MONITOR_ONLY_PATTERNS` (4-trade sample insufficient; -2.91R net)

**9 playbook gaps fixed from PDF audit (`setups.py` + `config.py`):**

| # | Gap | Fix |
|---|-----|-----|
| 1 | FRD/FGD fired Mon/Tue (front-side noise) | Added Wed/Thu DOW gate → `FRD_FGD_WRONG_DOW` |
| 2 | IFB checked Day -1 range, not Day -2 (outer range) | `broke_high/low` and T1 now reference `prev` (Day -2) |
| 3 | PCD missing pump quality checks | Added: pump day range ≥ 0.75 ATR, net displacement ≥ 1.5 ATR, coil not new 5-day high/low |
| 4 | FRD missing net trend displacement | Added: trend leg ≥ 2.0 ATR → `FRD_FGD_TREND_TOO_SMALL` |
| 5 | FRD T2 was arbitrary ATR step | T2 = pre-trend close (full round-trip target per playbook) |
| 6 | Score cap was 14, playbook says 12 | `min(score, 12)` |
| 7 | EMA coil used 3 periods [9,20,50] | Updated to [8,21,55,100,200] — all TFs aligned |
| 8 | LHF missing prior-close quality check | Prior candle must close in top/bottom 20% of range |
| 9 | FRD trend days not quality-checked | Each trend bar close must be in upper/lower 40% of range |

**Backtest baselines (all confirmed on 2023-2024 data before audit):**
- 72 trades, 47.2% WR, +0.10R expectancy, PF 1.20, FORCE_CLOSE avg +1.26R
- Optimizer sweep: `MIN_SETUP_SCORE=7` confirmed optimal

### v2.0 — 2026 — Initial automated system build
