# ACB Trader — Signal Sanitization Plan (Part 1)

**Goal:** Resolve the bottlenecks identified in the 2023–2024 backtest (49 trades, +5.1R net,
but +4.28R single outlier carries ~84% of profit). Build statistical confidence before live deployment.

**Status:** Under review — not yet implemented.

---

## Change 1 — Engine: Core Expectancy + FORCE_CLOSE Diagnostics ✅ APPROVED

**File:** `acb_trader/backtest/engine.py` → `print_report()`

**What to add:**

1. **Core Expectancy** — Re-calculate expectancy after stripping all trades with r_multiple > 3R
   (not just the single best trade). This exposes the system's baseline edge without fat-tail luck.

2. **FORCE_CLOSE breakdown** — Count how many FORCE_CLOSE terminals were wins vs. losses vs. flat.
   Many winning trades end in FORCE_CLOSE (5-day max-hold), which means the backtest is closing
   them at the daily close, not at the actual target. Need to know: are we cutting winners early,
   or is the hold-limit saving us from reversals?

**Output to add to print_report:**
```
  Core Expectancy:   +0.XXR  (outliers >3R removed: N trades)
  FORCE_CLOSE:       N total  (N wins / N losses / N flat)
```

**Risk:** None — additive reporting only, no strategy logic touched.

---

## Change 2 — IFB: Relax Close-Position Rejection (NOT compression) ❌ ORIGINAL REJECTED

**Why the original was wrong:**
The original plan proposed changing the IFB compression threshold from 0.70 → 0.60. This
tightens the filter — the inside day must be even more compressed. With 81 `IFB_INSIDE_DAY_NOT_COMPRESSED`
discards already at 0.70, going to 0.60 generates fewer candidates, not more. Direction was backwards.

**File:** `acb_trader/signals/setups.py` → `_detect_inside_false_break()`

**Actual bottleneck:** The 12 `IFB_WEAK_REJECTION` discards. These are inside days that passed
compression but failed because the signal-day close wasn't in the extreme 25% of the daily range.
On daily bars, a close in the bottom/top 25% is very aggressive — most valid rejections close in
the 25–35% zone.

