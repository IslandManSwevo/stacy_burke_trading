---
name: Stacy Burke Trading
description: Trading methodology based on Stacy Burke's ACB and Best Trade Setups Playbook.
---

# skill_market_classification.md

## Skill: Market Classification — The "Three Things" Analysis

**Version:** 2.0 — Codeable Definitions  
**Depends on:** Daily OHLCV data, ATR14

---

## Core Principle

All markets only do three things: **break out, continue to trend, or stay in a trading range.**  
This skill answers one question at EOD: *which state is this pair in right now?*  
The answer gates everything downstream — no setup is evaluated until classification passes.

---

## Input Requirements

```python
@dataclass
class ClassificationInput:
    pair: str
    daily_ohlcv: pd.DataFrame       # Minimum 20 rows, NY close (4:59 PM ET)
                                    # Columns: date, open, high, low, close, volume
    intraday_1min: pd.DataFrame     # 1-min bars for current day — for HOD/LOD/HOS/LOS
                                    # Columns: timestamp, open, high, low, close
    session_1min: pd.DataFrame      # 1-min bars since current session open — for HOS/LOS
    current_week: int               # ISO week number
    atr_period: int = 14
```

---

## Step 1 — Compute Base Indicators

These are computed once per pair per EOD run. All downstream logic references these values.

```python
# ATR (True Range method, Wilder smoothing)
atr14 = compute_atr(daily_ohlcv, period=14)

# Rolling 10-day high and low (the "box")
range_high_10 = daily_ohlcv['high'].rolling(10).max()
range_low_10  = daily_ohlcv['low'].rolling(10).min()
box_size      = range_high_10 - range_low_10

# ── NAMED STRUCTURAL LEVELS ───────────────────────────────────────────────────
# First-class variables updated live from intraday feed AND stored at EOD.
# Referenced directly by setup identification and execution layers.

# HOD / LOD — High and Low of the current trading DAY
hod = intraday_1min['high'].max()
lod = intraday_1min['low'].min()

# HOW / LOW — High and Low of the current trading WEEK (Mon–Fri rolling)
current_week_rows = daily_ohlcv[daily_ohlcv['date'].dt.isocalendar().week == current_week]
how           = current_week_rows['high'].max()
low_of_week   = current_week_rows['low'].min()  # avoid shadowing Python built-in `low`

# HOM / LOM — High and Low of the current MONTH (intraday extremes, NOT closes)
# *** Different from HCOM/LCOM which track the highest/lowest CLOSE ***
# HOM = highest HIGH of any candle this month | LOM = lowest LOW of any candle
current_month_rows = daily_ohlcv[daily_ohlcv['date'].dt.month == current_month]
hom = current_month_rows['high'].max()   # High of Month (intraday)
lom = current_month_rows['low'].min()    # Low of Month (intraday)
# HCOM / LCOM — Highest and Lowest CLOSING PRICE of the month (close-based)
hcom = current_month_rows['close'].max() # Highest Close of Month
lcom = current_month_rows['close'].min() # Lowest Close of Month

# HOS / LOS — High and Low of the current SESSION
# Resets at each session open. Updated bar-by-bar from 1-min intraday feed.
# session_1min = slice of 1-min bars from session open timestamp to current bar
hos = session_1min['high'].max()
los = session_1min['low'].min()

# ── DAY COUNTERS ──────────────────────────────────────────────────────────────

# close_streak: consecutive daily CLOSES in same direction (EOD state classification)
close_streak = compute_close_streak(daily_ohlcv['close'])
# +3 = 3 consecutive higher closes | -2 = 2 consecutive lower closes

# CIB (Closed In Breakout) — primary watchlist filter
# True if today's daily close broke beyond the prior 10-day range_high or range_low
# This is the first thing Burke looks for — a market that CLOSES IN BREAKOUT
cib_bullish = daily_ohlcv['close'].iloc[-1] > range_high_10.iloc[-2]  # close > prior 10d high
cib_bearish = daily_ohlcv['close'].iloc[-1] < range_low_10.iloc[-2]   # close < prior 10d low
cib         = cib_bullish or cib_bearish
cib_direction = "BULLISH" if cib_bullish else ("BEARISH" if cib_bearish else "NONE")

# day_break_counter: consecutive days that BROKE the prior day's HOD or LOD intraday
# *** Correct Day 1 / Day 2 / Day 3 counter per Burke's playbook ***
# Counts even if close does NOT confirm the break — the intraday H/L break is what matters
# +2 = two consecutive days that traded above the prior day's high intraday
# -2 = two consecutive days that traded below the prior day's low intraday
day_break_counter = compute_day_break_counter(daily_ohlcv)

# Range contraction ratio — detects compression / coiling
contraction_ratio = compute_atr(daily_ohlcv, period=3) / atr14
```

---

## Step 2 — Classify Market State

Evaluate in order. First match wins — states are mutually exclusive.

### State A: BREAKOUT

```
Conditions (ALL must be true):
  1. Today's close > range_high_10 of the prior 10 days (bullish breakout)
     OR today's close < range_low_10 of the prior 10 days (bearish breakout)

  2. Today's range (high - low) ≥ 1.0 × atr14
     → Breakout must be a meaningful range expansion, not a 2-pip poke

  3. Today's close is in the top 30% of today's range (bullish)
     OR bottom 30% of today's range (bearish)
     → Close must confirm direction, not give it back

  4. close_streak magnitude ≥ 1
     → At least today's candle closes in breakout direction
     (streak of 1 is sufficient — this IS the first breakout day)

  5. box_size ≤ 3.0 × atr14
     → The range being broken must be reasonably sized, not a 3-month consolidation
     (very wide boxes produce failed breakouts — skip)
```

**Substates (for scoring bonus in setup identification):**

- `BREAKOUT_DAY_1`: close_streak = 1 — first day out of the box
- `BREAKOUT_DAY_2`: close_streak = 2 — continuation, higher conviction
- `BREAKOUT_DAY_3+`: close_streak ≥ 3 — extended, watch for exhaustion

---

### State B: TRENDING

```
Conditions (ALL must be true):
  1. close_streak magnitude ≥ 2
     → At minimum 2 consecutive closes in the same direction

  2. Net move over streak ≥ 1.5 × atr14
     → Total displacement is meaningful, not just 2 tiny candles

  3. Today's close does NOT exceed range_high_10 / range_low_10
     → If it does, re-classify as BREAKOUT (evaluated first above)

  4. contraction_ratio ≥ 0.60
     → 3-day ATR is still at least 60% of 14-day ATR
     → If below 0.60, market is compressing → classify as RANGING instead
```

**Substates:**

- `TRENDING_FRONT_SIDE`: close_streak ≥ 2, day of week is Monday–Wednesday
- `TRENDING_BACK_SIDE`: close_streak ≥ 2, day of week is Thursday–Friday
  → Back side trending setups are First Red/Green Day candidates

---

### State C: RANGING

```
Conditions — classified as RANGING if NONE of the above match, OR:
  1. close_streak magnitude ≤ 1 AND
  2. contraction_ratio < 0.60 (compression) AND
  3. Today's close is between range_low_10 and range_high_10

RANGING = no setup evaluation. Output signal: "WAIT"
```

---

## Step 3 — Trap Identification (Supplementary Output)

When state is BREAKOUT or TRENDING, identify where trapped traders are likely positioned and where their stops cluster. This informs setup direction — the system fades trapped traders.

```python
@dataclass
class TrapAnalysis:
    trapped_side: str           # "LONGS_TRAPPED" | "SHORTS_TRAPPED" | "NONE"
    trap_level: float           # Price level where trapped traders entered
    stop_cluster: float         # Estimated stop location (just beyond trap_level)
    trap_confidence: str        # "HIGH" | "MEDIUM" | "LOW"
```

**Trap detection logic:**

```
LONGS_TRAPPED (look for short setups):
  - Market made a new N-day high in last 3 days (close_streak was +2 or +3)
  - AND current close is now BELOW that N-day high close
  - → Buyers who chased the breakout are now underwater
  - trap_level = highest close of the prior streak
  - stop_cluster = highest high of the prior streak + 2× spread

SHORTS_TRAPPED (look for long setups):
  - Market made a new N-day low in last 3 days
  - AND current close is now ABOVE that N-day low close
  - → Sellers who chased the breakdown are now underwater
  - trap_level = lowest close of the prior streak
  - stop_cluster = lowest low of the prior streak - 2× spread

trap_confidence:
  HIGH   → streak was ≥ 3 days (many traders committed to the move)
  MEDIUM → streak was 2 days
  LOW    → streak was 1 day or trap_level is outside 10-day box
```

---

## Step 4 — Basket Correlation Check

Never classify a pair in isolation. Compare across the correlated basket and flag the cleanest setup.

```python
def rank_basket(
    classifications: dict[str, MarketState],
    basket: list[str]
) -> list[str]:
    """
    Returns basket pairs ranked by setup clarity.
    Prioritizes: BREAKOUT > TRENDING_BACK_SIDE > TRENDING_FRONT_SIDE
    Deprioritizes: RANGING (excluded from output)
    Tiebreaker: highest close_streak magnitude wins
    """
```

**Standard baskets:**

| Basket | Pairs |
|---|---|
| USD majors | EURUSD, GBPUSD, USDJPY, USDCHF, USDCAD, AUDUSD, NZDUSD |
| GBP crosses | GBPUSD, GBPJPY, GBPAUD, GBPCAD, GBPCHF |
| JPY crosses | USDJPY, EURJPY, GBPJPY, AUDJPY, CADJPY |
| Commodity | XAUUSD, USOIL, AUDUSD, USDCAD |

Run basket ranking across all four at EOD. Trade only the **top-ranked pair per basket** — never two pairs from the same basket on the same day.

---

## Output Contract

```python
@dataclass
class MarketState:
    pair: str
    state: str                  # "BREAKOUT" | "TRENDING" | "RANGING"
    substate: str               # e.g. "BREAKOUT_DAY_2", "TRENDING_BACK_SIDE"
    direction: str              # "BULLISH" | "BEARISH" | "NEUTRAL"
    close_streak: int           # Consecutive closes in same direction (signed)
    day_break_counter: int      # Consecutive days breaking prior HOD/LOD (signed)
                                # This is the Day 1 / Day 2 / Day 3 value
    hod: float                  # High of Day (intraday)
    lod: float                  # Low of Day (intraday)
    how: float                  # High of Week (intraday)
    low_of_week: float          # Low of Week (intraday)
    hos: float                  # High of Session (intraday)
    los: float                  # Low of Session (intraday)
    hom: float                  # High of Month (intraday) — NOT same as HCOM
    lom: float                  # Low of Month (intraday)  — NOT same as LCOM
    hcom: float                 # Highest CLOSE of Month
    lcom: float                 # Lowest CLOSE of Month
    cib: bool                   # Closed In Breakout today — primary watchlist signal
    cib_direction: str          # "BULLISH" | "BEARISH" | "NONE"
    atr14: float
    contraction_ratio: float
    box_size: float
    trap: TrapAnalysis
    basket_rank: int            # 1 = cleanest in basket, 0 = excluded (RANGING)
    classified_at: datetime     # NY close timestamp
```

---

## Daily Watchlist Filter

Before any pair is evaluated for setups, it must pass the watchlist filter. This is the exact 6-criteria checklist from the playbook. Run at EOD against every pair in every basket.

```python
@dataclass
class WatchlistResult:
    pair: str
    on_watchlist: bool
    criteria_met: list[str]     # Which of the 6 criteria triggered
    priority: int               # 0–6, higher = more criteria met = higher priority scan

def evaluate_watchlist(state: MarketState, anchors: WeeklyAnchors) -> WatchlistResult:
    """
    Burke's exact 6 watchlist criteria (page 14 of ACB manual).
    A pair qualifies for setup scanning if ANY ONE criterion is met.
    Priority score = count of criteria met (more = better setup candidate).
    """
    criteria = []

    # 1. Previous MONTH closed in breakout
    if state.cib and is_month_boundary(state.classified_at):
        criteria.append("PREV_MONTH_CIB")

    # 2. Previous WEEK closed in breakout
    # Prior week's close was beyond the week-before's range
    if prior_week_closed_in_breakout(state.daily_ohlcv):
        criteria.append("PREV_WEEK_CIB")

    # 3. Previous DAY closed in breakout (CIB — most common trigger)
    if state.cib:
        criteria.append("PREV_DAY_CIB")

    # 4. Signal Days on daily chart (FRD, FGD, Inside Day detected at EOD)
    if state.substate in ["FIRST_RED_DAY_SIGNAL", "FIRST_GREEN_DAY_SIGNAL", "INSIDE_DAY"]:
        criteria.append("SIGNAL_DAY")

    # 5. New week break of WEEKLY or MONTHLY high/low levels (Monday trigger)
    if is_monday(state.classified_at):
        if state.how > anchors.prior_week_high or state.low_of_week < anchors.prior_week_low:
            criteria.append("NEW_WEEK_LEVEL_BREAK")
        if state.hom > anchors.prior_month_high or state.lom < anchors.prior_month_low:
            criteria.append("NEW_WEEK_MONTHLY_BREAK")

    # 6. HOD (Higher Highs) or LOD (Lower Lows) broken on Monday
    if is_monday(state.classified_at):
        if state.day_break_counter > 0:
            criteria.append("MONDAY_HOD_BREAK")
        elif state.day_break_counter < 0:
            criteria.append("MONDAY_LOD_BREAK")

    return WatchlistResult(
        pair         = state.pair,
        on_watchlist = len(criteria) > 0,
        criteria_met = criteria,
        priority     = len(criteria)
    )
```

**Key rule:** Monday's HOD/LOD break is a **signal to watch**, not a signal to trade. It flags the pair as "in play" for potential setups over the week. Do not trade the Monday break itself.

---

## Helper Function Signatures

```python
def compute_atr(ohlcv: pd.DataFrame, period: int) -> float:
    """Wilder smoothing ATR. Uses True Range = max(H-L, |H-Cprev|, |L-Cprev|)"""

def compute_close_streak(closes: pd.Series) -> int:
    """
    Count consecutive daily CLOSES in same direction from most recent backwards.
    Returns signed int: +3 = 3 higher closes, -2 = 2 lower closes, 0 = unchanged.
    Used for EOD state classification only.
    """

def compute_day_break_counter(ohlcv: pd.DataFrame) -> int:
    """
    Count consecutive days that BROKE the prior day's high or low intraday.
    This is the Day 1 / Day 2 / Day 3 counter per Burke's playbook.

    Logic:
      For each day (most recent first), check:
        - Did today's HIGH > yesterday's HIGH?  → bullish break, count +1
        - Did today's LOW  < yesterday's LOW?   → bearish break, count -1
        - If direction flips → stop counting

    Returns signed int:
      +1 = Day 1 (first day breaking prior HOD)
      +2 = Day 2 (second consecutive day breaking prior HOD)
      +3 = Day 3 — exhaustion / reversal zone
      Negative = same logic for LOD breaks
      0  = no break of prior day's range

    Note: A day CAN break both HOD and LOD (outside day).
    In that case, use the CLOSE direction to determine which break counts.
    """
    days = ohlcv.iloc[-5:].reset_index(drop=True)   # look back 5 days max
    counter = 0
    direction = None

    for i in range(len(days) - 1, 0, -1):
        today = days.iloc[i]
        yesterday = days.iloc[i - 1]
        broke_high = today['high'] > yesterday['high']
        broke_low  = today['low']  < yesterday['low']

        if broke_high and not broke_low:
            day_dir = 1
        elif broke_low and not broke_high:
            day_dir = -1
        elif broke_high and broke_low:
            # Outside day — use close direction
            day_dir = 1 if today['close'] > yesterday['close'] else -1
        else:
            break   # No break — streak ends

        if direction is None:
            direction = day_dir
        elif day_dir != direction:
            break   # Direction flipped — streak ends

        counter += direction

    return counter

def classify_market_state(input: ClassificationInput) -> MarketState:
    """Master function. Runs Steps 1–4 and returns MarketState."""

def rank_basket(classifications: dict[str, MarketState], basket: list[str]) -> list[str]:
    """Returns pairs sorted by setup quality. RANGING pairs excluded."""
```


# skill_psychology_guardrails.md

## Skill: Behavioral Circuit Breakers & Pre-Trade Checklist

**Version:** 2.0 — Automated Enforcement  
**Note:** In a human trader this skill governs psychology. In an automated system, psychology is replaced entirely by hard rules. Every guardrail below is a deterministic check — not a suggestion.

---

## Core Principle

The trader is the biggest variable in the markets. In this automated system, the "trader" is the configuration and the circuit breakers. Human interference with a running system IS the psychology problem. This skill exists to prevent that.

---

## Pre-Trade System Health Check

Runs **before** the EOD signal layer executes. If any check fails, the run aborts.

```python
@dataclass
class SystemHealthResult:
    passed: bool
    failures: list[str]
    warnings: list[str]
    timestamp: datetime

def run_pre_trade_checklist(account: AccountState, config: Config) -> SystemHealthResult:
    failures = []
    warnings = []

    # ── HARD FAILURES (abort entire EOD run) ─────────────────────────────────

    # 1. Broker connectivity
    if not broker.is_connected():
        failures.append("BROKER_DISCONNECTED")

    # 2. Daily loss circuit breaker
    if account.daily_pnl_pct <= -config.DAILY_LOSS_HALT_PCT:
        failures.append(f"DAILY_LOSS_HALT: {account.daily_pnl_pct:.2%} loss today")

    # 3. Weekly drawdown circuit breaker
    if account.weekly_drawdown_pct <= -config.WEEKLY_DD_HALT_PCT:
        failures.append(f"WEEKLY_DD_HALT: {account.weekly_drawdown_pct:.2%} from week open")

    # 4. Consecutive losses
    if account.consecutive_losses >= config.CONSEC_LOSS_HALT:
        failures.append(f"CONSEC_LOSS_HALT: {account.consecutive_losses} losses in a row")

    # 5. Entry date guard — only trade on Day N+1 (signal_date + 1 trading day)
    for pos in account.pending_entries:
        if date.today() > pos.entry_date:
            failures.append(f"ENTRY_DATE_EXPIRED: {pos.pair} signal was {pos.signal_date}, expired")

    # 6. Friday guard
    if datetime.now(ET).weekday() == 4:  # Friday
        failures.append("FRIDAY_NO_ENTRY: Exit day only")

    # 6. Open position in same basket
    for basket_name, pairs in BASKETS.items():
        open_in_basket = [p for p in account.open_positions if p.pair in pairs]
        if len(open_in_basket) >= 2:
            failures.append(f"BASKET_OVEREXPOSED: {basket_name} has {len(open_in_basket)} open")

    # ── WARNINGS (run continues, but flag in Telegram alert) ─────────────────

    # 7. Spread warning
    for pair in config.WATCHED_PAIRS:
        spread = broker.get_spread(pair)
        normal = config.NORMAL_SPREAD[pair]
        if spread > normal * 3:
            warnings.append(f"WIDE_SPREAD: {pair} spread {spread:.1f} pips (3× normal)")

    # 8. Account equity vs balance divergence (unrealised loss warning)
    unrealised_pct = (account.balance - account.equity) / account.balance
    if unrealised_pct > 0.015:
        warnings.append(f"UNREALISED_LOSS: {unrealised_pct:.2%} of balance in drawdown")

    # 9. Data staleness check
    if data_feed.last_update_age_minutes() > 10:
        warnings.append(f"STALE_DATA: Last feed update {data_feed.last_update_age_minutes()}m ago")

    return SystemHealthResult(
        passed=len(failures) == 0,
        failures=failures,
        warnings=warnings,
        timestamp=datetime.now(ET)
    )
```

---

## The Diddle Filter

The original framework warns against "science projects" — random market action that has no repeatable setup. In code, this is enforced at the classification layer: if `state == "RANGING"`, no setup is evaluated. But a secondary filter runs at the setup scoring stage.

```python
def is_diddle(setup: Setup, template: WeeklyTemplate) -> bool:
    """
    Returns True if this setup should be discarded as a "science project."
    A diddle is any trade that can't be clearly justified by the weekly template.
    """
    # Setup score too low to justify the risk
    if setup.score < config.MIN_SETUP_SCORE:
        return True

    # Setup direction conflicts with locked high/low
    if setup.direction == "LONG" and template.high_locked:
        return True
    if setup.direction == "SHORT" and template.low_locked:
        return True

    # Entry is not near any anchor level (floating in dead space)
    if not has_anchor_confluence(setup.entry_price, template.anchors, get_pip(setup.pair)):
        return True

    # Target 1 is less than minimum pips away (not worth the risk)
    target_distance = abs(setup.target_1 - setup.entry_price) / get_pip(setup.pair)
    if target_distance < config.MIN_TARGET_PIPS:
        return True

    # R:R is less than 2:1 at Target 1
    risk_pips = abs(setup.entry_price - setup.stop_price) / get_pip(setup.pair)
    if risk_pips == 0 or (target_distance / risk_pips) < 2.0:
        return True

    return False
```

If `is_diddle()` returns True → setup is discarded, logged as `DIDDLE_FILTERED`, and not passed to execution.

---

## Mid-Trade Interference Prevention

The most dangerous moment in automated trading is when a human overrides a running system mid-trade. This skill enforces a "hands off" protocol.

```python
PROTECTED_STATES = ["ACTIVE", "PARTIAL_EXIT", "PARTIAL_EXIT_2"]

def can_human_override(trade: ActiveTrade, reason: str) -> bool:
    """
    Human overrides are only permitted in these cases:
    1. Circuit breaker has been triggered (account-level emergency)
    2. Broker connectivity failure
    3. System has been manually halted via admin command

    Overrides for reasons like "I think the market will reverse" or
    "I want to move my stop closer" are REJECTED.
    """
    allowed_reasons = ["CIRCUIT_BREAKER", "BROKER_FAILURE", "ADMIN_HALT"]
    if reason not in allowed_reasons:
        log_override_attempt(trade, reason, approved=False)
        send_telegram(f"⚠️ OVERRIDE REJECTED: {reason} is not a valid override reason.")
        return False

    log_override_attempt(trade, reason, approved=True)
    return True
```

---

## The "100-Lot Litmus Test" (Setup Quality Gate)

Before any setup is executed, the system asks: *would a professional trade this setup with size?* This is encoded as a composite quality check.