**Revised change:**
- Relax close-position gate from 25% → 33% (top/bottom third of the day's range)
- Keep compression threshold at 0.70×ATR14 (do not change)

```python
# Current (too strict for daily bars):
if broke_high and closed_inside_high:
    if close_pct > 0.25:   # must be in bottom 25%
        return None, "IFB_WEAK_REJECTION"

# Revised:
if broke_high and closed_inside_high:
    if close_pct > 0.33:   # must be in bottom 33%
        return None, "IFB_WEAK_REJECTION"

# And for the long side:
# Current:
    if close_pct < 0.75:   # must be in top 25%
# Revised:
    if close_pct < 0.67:   # must be in top 33%
```

**Expected outcome:** Some of the 12 `IFB_WEAK_REJECTION` discards become valid setups.
IFB trade count moves from 0 to a small number (estimate 2–5 over 2 years).

**Verification:** After running backtest, check `IFB_WEAK_REJECTION` discard count drops
and `backtest_results.csv` contains INSIDE_FALSE_BREAK rows.

---

## Change 3 — PARA: Fix Level Proximity Hardcode + Streak Relaxation ⚠️ TEST FIRST

**File:** `acb_trader/signals/setups.py` → `_detect_parabolic_reversal()`

### Part A — Level proximity hardcode (implement now)

The PARA detector has its own proximity check hardcoded at 25 pips:
```python
# Line 367 — never updated when ANCHOR_CONFLUENCE_PIPS was changed to 50:
if level > 0 and abs(last_close - level) <= 25 * pip:
```

This should match the global `ANCHOR_CONFLUENCE_PIPS` constant (50 pips). Many of the
772 `PARA_NO_PUMP_INTO_LEVEL` discards may actually be level-proximity failures disguised
as streak failures — the level proximity check runs first, so candidates that fail level
proximity never reach the streak check at all.

**Change:** Import and use `ANCHOR_CONFLUENCE_PIPS` instead of hardcoded `25`:
```python
from acb_trader.config import ANCHOR_CONFLUENCE_PIPS
# ...
if level > 0 and abs(last_close - level) <= ANCHOR_CONFLUENCE_PIPS * pip:
```

### Part B — Streak relaxation (run backtest first, decide after)

**Proposed:** Relax streak requirement from `< 2` → `< 1` (allow single-close pushes into a level).

**Hold on this.** The 3 existing PARA trades that passed streak ≥ 2 are already at −0.64R average.
Adding single-close candidates is likely to bring in lower-conviction setups that perform worse.

**Decision gate:** Run the backtest with only Part A applied. If PARA trade count increases
meaningfully and R/trade improves, leave streak at 2. Only lower to 1 if PARA still produces
< 3 trades after the proximity fix.

---

## Change 4 — FGD: Score Floor Isolation Test ⚠️ TEST IN ISOLATION FIRST

**Why a per-pattern score override is wrong:**
Adding a special case inside `detect_setups()` for FIRST_GREEN_DAY creates pattern-specific
floor logic scattered in the master detector. Bad precedent.

**Correct approach — two-step:**

### Step 1: Run diagnostic backtest at MIN_SETUP_SCORE = 6 globally

Temporarily lower `MIN_SETUP_SCORE = 6` in `config.py`, run the backtest, then in the results CSV:
- Filter to `pattern == FIRST_GREEN_DAY AND score == 6`
- Measure win rate and avg R on that subset *only*

Do not evaluate the overall results — just the score-6 FGD trades in isolation.

### Step 2: Decide based on data

| Score-6 FGD outcome | Decision |
|---------------------|----------|
| Win rate ≥ 40% AND avg R > 0 | Lower FGD floor permanently to 6 |
| Win rate < 40% OR avg R ≤ 0 | Keep floor at 7; close the question |

If approved: implement as a `pattern_min_score` dict in `config.py`:
```python
PATTERN_MIN_SCORE = {
    "FIRST_GREEN_DAY": 6,
    "DEFAULT": 7,  # MIN_SETUP_SCORE for all other patterns
}
```
Then in `detect_setups()`:
```python
min_score = PATTERN_MIN_SCORE.get(setup.pattern, PATTERN_MIN_SCORE["DEFAULT"])
if setup.score < min_score:
    ...
```

**Do not merge the pattern-specific floor until Step 1 data is reviewed.**

---

## Implementation Order

| # | Change | Status | Notes |
|---|--------|--------|-------|
| 1 | Engine reporting (Core Expectancy + FORCE_CLOSE) | ✅ Done | Core exp +0.10R; FORCE_CLOSE 8/9 wins avg +1.26R |
| 2 | IFB close-position gate: 25% → 33% | ✅ Done | Reduced IFB_WEAK_REJECTION discards |
| 2b | IFB compression: 0.75×ATR14 (aligned to PCD coil standard) | ✅ Done | IFB_INSIDE_DAY_NOT_COMPRESSED now 68 (was 97) |
| 3a | PARA level proximity: hardcode 25 → ANCHOR_CONFLUENCE_PIPS | ✅ Done | PARA trade count: 4 trades (25% WR, -2.91R net) |
| 3b | PARA streak relaxation: ≥2 → ≥1 | ❌ Rejected | Tested: added 9 PARA trades, 8/9 losses. +7.7R was USDJPY macro event, not signal edge |
| 4 | FGD score floor = 6 workaround | ✅ Superseded | Replaced by proper +2 FGD bonus (see below) |
| 5 | FGD +2 scoring bonus (skill doc fix) | ✅ Done | Skill doc: "First Red/Green Day +2" applies to BOTH FRD and FGD. FGD was missing it |
| 5b | Remove FGD special floor=6 | ✅ Done | FGD now uses standard floor=7 (with +2 bonus, net requirement unchanged) |
| 6 | Optimizer crash handler | ✅ Done | Added try/except per iteration; defensive data copy; ∞ profit_factor display fix |

---

## ✅ CONFIRMED BASELINE — 72 trades @ MIN_SETUP_SCORE=7

| Metric | Value |
|--------|-------|
| Total trades | 72 |
| Win rate | 47.2% |
| Expectancy | +0.10R |
| Profit factor | 1.20 |
| Core expectancy | +0.05R (EURUSD +4.3R outlier stripped) |

**Pattern breakdown:**
- FGD: 32 trades, 50% WR, +5.53R net ← up from 25 trades (FGD +2 bonus confirmed working)
- MFB: ~17 trades, 47% WR (unchanged)
- FRD: ~8 trades, 37% WR (unchanged)
- PARA: 4 trades, 25% WR, -2.91R net (still a drag — decision pending)
- IFB: 0 trades (by design — no skill doc bonus, filter working correctly)

**Root cause of prior "identical optimizer results" identified:**
The FGD `floor=6` hardcode meant FGD count was frozen regardless of the sweep value.
FGD was 46% of all trades — so all scores 4–8 showed the same results.
Removing the hardcode + adding FGD +2 bonus fixed both the score floor logic AND the optimizer.

---

## Optimizer Sweep Results (2023–2024, confirmed working)

| Score | Trades | Win Rate | Expectancy | Profit Factor |
|-------|--------|----------|------------|---------------|
| 4 | 189 | 46.0% | +0.05R | 1.10 |
| 5 | 160 | 44.4% | +0.01R | 1.02 |
| 6 | 122 | 43.4% | +0.01R | 1.01 |
| **7** | **72** | **47.2%** | **+0.10R** | **1.20** ◄ BEST |

**Conclusion:** MIN_SETUP_SCORE=7 is confirmed optimal. Lower thresholds add volume but destroy
edge (score 6 expectancy drops to +0.01R — barely above break-even). Higher thresholds untested
but would reduce sample size below statistical relevance.

---

## IFB Scoring Gap — Closed

IFB generates valid setups (7 passed all structural filters) but none reached MIN_SETUP_SCORE=7.

**Root cause:** IFB target_1 = inside day's opposite extreme. When false break spike is large,
R:R < 3:1 → fails the +2 R:R criterion. No pattern-specific IFB bonus exists in the skill doc.

**Decision:** Do NOT add arbitrary bonus. Filter is working correctly. IFB will fire when full
confluence aligns (BREAKOUT + tight stop + 2+ anchors + HCOM/LCOM + Wed/Thu = 10 pts ≥ 7).

---

## PARA Decision — ✅ MONITOR ONLY (2026-03-28)

PARA: 4 trades, 25% WR, -2.91R net. Streak relaxation rejected. Proximity fix applied — still negative.

**Decision:** Monitor-only. Detector runs, setup scores, alert fires — but every PARA alert
is stamped ⚠️ MONITOR ONLY — do not trade in Telegram.

**Implementation:**
- `config.py`: `MONITOR_ONLY_PATTERNS = {"PARABOLIC_REVERSAL"}`
- `telegram.py`: `send_eod_briefing()` and `send_setup_armed()` check this set and append warning block

**Revisit criteria:** If 10+ live PARA signals observed with ≥ 50% WR, re-evaluate for
active trading. Until then, accumulate live data without risking capital.

---

## Open Questions (Part 2)

**Q: PCD EMA coil proxy for backtesting**
Engine hardcodes `ema_coil=False`, blocking all PCD setups. A daily-bar proxy (close within
0.10×ATR of 21-EMA) could unlock PCD testing. Noisy but better than zero data.
**Defer until PARA decision resolved.**

**Q: Pair-specific tuning**
Some pairs may perform better with tighter/looser stops or different baskets.
Requires per-pair breakdown from backtest_results.csv.