```python
def passes_100_lot_test(setup: Setup, template: WeeklyTemplate) -> bool:
    """
    A setup passes the 100-lot litmus test when:
    - Score >= 7 (not just minimum threshold)
    - Template type is REVERSAL_WEEK or NEW_MONTH_BREAKOUT
    - High/low of the week is clearly locked in setup's favour
    - Stop is clean and tight (≤ 0.75 × ATR14)
    - Entry is at a confluence of 2+ anchor levels
    - Pattern is FIRST_RED_DAY, PUMP_COIL_DUMP, or PARABOLIC_REVERSAL
    - EMA Coil confirmed on higher time frame
    - Entry is at HCOM or LCOM level (monthly extreme close)
    """
    anchor_confluences = count_anchor_confluences(setup.entry_price, template.anchors, get_pip(setup.pair))
    stop_distance_atr = abs(setup.entry_price - setup.stop_price) / template.anchors.atr14
    at_monthly_extreme = is_near_hcom_lcom(setup.entry_price, template.anchors, get_pip(setup.pair))

    return (
        setup.score >= 7 and
        template.template_type in ["REVERSAL_WEEK", "NEW_MONTH_BREAKOUT"] and
        stop_distance_atr <= 0.75 and
        anchor_confluences >= 2 and
        setup.pattern in ["FIRST_RED_DAY", "PUMP_COIL_DUMP", "PARABOLIC_REVERSAL"] and
        (at_monthly_extreme or setup.ema_coil_confirmed)
    )
```

If `passes_100_lot_test()` is True → `trade_type` is forced to `FIVE_STAR_SCALABLE` regardless of score.  
This overrides the score-based classification when a truly exceptional setup is identified.

---

## Post-Trade Debrief Logger

After every terminal state, the system generates a structured debrief — the automated equivalent of a trading journal.

```python
def generate_debrief(record: TradeRecord) -> str:
    outcome = "✅ WIN" if record.r_multiple > 0 else "❌ LOSS"
    debrief = f"""
─────────────────────────────────
TRADE DEBRIEF — {record.pair} {record.direction}
─────────────────────────────────
Outcome:      {outcome} {record.r_multiple:+.2f}R ({record.pips:+.1f} pips)
Pattern:      {record.pattern}
Trade Type:   {record.trade_type}
Score:        {record.score}/12
Terminal:     {record.terminal_state}

Entry:        {record.entry_price} at {record.entry_time.strftime('%H:%M ET')}
Exit:         {record.exit_price} at {record.exit_time.strftime('%H:%M ET')}
Stop was:     {record.stop_price}
T1 was:       {record.target_1}

Rule check:
  - Entry at quarter level?    {is_quarter_level(record.entry_price, record.pair)}
  - Stop at quarter level?     {is_quarter_level(record.stop_price, record.pair)}
  - ACB behaviour observed?    {record.notes}
─────────────────────────────────
"""
    return debrief
```

Every debrief is stored in the database and included in the Friday weekly report. Losses are not hidden or minimized — they are logged with full context for system review.

---

## Discard Log

Every rejected setup must be logged with a reason. This builds the dataset for future backtesting and calibration.

```python
@dataclass
class DiscardedSetup:
    pair: str
    pattern: str
    direction: str
    score: int
    reason: str         # "DIDDLE_FILTERED" | "BELOW_MIN_SCORE" | "FRIDAY_GUARD" |
                        # "CIRCUIT_BREAKER" | "NO_ANCHOR_CONFLUENCE" | "WIDE_SPREAD" |
                        # "BASKET_OVEREXPOSED" | "STALE_DATA" | "IB_TOO_SMALL" |
                        # "NEWS_BLOCKED" | "TWO_SIDED_EXIT" | "TEMPLATE_CONFLICT" |
                        # "IN_PROGRESS_CANDLE" | "NOT_ON_WATCHLIST" | "MONTHLY_FRONTSIDE_ONLY" |
                        # "EMA_COIL_NOT_CONFIRMED" | "BELOW_HCOM_LCOM_REQUIRED" |
                        # "ENTRY_DATE_EXPIRED" | "MONDAY_FALSE_BREAK_NO_FRD"
    discarded_at: datetime
    would_have_hit_t1: bool | None   # Populated during backtesting review only
```

Reviewing `would_have_hit_t1` on discarded setups weekly identifies if filters are too aggressive.

---

## Weekly System Review Checklist

Run manually every Friday after NY close. Not automated — requires human eyes.

```
□ Review all DiscardedSetup records — were any filtered incorrectly?
□ Review all TradeRecord terminal states — any unexpected FORCE_CLOSE events?
□ Check circuit breaker log — were any halts triggered? Why?
□ Review override_attempt log — any manual interference? Was it justified?
□ Check spread log — any pairs with consistently wide spreads? Remove from watchlist?
□ Adjust NO configuration parameters mid-week review
□ Schedule next week configuration changes for Sunday night only
```


# skill_session_execution.md

## Skill: Session Execution

**Version:** 2.0 — Codeable Definitions  
**Depends on:** `skill_setup_identification` (requires a valid `Setup` object as input)

---

## Core Principle

Execution is **fully deterministic** given a valid `Setup` object. No discretion is applied at this layer. The executor's only job is: place the right order, the right size, at the right time, and manage it to completion without interference.

---

## Session Windows

All times are **New York ET**. The system only places orders during defined windows — never outside them.

| Session | Open | Close | Pairs in Focus |
|---|---|---|---|
| **Asia** | 7:00 PM | 11:00 PM | JPY crosses (AUDJPY, NZDJPY, GBPJPY, EURJPY, USDJPY), AUDUSD, NZDUSD, XAUUSD |
| **London** | 1:00 AM | 5:00 AM | EURUSD, GBPUSD, USDCHF, USDCAD, GBPJPY, EURJPY, XAUUSD |
| **New York** | 7:00 AM | 11:00 AM | EURUSD, GBPUSD, USDCHF, USDCAD, GBPJPY, EURJPY, XAUUSD |
| **NY Equity** | 9:30 AM | 11:00 AM | SP500, DJ30, NAS100 (indexes only — equity open) |
| **NY Close (EOD)** | 4:45 PM | 5:05 PM | All pairs — daily classification window |

**EOD window (4:45–5:05 PM ET) is the primary signal generation window.** This is when:

- Daily candles are evaluated
- Setups are detected and scored
- Orders are placed for the next session

---

## Live Session Level Tracking

All five named structural levels must be updated on every 1-min bar during an active session. They are passed into the execution layer as a live `SessionLevels` object.

```python
@dataclass
class SessionLevels:
    # Updated live, bar-by-bar
    hod: float              # High of Day — highest high since midnight ET
    lod: float              # Low of Day  — lowest low since midnight ET
    how: float              # High of Week — highest high Mon–today
    low_of_week: float      # Low of Week  — lowest low Mon–today
    hos: float              # High of Session — highest high since session open
    los: float              # Low of Session  — lowest low since session open

    # Static for the session (set at session open)
    prior_hod: float        # Yesterday's HOD — key breakout reference
    prior_lod: float        # Yesterday's LOD — key breakout reference
    prior_hos: float        # Prior session's HOS — LHF pullback reference
    prior_los: float        # Prior session's LOS — LHF pullback reference

def update_session_levels(levels: SessionLevels, bar: dict) -> SessionLevels:
    """Called on every new 1-min bar. Updates rolling highs/lows."""
    levels.hod = max(levels.hod, bar['high'])
    levels.lod = min(levels.lod, bar['low'])
    levels.how = max(levels.how, bar['high'])
    levels.low_of_week = min(levels.low_of_week, bar['low'])
    levels.hos = max(levels.hos, bar['high'])
    levels.los = min(levels.los, bar['low'])
    return levels

def reset_session_levels(prior_levels: SessionLevels) -> SessionLevels:
    """Called at each new session open. Carries forward HOD/HOW, resets HOS/LOS."""
    return SessionLevels(
        hod          = prior_levels.hod,        # HOD carries forward until midnight
        lod          = prior_levels.lod,
        how          = prior_levels.how,
        low_of_week  = prior_levels.low_of_week,
        hos          = 0.0,                     # HOS resets at session open
        los          = float('inf'),            # LOS resets at session open
        prior_hod    = prior_levels.hod,        # Freeze prior session's levels
        prior_lod    = prior_levels.lod,
        prior_hos    = prior_levels.hos,
        prior_los    = prior_levels.los,
    )
```

**These levels are used directly by Parabolic Reversal detection** — a setup triggers when price false-breaks one of these named levels within the equity hour window.

---

## Initial Balance (IB)

The IB is the **first hour of each session's high/low range**. It is the primary range from which measured move targets are projected intraday. Compute at session open + 60 minutes.

```python
@dataclass
class InitialBalance:
    session: str
    ib_high: float          # Highest price in first 60 minutes
    ib_low: float           # Lowest price in first 60 minutes
    ib_range: float         # ib_high - ib_low in pips
    target_100pct: float    # 100% expansion — primary target
    target_200pct: float    # 200% expansion — ACB/parabolic target
    target_300pct: float    # 300% expansion — extreme momentum only
    retracement_50: float   # 50% of IB — "First Bounce" entry zone

def compute_initial_balance(session_ohlcv_1min: pd.DataFrame, direction: str) -> InitialBalance:
    first_hour = session_ohlcv_1min.iloc[:60]
    ib_high = first_hour['high'].max()
    ib_low  = first_hour['low'].min()
    ib_range = ib_high - ib_low

    if direction == "SHORT":
        target_100 = snap_to_quarter(ib_low - ib_range)
        target_200 = snap_to_quarter(ib_low - (2 * ib_range))
        target_300 = snap_to_quarter(ib_low - (3 * ib_range))
        retrace_50 = snap_to_quarter(ib_low + (ib_range * 0.5))
    else:  # LONG
        target_100 = snap_to_quarter(ib_high + ib_range)
        target_200 = snap_to_quarter(ib_high + (2 * ib_range))
        target_300 = snap_to_quarter(ib_high + (3 * ib_range))
        retrace_50 = snap_to_quarter(ib_high - (ib_range * 0.5))

    return InitialBalance(...)
```

**Minimum IB range:** 20 pips. If IB range < 20 pips, session is dead — no entry armed.

---

## Quarter Level Grid

All entries, stops, and targets must snap to the nearest quarter level. This is the "hidden" grid Burke uses to time trades and verify targets.

```python
QUARTER_LEVELS_PIPS = [0, 25, 50, 75, 100]   # Per 100-pip range, repeating

def snap_to_quarter(price: float, pair: str) -> float:
    """
    Snap a price to the nearest 00/25/50/75 quarter level.
    For 5-decimal pairs (EURUSD): levels are at .0000, .0025, .0050, .0075
    For JPY pairs: levels are at .00, .25, .50, .75
    """
    pip = get_pip_size(pair)           # 0.0001 for majors, 0.01 for JPY
    level_size = 25 * pip              # One quarter level step
    return round(round(price / level_size) * level_size, 5)

def snap_stop_beyond(price: float, direction: str, pair: str) -> float:
    """
    For stops: snap to the quarter level BEYOND the wick, not nearest.
    Bearish trade stop → snap UP to next quarter above the wick.
    Bullish trade stop → snap DOWN to next quarter below the wick.
    """
    pip = get_pip_size(pair)
    level_size = 25 * pip
    if direction == "SHORT":
        return math.ceil(price / level_size) * level_size
    else:
        return math.floor(price / level_size) * level_size
```

**Why this matters for automation:** Round numbers act as liquidity magnets. Stops parked at 1.08413 get hunted; stops at 1.08425 (the quarter) are where the real orders cluster.

---

## Order Placement Rules

### The "Equity Hour" Rule

Burke only looks for entries during the **first hour of London or New York** — highest probability window. The entry *trigger* is a **15-minute candle close** at or near a quarter level — never a wick poke.

| Session | Equity Hour | Max Entry Window |
|---|---|---|
| Asia | 7:00–8:00 PM ET | 7:00–11:00 PM ET |
| London | 3:00–4:00 AM ET | 1:00–5:00 AM ET |
| New York (FX) | 7:00–8:00 AM ET | 7:00–11:00 AM ET |
| New York (Indexes) | 9:30–10:30 AM ET | 9:30–11:00 AM ET |

**London Equity Hour is 3:00–4:00 AM ET** (the first hour after full London open).
**NY indexes use 9:30 AM open** — do NOT enter index setups before 9:30 AM.

Outside these windows — no new entries regardless of setup score.

### EMA Coil Entry Gate (Mandatory — All Strategies)

> The EMA Coil is **not optional**. For every setup from every pattern, you do not enter until the coil forms near the level. Hitting a level is not enough. The coil is the entry trigger.

```python
def wait_for_ema_coil(
    pair: str,
    level: float,              # The daily signal level (HCOM, LCOM, FRD close, etc.)
    direction: str,            # "SHORT" or "LONG"
    session_1min: pd.DataFrame,
    ema_periods: list[int] = [9, 20, 50]
) -> CoilState:
    """
    Monitors the intraday feed in real time.
    Returns CoilState with triggered=True when:
      1. Price is within 10 pips of `level`
      2. All EMAs have converged (spread ≤ 0.5 × ATR14)
      3. Price has been sideways for ≥ 3 consecutive 15-min bars (the coil)
      4. A breakdown bar closes BELOW the coil low (SHORT) or ABOVE the coil high (LONG)
         → This is the actual entry trigger candle
    """
    emas = {p: session_1min['close'].ewm(span=p).mean() for p in ema_periods}
    current_spread = max(e.iloc[-1] for e in emas.values()) - min(e.iloc[-1] for e in emas.values())
    atr = compute_atr(session_1min, 14)
    near_level   = abs(session_1min['close'].iloc[-1] - level) <= 10 * get_pip(pair)
    emas_coiled  = current_spread <= 0.5 * atr
    sideways     = is_sideways(session_1min.iloc[-3:], atr)  # last 3 bars in tight range
    breakdown    = (
        session_1min['close'].iloc[-1] < session_1min['low'].iloc[-4:-1].min()  # SHORT
        if direction == "SHORT"
        else session_1min['close'].iloc[-1] > session_1min['high'].iloc[-4:-1].max()  # LONG
    )
    return CoilState(
        triggered    = near_level and emas_coiled and sideways and breakdown,
        coil_low     = session_1min['low'].iloc[-3:].min(),
        coil_high    = session_1min['high'].iloc[-3:].max(),
        ema_spread   = current_spread,
        bars_sideways= count_sideways_bars(session_1min, atr)
    )
```

**Entry is ONLY placed when `CoilState.triggered == True`.** Price touching a level without a coil = no trade. A daily signal without a coil the following session = expired, wait for next signal.

### Signal Day → Entry Day Sequence

The timing is always **two-day**:

```
Day N:   Signal Day closes (FRD, FGD, CIB, 3HC/3LC at HCOM/LCOM)
          → System flags setup, arms monitoring for next session
Day N+1: Entry Day — wait_for_ema_coil() monitors intraday
          → Coil forms near signal level → breakdown → ENTRY
          → If no coil forms by session close → EXPIRED, no carry to Day N+2
```

```python
def is_entry_day(setup: Setup, today: date) -> bool:
    """Entry is only valid on the day AFTER the signal day closed."""
    return today == setup.signal_date + timedelta(days=1)  # next trading day
```

---

### ACB (Ain't Coming Back) Filter

Once in a trade, if price shows **two-sided trading** (oscillating within 15 pips of entry for > 30 minutes), the setup has failed — exit at market immediately.

```python
def is_two_sided(price_history_15min: list[float], entry_price: float, pip: float) -> bool:
    """
    Returns True (exit signal) if price has oscillated within 15 pips
    of entry for 2+ consecutive 15-min candles post-entry.
    """
    threshold = 15 * pip
    recent = price_history_15min[-2:]
    return all(abs(p - entry_price) < threshold for p in recent)
```

A valid ACB trade moves **immediately and strongly** from entry. Hesitation = exit.

---

### Entry Order Type

| Condition | Order Type |
|---|---|
| Setup score ≥ 9 AND direction aligns with session trend | **Limit order at Setup.entry_price** — patient fill |
| Setup score 5–8 | **Stop entry order at Setup.entry_price** — momentum confirmation required |
| News event within 4 hours of session open | **No order placed** — defer to next valid session |

### Entry Timing by Setup Pattern

| Pattern | Preferred Session | Fallback Session |
|---|---|---|
| PUMP_COIL_DUMP | London equity hour (3:00 AM) | NY open (7:00 AM) |
| FIRST_RED_DAY | NY open (7:00 AM) or NY equity (9:30 AM for indexes) | London open |
| INSIDE_FALSE_BREAK | NY open — wait until 10:00 AM minimum | Skip if no trigger by 11:00 AM |
| MONDAY_FALSE_BREAK | London equity (3:00 AM) Tuesday onward | NY open (7:00 AM) |
| LOW_HANGING_FRUIT | NY open (7:00 AM) | NY equity (9:30 AM for indexes) |

**10:00 AM NY Rule:** For INSIDE_FALSE_BREAK and any setup coinciding with a high-impact news release, the entry order is not armed until 10:00 AM ET. This allows the news trap to play out and the real direction to establish.

---

## Position Sizing

Account risk is **fixed at 1% per trade** regardless of score. Score determines position *structure* (how many targets, whether a trailer runs), not risk amount. This keeps drawdown mechanical and removes sizing as a discretionary input.

```python
def calculate_position_size(
    account_balance: float,
    risk_pct: float,          # Always 0.01 (1%)
    entry_price: float,
    stop_price: float,
    pair: str
) -> float:
    risk_amount = account_balance * risk_pct
    stop_distance_pips = abs(entry_price - stop_price) * pip_multiplier(pair)
    pip_value = get_pip_value(pair)                  # USD value per pip per lot
    lot_size = risk_amount / (stop_distance_pips * pip_value)
    return round(lot_size, 2)                        # Round to nearest 0.01 lot
```

**Pip multiplier reference:**

- 5-decimal pairs (EURUSD, GBPUSD): multiply by 10,000
- JPY pairs (USDJPY, GBPJPY): multiply by 100
- XAU/USD (Gold): multiply by 10

---

## Trade Tier Classification

Burke explicitly names two tiers. The `trade_type` field on every `Setup` uses these exact labels.

| Tier | Burke Name | Score | Position Structure | Session Scope |
|---|---|---|---|---|
| 1 | **SESSION TRADE** | 5–8 | Half position, Target 1 only, exit same session | Equity hour window only |
| 2 | **5-STAR SCALABLE** | ≥9 OR EMA Coil confirmed | Full tranche structure, trailer to T2/T3 | Can carry across sessions |

```python
def classify_trade_tier(setup: Setup, ema_coil: bool) -> str:
    """
    Returns "SESSION_TRADE" or "FIVE_STAR_SCALABLE".
    EMA coil overrides score — a coiled setup is always 5-star.
    """
    if ema_coil or setup.score >= 9:
        return "FIVE_STAR_SCALABLE"
    return "SESSION_TRADE"
```

**5-STAR SCALABLE criteria (any one qualifies):**

- Score ≥ 9
- EMA Coil confirmed on higher time frame
- Setup is at HCOM/LCOM level (monthly extreme)
- Monthly phase is BACKSIDE + FRD/FGD signal
- New month FDTM CIB with 3HC/3LC count at SIGNAL_DAY

---

## Position Structure by Trade Type

Determined by `Setup.trade_type` from the scoring system.

### SESSION_TRADE (formerly SESSION_TRADE — Score 5–8)

```
Total position = 1 lot equivalent at 1% risk

Lot allocation:
  - Tranche A (100% of position): exit at Target 1

Stop management:
  - Initial stop: Setup.stop_price
  - After Target 1 hit: CLOSE ENTIRE POSITION — no trailer
  - Move to breakeven: NOT applied on nail-and-bail (exit is binary)

Max hold time: Current session only. If Target 1 not hit by session close → exit at market.
```

### FIVE_STAR_SCALABLE (Score ≥ 9 or EMA Coil)

```
Total position = 1 lot equivalent at 1% risk, split into tranches

Lot allocation:
  - Tranche A (50%): exit at Target 1
  - Tranche B (30%): exit at Target 2
  - Tranche C (20%): trail to Target 3 or stop

Stop management:
  - Initial stop: Setup.stop_price (all tranches)
  - After Tranche A fills: move stop to breakeven on B + C
             **Breakeven rule (exact):** Do NOT move to BE until EITHER:
             (a) A 15-min candle CLOSES 30 pips or more in your favour, OR
             (b) Price CLOSES beyond a significant high/low boundary on a 15-min candle
             → Moving stop too early is the #1 way to get stopped out of a winner
  - After Tranche B fills: move stop to Target 1 level on C (lock in profit)
  - Tranche C: trailing stop = 0.75 × ATR14 behind highest close (not highest wick)

Max hold time: Expires at NY close Friday. If still open entering Friday → close Tranche C at market.
```

---

## Order Management State Machine

Each active trade moves through these states exactly once — no backwards transitions.

```
PENDING_ENTRY
    │
    ├─ Entry triggered within validity window → ACTIVE
    └─ Validity window expires unfilled → EXPIRED (cancel order, log reason)

ACTIVE
    │
    ├─ Stop hit → STOPPED_OUT (log entry/exit/pips/R-multiple)
    ├─ Target 1 hit → PARTIAL_EXIT
    └─ Session close with no targets hit (SESSION_TRADE only) → FORCE_CLOSE

PARTIAL_EXIT
    │
    ├─ Stop (moved to BE) hit → BREAKEVEN_CLOSE
    ├─ Target 2 hit → PARTIAL_EXIT_2
    └─ Friday close → FORCE_CLOSE

PARTIAL_EXIT_2
    │
    ├─ Trailing stop hit → TRAIL_CLOSE
    ├─ Target 3 hit → FULL_TARGET_CLOSE
    └─ Friday close → FORCE_CLOSE

Terminal states: STOPPED_OUT | EXPIRED | FORCE_CLOSE | BREAKEVEN_CLOSE | TRAIL_CLOSE | FULL_TARGET_CLOSE
```

Every terminal state must write a `TradeRecord` to the database before the position is considered closed.

---

## News Filter

Check economic calendar before arming any order. Source: ForexFactory JSON feed or investing.com API.

```python
def is_news_blocked(pair: str, session_open: datetime) -> bool:
    """
    Returns True if a high-impact news event falls within
    the 4-hour window around session open for this pair's currencies.
    """
    currencies = get_currencies(pair)           # e.g. EURUSD → ["EUR", "USD"]
    window_start = session_open - timedelta(hours=1)
    window_end = session_open + timedelta(hours=3)
    events = fetch_calendar(window_start, window_end, impact="HIGH")
    return any(e.currency in currencies for e in events)
```

**Blocked = order not placed.** Setup is not marked expired — it is held in `PENDING_ENTRY` and re-evaluated at the next valid session window within its validity period.

---

## Circuit Breakers

These override all setup signals. Checked before any order is placed.

| Condition | Action |
|---|---|
| Daily loss ≥ 2% of account | Halt all new entries for remainder of trading day |
| 3 consecutive stopped-out trades | Halt new entries for 24 hours — log for review |
| Account drawdown ≥ 5% from weekly open | Halt new entries until Monday NY open |
| Spread > 3× normal average spread for pair | Skip entry — abnormal liquidity condition |
| MT5/broker connection lost > 60 seconds | Cancel all pending orders, alert via Telegram |

---

## Logging & Alerts

Every state transition must produce a structured log entry and a Telegram alert.

### Telegram Alert Template

```
🔔 [PAIR] [DIRECTION] [PATTERN]
State: PENDING_ENTRY → ACTIVE
Entry: 1.08420 | Stop: 1.08210 | T1: 1.08750
Risk: 1% | Size: 0.45 lots | Score: 8 | Type: SESSION_TRADE
Session: London | Time: 03:14 ET
```

### TradeRecord Schema

```python
@dataclass
class TradeRecord:
    trade_id: str              # UUID
    pair: str
    pattern: str
    direction: str             # LONG | SHORT
    trade_type: str            # SESSION_TRADE | FIVE_STAR_SCALABLE
    score: int
    session: str               # ASIA | LONDON | NEW_YORK
    entry_price: float
    entry_time: datetime
    stop_price: float
    lot_size: float
    target_1: float
    target_2: float
    target_3: float | None
    exit_price: float          # Weighted avg if multiple tranches
    exit_time: datetime
    terminal_state: str        # One of the terminal states above
    pips: float                # Positive = profit
    r_multiple: float          # e.g. 2.3R = made 2.3× the amount risked
    notes: str
```

---

## Output Contract

The executor exposes one public function:

```python
def execute_setup(
    setup: Setup,
    account: AccountState,
    broker: MT5Client | cTraderClient
) -> TradeRecord:
    """
    Places orders, manages the trade through its full lifecycle,
    and returns a completed TradeRecord on terminal state.
    Raises ExecutionError on broker connectivity failure.
    """
```

`AccountState` contains: `balance`, `equity`, `daily_pnl`, `weekly_drawdown`, `consecutive_losses`, `open_positions`.


# skill_setup_identification.md

## Skill: Setup Identification

**Version:** 2.0 — Codeable Definitions  
**Depends on:** `skill_market_classification`, `skill_weekly_template_mapping`

---

## Core Principle

The system recognizes **two trades only:** Buy Low or Sell High. Every pattern below is a structured expression of one of these two trades. A setup is only valid if it satisfies **all criteria** in its checklist — partial matches are discarded as "diddles."

---

## Critical: EOD-Only Rule

> **"These SETUPS are based on the CLOSING PRICE. The SETUPS will present AFTER the previous day has CLOSED, NOT WHILE IT'S TRADING."**
> — Stacey Burke, ACB Manual p.32

```python
def is_candle_closed(timestamp: datetime, pair: str) -> bool:
    """
    Hard guard. No setup is evaluated on a candle that has not yet closed.
    NY close = 4:59 PM ET. EOD run fires at 5:04 PM ET minimum.
    Returns False (block evaluation) if current time < NY close for today.
    """
    ny_close_today = get_ny_close_today()
    return datetime.now(ET) >= ny_close_today + timedelta(minutes=4)

# Called at the top of detect_setups():
if not is_candle_closed(datetime.now(ET), pair):
    raise RuntimeError("EOD run fired before NY close — aborting to prevent in-progress candle evaluation")
```

---

## Shared Pre-Conditions (All Setups)

Before evaluating any pattern, the following must be true:

| Parameter | Rule |
|---|---|
| **Data basis** | NY close (4:59 PM ET) daily OHLCV only — no intraday data |
| **Lookback window** | 10 trading days (2 full weeks) |
| **ATR period** | 14-day ATR on daily closes |
| **Minimum ATR** | Current ATR ≥ 0.0050 (50 pips) for major Forex pairs — filters dead/illiquid markets |
| **Market state** | Must be classified as BREAKOUT or TRENDING — no setups in RANGING markets |
| **Day filter** | No new setups on Fridays (backside exits only) |

---

> **CRITICAL DISTINCTION:** A *Three Day Setup* (the pump/dump cycle over 3 days) is **NOT the same** as *Day 3 Longs/Shorts* (a specific intraday trigger on Day 3). These are two separate, distinct trading setups. The Three Day Setup is the multi-day context; Day 3 Longs/Shorts is one specific entry within it.

---

## Pattern 1: Three Higher/Lower Closes → Pump, Coil, and Dump

**Burke notation: 3HC (Three Higher Closes) → bearish setup | 3LC (Three Lower Closes) → bullish setup**
*Also called: Pump and Dump (bearish) | Dump and Pump (bullish)*

### How the Three Day Cycle Begins: Day 1 False Break

**Day 1 of the Three Day Setup starts with a FALSE BREAK from the HIGH or LOW of the WEEK.**
A day breaks out of the prior day's high or low, then closes back INSIDE — this is the failed breakout that starts the cycle. Day 2 can then become a FGD, FRD, Inside Day, Breakout Trend, or Reversal. Day 3 is the completion.

### Conceptual Logic

Market posts 3 consecutive closes in one direction (the 3HC/3LC count), compresses into a coil (energy buildup), then reverses sharply to trap late breakout chasers. This is Burke's **favourite EA$Y MONEY setup** and the foundation of the ACB methodology.

### Codeable Criteria

**Step 1 — Identify the Pump (Days -4 to -2)**

```
- Minimum 2 consecutive higher daily closes (bullish pump) OR lower closes (bearish pump)
- Each pump day's range (High - Low) ≥ 0.75 × ATR14  →  "full-bodied" candles, not doji days
- Net displacement over pump legs ≥ 1.5 × ATR14  →  meaningful move, not noise
```

**Step 2 — Identify the Coil (Day -1)**

```
- Day -1 range (High - Low) ≤ 0.50 × ATR14  →  compression/inside day
- Close must be within the upper 30% of range (bearish coil) OR lower 30% (bullish coil)
  → This signals rejection, not indecision
- Day -1 must NOT make a new 5-day high (bearish) or new 5-day low (bullish)
  → If it does, the pump is still in progress — wait
```

**Step 3 — Confirm the Setup (Day 0, current close)**

```
- Bearish dump: Day 0 closes BELOW Day -1 low  →  breakdown confirmed
- Bullish pump: Day 0 closes ABOVE Day -1 high  →  breakout confirmed
- Day 0 range ≥ 0.75 × ATR14  →  momentum candle, not a weak poke
```

**Step 4 — Entry Parameters**

```
- Entry:     Snap to nearest quarter level (00/25/50/75) at or beyond Day 0 close
             e.g. close at 1.08437 bearish → entry at 1.08425 (nearest 25 below)
- Stop:      2 ticks above Day -1 high (bearish) / below Day -1 low (bullish)
             Snap stop to nearest quarter level BEYOND the wick

- Target 1:  100% measured move expansion from coil range
             = coil_low - (pump_high - coil_low) for bearish
             = coil_high + (coil_high - pump_low) for bullish
             → Snap to nearest 00 or 50 level AT OR BEFORE the calculated target
             → Minimum 50 pips from entry — discard setup if target < 50 pips away

- Target 2:  200% expansion (parabolic/ACB setups, score ≥ 9 only)
             = entry + 2× the coil-to-pump range
             → Only active if Day 0 is a strong momentum candle (close in top/bottom 20% of range)

- Target 3:  300% expansion (extreme momentum — Gold, indices during news events only)
             → Only arm if score = 12 AND volatility > 1.5× ATR14 on Day 0

- Max risk:  Stop ≤ get_max_stop(pair)[1] pips (15–20 currencies, 20–25 gold/oil/indexes)
             Also check: stop ≤ 0.75 × ATR14 — whichever is TIGHTER wins
```

**Validity Window:** Setup is live for 1 trading day only.

---

## Instrument Pip Level Reference (Three Levels of Rise/Fall)

Burke uses fixed pip level benchmarks per instrument class to set targets and assess whether Monday's opening range is large, normal, or small. These replace abstract ATR multiples for target calibration.

```python
THREE_LEVELS = {
    # Currencies: 3 levels (quarter level multiples of 75)
    "CURRENCIES": {"L1": 75, "L2": 150, "L3": 250, "L3_EXT": 300},
    # Gold (XAUUSD)
    "GOLD":       {"L1": 150, "L2": 250, "L3_EXT": 300},
    # Oil (USOIL/UKOIL)
    "OIL":        {"L1": 150, "L2": 250, "L3": 300, "L4": 500, "L5": 750},
    # Equity Indexes (SP500, NAS100, DJ30)
    "INDEXES":    {"L1": 250, "L2": 500, "L3": 750},
}

INSTRUMENT_CLASS = {
    "EURUSD": "CURRENCIES", "GBPUSD": "CURRENCIES", "USDJPY": "CURRENCIES",
    "USDCHF": "CURRENCIES", "USDCAD": "CURRENCIES", "AUDUSD": "CURRENCIES",
    "NZDUSD": "CURRENCIES", "GBPJPY": "CURRENCIES", "EURJPY": "CURRENCIES",
    "AUDJPY": "CURRENCIES", "CADJPY": "CURRENCIES", "GBPAUD": "CURRENCIES",
    "XAUUSD": "GOLD",
    "USOIL":  "OIL",   "UKOIL": "OIL",
    "SP500":  "INDEXES", "NAS100": "INDEXES", "DJ30": "INDEXES",
}

MAX_STOP_PIPS = {
    "CURRENCIES": (15, 20),   # min, max
    "GOLD":       (20, 25),
    "OIL":        (20, 25),
    "INDEXES":    (20, 25),   # points, not pips — adjust per instrument
}

def get_three_levels(pair: str) -> dict:
    cls = INSTRUMENT_CLASS.get(pair, "CURRENCIES")
    return THREE_LEVELS[cls]

def get_max_stop(pair: str) -> tuple[int, int]:
    cls = INSTRUMENT_CLASS.get(pair, "CURRENCIES")
    return MAX_STOP_PIPS[cls]
```

**Monday's opening range context:**

- Monday range < L1 → small opening range, watch for expansion
- Monday range ≈ L1 → normal range, standard thesis
- Monday range ≈ L2 → large opening range, reversal candidate already in play
- Monday range > L2 → explosive day, LHF continuation likely for NY

**Target selection rule:** Always target the nearest THREE_LEVELS pip level at or beyond the 100% measured move. Snap to the closest L1/L2/L3 level rather than a raw calculated pip number. If price does not trigger entry on Day 1, mark as expired.

---

## EMA Coil Confirmation (All Patterns — Optional but High Value)

The EMA Coil is a secondary confirmation that all time frames are aligned at the entry level. When present it upgrades any setup to near-maximum conviction. Check AFTER a setup is detected.

```python
def has_ema_coil(ohlcv_htf: pd.DataFrame, ema_periods: list[int] = [8, 21, 55, 100, 200]) -> bool:
    """
    EMA Coil = multiple EMAs have converged (are within a tight band) at the
    current price level after a period of sideways consolidation.

    Conditions:
    1. All EMAs are within 0.5 × ATR14 of each other (tight convergence)
    2. Price has been range-bound for at least 3 bars at this level (the coil)
    3. EMAs are flat or beginning to turn in the trade direction

    When ALL time frames have the same EMAs coiled together = maximum conviction.
    Burke calls this "ZERO Management" — the trade fires and doesn't come back.
    """
    emas = {p: ohlcv_htf['close'].ewm(span=p).mean().iloc[-1] for p in ema_periods}
    ema_spread = max(emas.values()) - min(emas.values())
    atr = compute_atr(ohlcv_htf, 14)
    coil_tight = ema_spread <= 0.5 * atr

    # Check sideways consolidation — last 3 bars within 1 ATR range
    last_3_range = ohlcv_htf['high'].iloc[-3:].max() - ohlcv_htf['low'].iloc[-3:].min()
    coil_sideways = last_3_range <= 1.0 * atr

    return coil_tight and coil_sideways
```

**If `has_ema_coil()` returns True → add +2 to setup score and flag as `FIVE_STAR_SCALABLE` candidate regardless of base score.**

---

## Pattern 2: First Red Day (Bearish) / First Green Day (Bullish)

### Conceptual Logic

After a sustained multi-day trend, the first candle that closes against the trend is a signal that the front side is exhausted. This is the "backside entry" — fade the trend at the first reversal candle on Wednesday or Thursday.

### Codeable Criteria

**Step 1 — Confirm a Valid Trend Leg**

```
- Minimum 3 consecutive closes in the same direction (e.g., 3 higher closes = uptrend)
- Each trending day: close must be in upper/lower 40% of its range
  → Filters weak closes that don't reflect real directional conviction
- Net trend move ≥ 2.0 × ATR14  →  meaningful expansion, not chop
```

**Step 2 — Identify First Red/Green Day**

```
- First Red Day (exact condition):
    current close < current open           →  bearish candle body (close below its OWN open)
    AND current close < previous close     →  also lower than yesterday's close
    → BOTH conditions required — a candle that closes lower than yesterday but
      still closes above its own open is NOT a First Red Day, it is just a pullback

- First Green Day (exact condition):
    current close > current open           →  bullish candle body (close above its OWN open)
    AND current close > previous close     →  also higher than yesterday's close

- Must occur on Wednesday OR Thursday (day 3 or 4 of the trading week)
  → Monday/Tuesday First Red/Green Days are front-side noise — skip
  → Friday is exit-only — skip
```

**Step 3 — Rejection Confirmation**

```
- Reversal candle body ≥ 0.40 × candle range  →  real close, not a doji/indecision
- Reversal candle range ≥ 0.60 × ATR14  →  meaningful day, not a limp move
- Reversal close must NOT exceed the high of Day -3 (bearish) or low of Day -3 (bullish)
  → If it does, the trend may still have legs — wait one more day
```

**Step 4 — Entry Parameters**

```
- Entry:     Snap to nearest quarter level (00/25/50/75) at or near Day 0 close
             If Day 0 close sits between two quarter levels, use the one closer to stop

- Stop:      2 ticks above the high of the reversal candle (bearish) / below low (bullish)
             Snap to nearest quarter level BEYOND the wick

- First Bounce variation (higher conviction entry):
             If price pulls back 50% into the prior trend leg after the First Red/Green Day,
             wait for that 50% level — this is the "sweet spot" re-entry
             50% level = trend_leg_start + (trend_leg_end - trend_leg_start) × 0.5

- Target 1:  100% measured move of the reversal candle's range, projected from entry
             → Snap to nearest 00 or 50 level AT OR BEFORE calculated target
             → Minimum 50 pips — discard if target < 50 pips from entry

- Target 2:  100% retracement of the entire prior trend leg (full round trip)
             = trend_leg_start price
             → Snap to nearest 00 or 50 level

- Target 3:  200% expansion beyond trend_leg_start (only score ≥ 9 "month money" setups)

- Max risk:  Stop distance ≤ 1.0 × ATR14  →  discard if setup requires a wide stop
```

**Validity Window:** 1 trading day. If entry is not triggered by next EOD, mark expired.

---

## Pattern 3: Inside Day False Break

### Conceptual Logic

An inside day (range fully contained within prior day) followed by a breakout in one direction that immediately reverses — trapping breakout traders and offering a fade entry.

### Codeable Criteria

**Step 1 — Identify Inside Day**

```
- Day -1: High < Day -2 High AND Low > Day -2 Low  →  strict inside day
- Day -1 range ≤ 0.50 × ATR14  →  genuine compression, not a near-miss
```

**Step 2 — Identify False Break on Day 0**

```
- Day 0 trades ABOVE Day -2 High (false bullish breakout) or BELOW Day -2 Low (false bearish)
  → Price must have traded through the level intraday (use High/Low, not just close)
- Day 0 CLOSES back inside Day -2 range  →  the break failed, close is the confirmation
- Day 0 close in lower 25% of range (false bullish break) or upper 25% (false bearish)
  → Strong rejection required
```

**Step 3 — Entry Parameters**

```
- Entry:     Snap to nearest quarter level (00/25/50/75) at or near Day 0 close
- Stop:      2 ticks above Day 0 High (false bullish) / below Day 0 Low (false bearish)
             Snap to nearest quarter level BEYOND the wick
- Target 1:  Opposite side of Day -2 range (the "full box" 100% measured move)
             = Day -2 Low (bearish false break) or Day -2 High (bullish false break)
             → Snap to nearest 00 or 50 level
             → Minimum 50 pips — discard if box is too small
- Target 2:  200% expansion beyond the box (Day -2 range × 2, projected from entry)
             → Only on score ≥ 9
- Max risk:  Stop distance ≤ 1.25 × ATR14
```

**Validity Window:** 1 trading day.

---

## Pattern 4: Parabolic Reversal at Structural Level

### Conceptual Logic

Price pushes aggressively into a named structural level (HOD, LOD, HOW, LOW, HOS, LOS or a prior week's HCOW/LCOW). It briefly breaks through — creating a false break / stop hunt — then reverses immediately. Traders who chased the break are trapped. The entry fades the false break.

### Codeable Criteria

**Step 1 — Identify the Structural Level**

```
- Named level must be one of: HOD, LOD, HOW, low_of_week, HOS, LOS,
  prior_week_high, prior_week_low, prior_week_hcow, prior_week_lcow, month_open
- Level must be within 15 pips of the current price at session open
  → "In play" means price is gravitating toward it, not 100 pips away
```

**Step 2 — Confirm the False Break on 15-min chart**

```
- A 15-min candle trades BEYOND the structural level (wick poke)
- The SAME 15-min candle CLOSES back on the near side of the level
  → Wick through, close back inside = false break confirmed
- The reversal candle body ≥ 40% of the candle's total range
  → Real rejection, not a tiny body doji
- False break must occur within the equity hour window
  (London 2–5 AM ET | NY 8–11 AM ET)
```

**Step 3 — Entry Parameters**

```
- Entry:     Limit order at the structural level (snap to nearest quarter level)
             OR market order on the 15-min close that confirms the false break
- Stop:      2 ticks beyond the extreme of the false break wick
             Snap to next quarter level BEYOND the wick (snap_stop_beyond)
- Target 1:  HOD/LOD of the opposite side of the session range (100% session expansion)
             OR prior session's HOS/LOS — whichever is closer to a round number
             Snap to nearest 00 or 50 level
             Minimum 50 pips from entry
- Target 2:  Opening Range target_100pct in the reversal direction
- Max risk:  Stop distance ≤ 1.0 × ATR14
```

**Validity Window:** Current session only. If not triggered within the equity hour window → EXPIRED.

---

## Pattern 5: Low Hanging Fruit (LHF) — Session Continuation

### Conceptual Logic

An explosive directional move occurs in an earlier session (typically London). The move is so strong it leaves "low hanging fruit" — a clear directional bias and an easy continuation entry for the next session (typically New York). Price pulls back to the 50% retracement of the prior session's move, then resumes. The fruit is already on the ground — just pick it up.

### Codeable Criteria

**Step 1 — Confirm a Prior Session Explosive Move**

```
- Prior session (e.g. London) range ≥ 1.5 × ATR14
  → The session must have moved significantly, not just drifted
- Prior session close is in the top 20% of its range (bullish LHF)
  OR bottom 20% of its range (bearish LHF)
  → Session must have closed strong, confirming the direction
- Prior session HOS or LOS must have broken a named structural level
  (HOD, LOD, prior_week_hcow, prior_week_lcow, etc.)
  → The move must have taken out a real level, not random space
```

**Step 2 — Identify the 50% Pullback Zone in Next Session**

```
- In the next session (e.g. NY), price pulls back toward the 50% level
  of the prior session's range:
  pullback_target = prior_los + (prior_hos - prior_los) * 0.5   # for bullish LHF
  pullback_target = prior_hos - (prior_hos - prior_los) * 0.5   # for bearish LHF
- Snap pullback_target to nearest quarter level
- Price must reach within 10 pips of pullback_target
  → If price never pulls back and just continues — LHF entry is MISSED, do not chase
```

**Step 3 — Entry Trigger**

```
- On 15-min chart, wait for an engulfing candle or pin bar AT the pullback level
  (within the 10-pip tolerance zone)
- Engulfing candle: body fully engulfs prior candle's body in the continuation direction
- Pin bar: long wick rejection at the pullback level, close in continuation direction
- Entry trigger must occur within the NY equity hour (8–11 AM ET)
```

**Step 4 — Entry Parameters**

```
- Entry:     Limit order at pullback_target (snap to nearest quarter level)
- Stop:      Below the low of the pullback candle (bullish) / above its high (bearish)
             Snap to next quarter level beyond the wick
- Target 1:  Prior session's HOS (bullish) or LOS (bearish) — continuation to new session extreme
             Snap to nearest 00 or 50 level
             Minimum 50 pips from entry
- Target 2:  Opening Range target_100pct in the continuation direction
- Max risk:  Stop ≤ 0.75 × ATR14 — LHF setups should have tight stops at the pullback level
```

**Validity Window:** NY equity hour only (7:00–11:00 AM ET for FX, 9:30–11:00 AM for indexes). If pullback and trigger do not occur → EXPIRED.

---

## Sub-Pattern: Three Session Setups (Intraday Mini Pump/Dump)

A smaller version of the Three Day Setup occurring within a SINGLE day across sessions. SESSION TRADE tier only — never 5-STAR SCALABLE.

```
Three Session Setup (currencies):
  3 levels × 25 pips = 75-pip intraday box
  Asia session pumps 25 pips → consolidates → London dumps 25 pips → NY continuation
  Target: 75 pips total (3 × L1 quarter level)

Three Session Setup (indexes):
  3 levels × 83 pips ≈ 250 pips (L1 for indexes)
```

**Classification rule:** If the setup completes within a single calendar day across sessions → SESSION_TRADE. If it spans multiple days → evaluate as full Three Day Setup.

---

## Pattern 6: Monday False Break (Pump & Dump / Dump & Pump)

### Conceptual Logic

Monday breaks the prior week's high or a major daily level, closes as a higher high — establishing the "Pump" phase. This traps breakout buyers. The system then waits for price to fail and fade the trap later in the week. This is a weekly template setup, not a session setup — the signal fires on Monday's EOD close, execution comes on Tuesday–Thursday when the failure is confirmed.

### Codeable Criteria

**Step 1 — Monday Pump Signal (EOD Monday close)**

```
- Monday's intraday HIGH breaks above prior_week_high (HOW from last week)
  OR Monday's intraday HIGH breaks above a major daily level (HOM, HCOM, prior_week_hcow)
- Monday's CLOSE is higher than prior_week_high (closes IN breakout, not just a wick poke)
  → The close must confirm — a wick through without a close = no signal
- Monday close is in the upper 30% of Monday's range
  → Strong bullish close, not a reversal candle
```

```
DUMP Signal (inverse — Monday False Break Short):
- Monday's intraday LOW breaks below prior_week_low
- Monday CLOSES below prior_week_low (close confirms breakdown)
- Monday close in lower 30% of range
```

**Step 2 — Failure Confirmation (Tuesday–Thursday)**

```
- Market fails to make a new high above Monday's high on Tuesday or Wednesday
  → day_break_counter does NOT extend to +2 or +3 in the bullish direction
- A First Red Day prints on Tuesday, Wednesday, or Thursday
  → close < open AND close < Monday's close
  → This IS the 3HC/3LC → FRD pattern, but specifically primed by Monday's false break
```

**Step 3 — EMA Coil Formation (Entry Day — day after FRD)**

```
- Following the FRD, wait for EMA Coil to form near Monday's close level
  (or near HCOW / HCOM level — whichever is the nearest anchor)
- EMA Coil breakdown = entry trigger (handled by wait_for_ema_coil() in skill_session_execution)
```

**Step 4 — Entry Parameters**

```
- Entry:     On EMA Coil breakdown — snap to nearest quarter level
- Stop:      2 ticks above Monday's HIGH (bearish) / below Monday's LOW (bullish)
             Snap to next quarter level BEYOND the extreme
- Target 1:  Low of Week (low_of_week) — 100% measured move from Monday's close
             Snap to nearest 00 or 50 level. Minimum 50 pips.
- Target 2:  Opening Range target_100pct_dn (Monday–Tuesday box expansion)
- Target 3:  Prior week's LCOW (bearish) / HCOW (bullish) — only on 5-STAR SCALABLE
- Max risk:  Stop ≤ 1.25 × ATR14
```

**Validity Window:** Tuesday–Thursday only. If no FRD confirmation by Thursday EOD → EXPIRED.
Monday False Breaks that resolve on Friday are discarded (exit-only day).

---

## Setup Scoring (Priority Ranking)

When multiple setups trigger across the basket on the same day, rank by score and trade the highest only (one setup at a time).

| Criterion | Points |
|---|---|
| Setup occurs on Wednesday or Thursday | +2 |
| Market state = BREAKOUT (not just TRENDING) | +2 |
| Stop distance ≤ 0.50 × ATR14 (tight risk) | +2 |
| R:R ≥ 3:1 at Target 1 alone | +2 |
| Confirmed by 2+ correlated pairs showing same state | +2 |
| EMA Coil confirmed on higher time frame (all EMAs converging) | +2 |
| Setup at HCOM/LCOM (highest/lowest close of month) | +2 |
| Monthly phase is BACKSIDE (Week 3–4) — highest reversal probability | +1 |
| CIB count = SIGNAL_DAY (Day 3+ close in breakout direction) | +1 |
| Setup is First Red/Green Day (highest probability pattern) | +2 |
| Setup is Parabolic Reversal at named structural level (HOW/LOW/HCOW) | +2 |
| Setup is Low Hanging Fruit after explosive prior session | +1 |
| Setup is Monday False Break (clear HOW/LOW trap) | +2 |
| Setup is against a 5-day trend (backside only) | +1 |
| **Maximum score** | **12** |

**Minimum score to fire:** 5  
**"Load the boat" threshold:** Score ≥ 9 → scale to full position size  
**"Nail and bail" threshold:** Score 5–8 → half position, Target 1 only, no trailer  

---

## Discard Conditions (Any One = Skip)

- ATR < 0.0050 (dead market)
- Setup triggers on a Friday
- Stop distance > 1.25 × ATR14
- Market state = RANGING
- Score < 5
- Same pair triggered a setup in the last 2 trading days (cooldown period)
- Today is NOT the entry_date (signal_date + 1) — setups don't carry past Day N+1
- EMA Coil did not confirm during the session (entry_date expired without coil)
- Major scheduled news event within 4 hours of entry (check economic calendar)

---

## Output Contract

The `detect_setups()` function must return a list of `Setup` objects:

```python
@dataclass
class Setup:
    pair: str                  # e.g. "EURUSD"
    pattern: str               # "PUMP_COIL_DUMP" | "FIRST_RED_DAY" | "INSIDE_FALSE_BREAK"
                               # | "PARABOLIC_REVERSAL" | "LOW_HANGING_FRUIT" | "MONDAY_FALSE_BREAK"
    signal_date: date          # The signal day (FRD/FGD/CIB close date)
    entry_date: date           # signal_date + 1 trading day — the only valid entry day
    ema_coil_confirmed: bool   # True if EMA coil detected on entry day
    direction: str             # "LONG" | "SHORT"
    entry_price: float
    stop_price: float
    target_1: float
    target_2: float
    target_3: float | None     # Only on score >= 9
    risk_pips: float
    score: int                 # 0–12
    trade_type: str            # "NAIL_AND_BAIL" | "LOAD_THE_BOAT"
    expires: date              # Next trading day EOD
    notes: str                 # Human-readable rationale
```


# skill_weekly_template_mapping.md

## Skill: Weekly Template Mapping

**Version:** 2.0 — Codeable Definitions  
**Depends on:** `skill_market_classification` (requires `MarketState` per pair)  
**Runs at:** NY close (4:59 PM ET) Monday–Friday

---

## Core Principle

The weekly template is the **structural context** that tells the system where it is in the weekly cycle. Every day of the week has a role. The system does not treat Monday and Thursday the same — they have different probabilities and different setup types. Map the week first. Then hunt the setup.

---

## Input Requirements

```python
@dataclass
class WeeklyTemplateInput:
    pair: str
    daily_ohlcv: pd.DataFrame       # At minimum: current week + prior 2 weeks
                                    # Columns: date, open, high, low, close
                                    # NY close (4:59 PM ET) only
    current_day: str                # "MON" | "TUE" | "WED" | "THU" | "FRI"
    week_number: int                # ISO week number
    month_open: float               # First close of the calendar month
```

---

## Step 1 — Establish the Weekly Anchor Levels

Computed fresh each Monday at NY close. Referenced all week without recalculation.

```python
@dataclass
class WeeklyAnchors:
    # Prior week levels
    prior_week_high: float          # Highest HIGH of prior week (Mon–Fri)
    prior_week_low: float           # Lowest LOW of prior week
    prior_week_hcow: float          # Highest CLOSE of prior week (HCOW)
    prior_week_lcow: float          # Lowest CLOSE of prior week (LCOW)

    # Prior month levels (reset on first trading day of new month)
    prior_month_high: float
    prior_month_low: float
    prior_month_hcom: float         # Highest Close of Month (HCOM)
    prior_month_lcom: float         # Lowest Close of Month (LCOM)

    # Current week (builds as week progresses)
    week_open: float                # Monday's open price
    current_week_high: float        # Rolling — updates each day
    current_week_low: float         # Rolling — updates each day
    current_hcow: float             # Highest close so far this week
    current_lcow: float             # Lowest close so far this week

    # Month context
    month_open: float               # First close of the calendar month
    days_into_month: int            # 1–23 approx trading days

    # FDTM — First Day of Trading Month (its own anchor, distinct from month_open)
    # Establishes the OPEN/HIGH/LOW/CLOSE of the new month's first candle
    # This is the monthly equivalent of Monday's opening range
    fdtm_open: float | None         # FDTM open price (None until month starts)
    fdtm_high: float | None         # FDTM high
    fdtm_low: float | None          # FDTM low
    fdtm_close: float | None        # FDTM close = month_open
    fdtm_complete: bool             # True once first trading day of month has closed

    # Monthly Front/Back Side context
    monthly_phase: str              # "FRONTSIDE" | "BACKSIDE" | "RESET"
                                    # FRONTSIDE = Week 1–2 of month
                                    # BACKSIDE  = Week 3–4 of month
                                    # RESET     = New month just started (Day 1–3)
```

**Quarter-snap all anchor levels** using `snap_to_quarter()` before storing — these are the magnet levels price will gravitate toward and the system uses as target validators.

---

## Opening Range (Weekly Initial Balance)

> **Not the same as the intraday Initial Balance (IB).**  
> The Opening Range is the combined high/low of **Monday and Tuesday** — the first two days of the week. It defines the weekly "box" that the front side is building. The backside trade fades or breaks out of this range.

```python
@dataclass
class OpeningRange:
    high: float             # Highest HIGH of Monday + Tuesday combined
    low: float              # Lowest LOW of Monday + Tuesday combined
    size_pips: float        # high - low in pips — the weekly box size
    midpoint: float         # 50% of the range — first bounce / pullback target
    target_100pct_up: float # high + size_pips — 100% expansion upward
    target_100pct_dn: float # low  - size_pips — 100% expansion downward
    target_200pct_up: float # high + (2 × size_pips)
    target_200pct_dn: float # low  - (2 × size_pips)
    complete: bool          # True once Tuesday NY close is available

def compute_opening_range(daily_ohlcv: pd.DataFrame, current_week: int) -> OpeningRange:
    """
    Computed at Tuesday NY close. Not available on Monday.
    All targets are quarter-snapped before storing.
    """
    week_rows = daily_ohlcv[daily_ohlcv['date'].dt.isocalendar().week == current_week]
    mon_tue   = week_rows.iloc[:2]          # First two rows = Monday and Tuesday

    if len(mon_tue) < 2:
        return OpeningRange(complete=False, ...)    # Monday only — not yet available

    or_high = mon_tue['high'].max()
    or_low  = mon_tue['low'].min()
    size    = or_high - or_low

    return OpeningRange(
        high             = snap_to_quarter(or_high, pair),
        low              = snap_to_quarter(or_low,  pair),
        size_pips        = size / get_pip(pair),
        midpoint         = snap_to_quarter((or_high + or_low) / 2, pair),
        target_100pct_up = snap_to_quarter(or_high + size, pair),
        target_100pct_dn = snap_to_quarter(or_low  - size, pair),
        target_200pct_up = snap_to_quarter(or_high + (2 * size), pair),
        target_200pct_dn = snap_to_quarter(or_low  - (2 * size), pair),
        complete         = True
    )
```

**Minimum Opening Range size:** 40 pips. If Mon–Tue range < 40 pips, the week is dead/ranging — low probability for any backside setup. Log as `TIGHT_OPENING_RANGE` and reduce position size if a setup does trigger.

---

## Step 2 — Map the Day-of-Week Role

Each day has a primary role and a secondary role. The system uses these to weight setup probability.

| Day | Primary Role | Secondary Role | Setup Bias |
|---|---|---|---|
| **Monday** | Opening Range / Signal Day | New month breakout if Day 1 of month | Watch — do not force |
| **Tuesday** | Front Side Day 2 | Continuation or first pullback | Front side entry if streak = 2 |
| **Wednesday** | Pivot Day | Front side exhaustion OR back side begins | Highest setup probability day |
| **Thursday** | Back Side Day 1 | First Red/Green Day setups | High — fade the front side |
| **Friday** | Exit Day | Close open positions | NO new entries — exits only |

```python
def get_day_role(day: str, close_streak: int, week_template: WeeklyTemplate) -> DayRole:
    roles = {
        "MON": DayRole(primary="OPENING_RANGE", entry_bias="WAIT"),
        "TUE": DayRole(primary="FRONT_SIDE_DAY2", entry_bias="FRONT_SIDE" if abs(close_streak) >= 2 else "WAIT"),
        "WED": DayRole(primary="PIVOT", entry_bias="FRONT_SIDE" if abs(close_streak) <= 2 else "BACK_SIDE"),
        "THU": DayRole(primary="BACK_SIDE_DAY1", entry_bias="BACK_SIDE"),
        "FRI": DayRole(primary="EXIT_ONLY", entry_bias="NO_ENTRY"),
    }
    return roles[day]
```

---

## Step 3 — Identify the Weekly Template Type

At Wednesday's NY close, the system can classify which template the week is following. This determines which setups are valid for Thursday–Friday.

### Template A: BREAKOUT WEEK

```
Conditions:
  - Monday OR Tuesday closes beyond prior_week_high or prior_week_low
  - close_streak ≥ 2 by Wednesday close
  - Wednesday close extends the streak (does NOT reverse)

Implication:
  - High probability of continuation into Thursday
  - Thursday = Trend continuation entry (front side still running) OR
    First Red/Green Day if Wednesday was an exhaustion candle
  - Friday target: prior_week_hcow / prior_week_lcow extended by 100% measured move
```

### Template B: REVERSAL WEEK (Pump & Dump / Dump & Pump)

```
Conditions:
  - Monday–Tuesday pump in one direction (close_streak = +2 or -2)
  - Wednesday prints FIRST RED/GREEN DAY (reverses the streak)
  - Wednesday close is in opposite direction to Monday–Tuesday

Implication:
  - Wednesday IS the entry day (First Red/Green Day trigger)
  - Thursday = trailer management + potential scale-in on 50% pullback
  - Friday = close all positions, take profit
  - This is the highest-probability weekly template — "month money" candidate
```

### Template C: RANGING WEEK (The Box)

```
Conditions:
  - Monday–Wednesday all close within prior_week_high / prior_week_low range
  - No close_streak > 1 in either direction
  - Box size (current_week_high - current_week_low) ≤ 1.5 × ATR14

Implication:
  - No directional setup this week
  - System outputs: WAIT
  - Watch for Thursday/Friday breakout of the box as a potential trigger for NEXT week
  - Log the box boundaries as next week's anchor breakout levels
```

### Template D: NEW MONTH BREAKOUT

```
Conditions:
  - Current week contains the first 1–3 trading days of a new calendar month
  - Monday or Tuesday closes beyond prior_month_hcom or prior_month_lcom

Implication:
  - Highest conviction breakout signal of any template
  - A new month opening with a breakout close is the ACB setup — "load the boat"
  - Targets: 100%, 200%, 300% expansion from the monthly open level
  - This is the setup Burke refers to as the best opportunity in any given month
```

---

## Monthly Front/Back Side Cycle

Burke explicitly maps the month into two phases mirroring the weekly structure. This context filters which setup types are valid.

```python
def get_monthly_phase(days_into_month: int) -> str:
    """
    Monthly cycle mapping per the ACB playbook:
      RESET     → Days 1–3   (new timing cycle beginning, FDTM being established)
      FRONTSIDE → Days 4–10  (approx Week 1–2, building the monthly box)
      BACKSIDE  → Days 11+   (approx Week 3–4, hunting reversals from HOM/LOM)

    Note: days_into_month counts TRADING days, not calendar days.
    """
    if days_into_month <= 3:
        return "RESET"
    elif days_into_month <= 10:
        return "FRONTSIDE"
    else:
        return "BACKSIDE"
```

**Monthly phase affects setup bias:**

| Monthly Phase | Setup Bias | Notes |
|---|---|---|
| RESET (Day 1–3) | Watch only | FDTM being established — no trades until CIB confirmed |
| FRONTSIDE (Wk 1–2) | Trend/breakout entries | 3HC/3LC building — ride the breakout direction |
| BACKSIDE (Wk 3–4) | Reversal entries (HCOM/LCOM) | Hunt FRD/FGD from monthly extremes |

---

## 3HC / 3LC Countdown (1-2-Signal Day)

Once a CIB is detected on the watchlist, the system begins a countdown tracking consecutive higher/lower closes toward the signal day.

```python
@dataclass
class CloseCountdown:
    pair: str
    direction: str          # "3HC" (three higher closes) | "3LC" (three lower closes)
    count: int              # Current count: 1, 2, or 3+
    label: str              # "DAY_1" | "DAY_2" | "SIGNAL_DAY"
    at_hcom_lcom: bool      # True if current close is AT or near HCOM/LCOM level
    at_hom_lom: bool        # True if close is near HOM/LOM (intraday monthly extreme)
    signal_ready: bool      # True when count >= 2 AND next day is valid for FRD/FGD/Inside

def compute_close_countdown(daily_ohlcv: pd.DataFrame, cib_direction: str) -> CloseCountdown:
    """
    After a CIB, count consecutive closes in the CIB direction.
    Day 1 = first close in breakout direction
    Day 2 = second consecutive close
    Signal Day = Day 2 or Day 3 + a FRD/FGD/Inside Day pattern
                 OR: Day 3+ close at HCOM/LCOM = setup is ready

    Per the playbook summary (page 40):
    1) 3HC/3LC breaking out of monthly range
    2) 3HC/3LC INSIDE monthly range with FRD/FGD at HCOM/LCOM
    3) CIB AS the HCOM/LCOM itself
    All three variations are valid — signal_ready flags all of them.
    """
    streak = compute_close_streak(daily_ohlcv['close'])
    direction_matches = (
        (cib_direction == "BULLISH" and streak > 0) or
        (cib_direction == "BEARISH" and streak < 0)
    )

    count = abs(streak) if direction_matches else 0
    label = "DAY_1" if count == 1 else ("DAY_2" if count == 2 else "SIGNAL_DAY" if count >= 3 else "NONE")

    return CloseCountdown(
        pair         = daily_ohlcv['pair'].iloc[-1],
        direction    = "3HC" if cib_direction == "BULLISH" else "3LC",
        count        = count,
        label        = label,
        at_hcom_lcom = is_near_hcom_lcom(daily_ohlcv),
        at_hom_lom   = is_near_hom_lom(daily_ohlcv),
        signal_ready = count >= 2
    )
```

---

```python
def classify_weekly_template(
    anchors: WeeklyAnchors,
    daily_closes: list[float],      # Mon–Wed closes so far
    close_streak: int,
    atr14: float,
    days_into_month: int
) -> str:
    """Returns: "BREAKOUT_WEEK" | "REVERSAL_WEEK" | "RANGING_WEEK" | "NEW_MONTH_BREAKOUT" """
```

---

## Step 4 — Lock the High/Low of the Week

From Wednesday onward, track whether the weekly high or low appears to be "locked in." A locked high/low means the back side is active and the system should be looking for fades, not continuations.

```python
def is_high_locked(anchors: WeeklyAnchors, current_day: str, close_streak: int) -> bool:
    """
    High is considered locked when:
    - current_week_high was set on Monday or Tuesday AND
    - Wednesday (or later) close is BELOW Monday's or Tuesday's close AND
    - close_streak has flipped negative (at least one lower close)
    """

def is_low_locked(anchors: WeeklyAnchors, current_day: str, close_streak: int) -> bool:
    """Inverse of above."""
```

**When high is locked:** Only SHORT setups are valid for the remainder of the week.  
**When low is locked:** Only LONG setups are valid for the remainder of the week.  
**Neither locked (Wednesday):** Both directions possible — use `classify_weekly_template()` to decide.

---

## Step 5 — Validate Setup Against Weekly Template

Before passing any `Setup` object to the execution layer, it must pass the weekly template gate.

```python
def validate_setup_vs_template(
    setup: Setup,
    template: WeeklyTemplate,
    anchors: WeeklyAnchors,
    current_day: str
) -> tuple[bool, str]:
    """
    Returns (is_valid, rejection_reason).

    Rejection conditions:
    - current_day == "FRI"                          → "EXIT_ONLY_DAY"
    - template.type == "RANGING_WEEK"               → "NO_DIRECTIONAL_TEMPLATE"
    - setup.direction == "LONG" and high_is_locked  → "HIGH_LOCKED_SHORTS_ONLY"
    - setup.direction == "SHORT" and low_is_locked  → "LOW_LOCKED_LONGS_ONLY"
    - setup.pattern == "FIRST_RED_DAY" and
      current_day == "MON" or "TUE"                 → "TOO_EARLY_FOR_BACKSIDE"
    - setup.entry_price not near weekly anchor level → "NO_ANCHOR_CONFLUENCE"
    """
```

### Anchor Confluence Check

A setup's entry price must be within **25 pips** of at least one weekly anchor level to pass. This ensures entries are at meaningful levels, not in the middle of nowhere.

```python
def has_anchor_confluence(entry_price: float, anchors: WeeklyAnchors, pip: float) -> bool:
    anchor_levels = [
        anchors.prior_week_high, anchors.prior_week_low,
        anchors.prior_week_hcow, anchors.prior_week_lcow,
        anchors.prior_month_high, anchors.prior_month_low,
        anchors.current_week_high, anchors.current_week_low,
        anchors.month_open
    ]
    return any(abs(entry_price - level) <= 25 * pip for level in anchor_levels)
```

---

## Step 6 — Select the Cleanest Pair in the Basket

Once all pairs have been templated and setups validated, rank by template quality and select the top candidate per basket.

```python
TEMPLATE_PRIORITY = {
    "NEW_MONTH_BREAKOUT": 4,
    "REVERSAL_WEEK": 3,
    "BREAKOUT_WEEK": 2,
    "RANGING_WEEK": 0,          # Excluded
}

def select_best_pair(
    validated_setups: list[tuple[str, Setup, WeeklyTemplate]],
    basket: list[str]
) -> Setup | None:
    """
    Filters to basket pairs only.
    Ranks by: template_priority → setup score → tightest stop distance.
    Returns top-ranked Setup, or None if no valid setups exist.
    """
```

**One trade per basket per day.** If the top pair already has an open position, move to the next ranked pair. Never open two positions in correlated pairs simultaneously.

---

## Output Contract

```python
@dataclass
class WeeklyTemplate:
    pair: str
    week_number: int
    template_type: str              # BREAKOUT_WEEK | REVERSAL_WEEK | RANGING_WEEK | NEW_MONTH_BREAKOUT
    anchors: WeeklyAnchors
    day_role: DayRole
    high_locked: bool
    low_locked: bool
    valid_directions: list[str]     # ["LONG"] | ["SHORT"] | ["LONG", "SHORT"] | []
    best_setup_day: str             # Predicted highest-probability day: "WED" | "THU" etc.
    template_confidence: str        # "HIGH" | "MEDIUM" | "LOW"
    notes: str                      # Human-readable context e.g. "Day 2 of new month breakout"
    monthly_phase: str              # "RESET" | "FRONTSIDE" | "BACKSIDE"
    close_countdown: CloseCountdown # 3HC/3LC countdown state
    fdtm: dict | None               # FDTM OHLC if in new month, else None
    generated_at: datetime          # NY close timestamp
```

---

## Weekly Template Cheat Sheet (Human-Readable)

For the Telegram daily briefing output:

```
📅 WEEKLY TEMPLATE — EURUSD — Week 13
Template:    REVERSAL_WEEK (Pump & Dump)
Day Role:    THURSDAY — Back Side Day 1
High Locked: ✅ YES (set Tuesday 1.09245)
Low Locked:  ❌ NO
Valid Dir:   SHORT only

Anchors:
  Prior Week H/L:  1.09312 / 1.08750
  Prior Week HCOW: 1.09180
  Month Open:      1.08900

Setup Bias:  First Red Day SHORT
Best Entry Zone: 1.09150–1.09175 (near HCOW + quarter level)
Target 1:    1.08900 (month open, 100% measured move)
Target 2:    1.08650 (200% expansion)
```


