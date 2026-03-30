"""
acb_trader/signals/_scoring.py
──────────────────────────────
Standalone scoring hook — drop-in replacement for the _score() block
currently inlined in setups.py.

Import and call score_setup() from detect_setups() instead of the
inline _score() function. The logic is identical to the existing
implementation but now sources all pattern metadata from patterns.py.

Changes vs. inlined _score()
─────────────────────────────
1. Pattern score bonuses come from PATTERN[name].score_bonus — no more
   scattered `if setup.pattern == "X": score += N` literals.
2. R:R floor and min-score gate are also delegated to the PatternDef,
   so adding Pattern 7+ only requires editing patterns.py, not setups.py.
3. A ScoreBreakdown dataclass is returned alongside the integer score,
   enabling detailed Telegram alerts and backtest diagnostics without
   re-running the scorer.

Public API
──────────
    from acb_trader.signals._scoring import score_setup, ScoreBreakdown

    breakdown = score_setup(setup, state, template, ema_coil)
    setup.score = breakdown.total
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime

from acb_trader.db.models import Setup, MarketState, WeeklyTemplate
from acb_trader.data.levels import price_to_pips, get_pip_size
from acb_trader.signals.patterns import PATTERN, get_score_bonus
import acb_trader.config as cfg


# ── SCORE BREAKDOWN ───────────────────────────────────────────────────────────

@dataclass
class ScoreBreakdown:
    """Detailed record of how each point was awarded."""
    total:              int   = 0

    # Contextual criteria (+2 each)
    wed_thu_signal:     int   = 0   # signal day is Wednesday or Thursday
    breakout_state:     int   = 0   # market state == BREAKOUT
    tight_stop:         int   = 0   # stop ≤ 0.5× ATR14
    rr_3to1:            int   = 0   # R:R ≥ 3:1 to T1
    anchor_confluence:  int   = 0   # entry within 50 pips of 2+ anchor levels
    ema_coil:           int   = 0   # EMA coil confirmed on 4H chart
    near_hcow_lcow:     int   = 0   # entry within 25 pips of current HCOW/LCOW

    # Pattern bonus (sourced from PatternDef.score_bonus)
    pattern_bonus:      int   = 0

    # Extra PARABOLIC stack (+2 when near HCOW/LCOW AND pattern == PARA)
    para_level_stack:   int   = 0

    # Structural decoupled bonuses (+2 each — calendar-independent)
    frd_fgd_structural: int   = 0   # FRD/FGD: prior 3HC/3LC streak confirmed (not tied to Wed/Thu)
    mfb_structural:     int   = 0   # MFB: Monday locked HOW/LOW — Tue entry equally valid as Thu

    # Three-box exhaustion (+2 when entry aligns with 3-box grid exhaustion zone)
    three_box_exhaustion: int = 0   # price drove through 3 institutional boxes → trap is BUILT

    # Minor bonuses (+1 each)
    backside_phase:     int   = 0   # monthly phase == BACKSIDE
    signal_day_label:   int   = 0   # 3HC/3LC countdown label == SIGNAL_DAY

    def lines(self) -> list[str]:
        """Human-readable breakdown for Telegram / logs."""
        rows = []
        mapping = [
            ("Wed/Thu signal day",    self.wed_thu_signal),
            ("BREAKOUT state",        self.breakout_state),
            ("Tight stop ≤0.5×ATR",   self.tight_stop),
            ("R:R ≥ 3:1",             self.rr_3to1),
            ("2+ anchor confluences", self.anchor_confluence),
            ("EMA coil (4H)",         self.ema_coil),
            ("Near HCOW/LCOW",        self.near_hcow_lcow),
            ("Pattern bonus",         self.pattern_bonus),
            ("PARA level stack",      self.para_level_stack),
            ("FRD/FGD structural",    self.frd_fgd_structural),
            ("MFB structural",        self.mfb_structural),
            ("3-box exhaustion",      self.three_box_exhaustion),
            ("Backside phase",        self.backside_phase),
            ("3HC/3LC SIGNAL_DAY",    self.signal_day_label),
        ]
        for label, pts in mapping:
            if pts:
                rows.append(f"  +{pts}  {label}")
        rows.append(f"  ─────────────")
        rows.append(f"  {self.total}/14  TOTAL")
        return rows


# ── HELPERS (mirrors setups.py private helpers) ───────────────────────────────

def _is_near_hcom_lcom(price: float, anchors, pair: str) -> bool:
    pip = get_pip_size(pair)
    hcom = anchors.current_hcow
    lcom = anchors.current_lcow
    return (
        (hcom > 0 and abs(price - hcom) <= 25 * pip) or
        (lcom > 0 and abs(price - lcom) <= 25 * pip)
    )


def _count_anchor_confluences(price: float, anchors, pair: str) -> int:
    pip = get_pip_size(pair)
    levels = [
        anchors.prior_week_high, anchors.prior_week_low,
        anchors.prior_week_hcow, anchors.prior_week_lcow,
        anchors.current_week_high, anchors.current_week_low,
        anchors.month_open, anchors.prior_month_hcom, anchors.prior_month_lcom,
    ]
    return sum(
        1 for lvl in levels
        if lvl > 0 and abs(price - lvl) <= cfg.ANCHOR_CONFLUENCE_PIPS * pip
    )


# ── MAIN SCORING FUNCTION ─────────────────────────────────────────────────────

def score_setup(
    setup: Setup,
    state: MarketState,
    template: WeeklyTemplate,
    ema_coil: bool,
) -> ScoreBreakdown:
    """
    Score a Setup and return a ScoreBreakdown (which includes .total).

    Caller should assign:   setup.score = breakdown.total
    """
    bd = ScoreBreakdown()

    # Resolve signal date day-of-week
    if isinstance(setup.signal_date, str):
        sig_date = datetime.strptime(setup.signal_date, "%Y-%m-%d").date()
    else:
        sig_date = setup.signal_date
    dow = sig_date.weekday()   # 0=Mon … 4=Fri

    # ── Contextual criteria ───────────────────────────────────────────────────

    if dow in (2, 3):                                              # Wed / Thu
        bd.wed_thu_signal = 2

    if state.state == "BREAKOUT":
        bd.breakout_state = 2

    atr14 = state.atr14
    if setup.risk_pips <= 0.5 * price_to_pips(atr14, setup.pair):
        bd.tight_stop = 2

    t1_dist = price_to_pips(abs(setup.target_1 - setup.entry_price), setup.pair)
    rr = t1_dist / (setup.risk_pips or 1)
    if rr >= 3.0:
        bd.rr_3to1 = 2

    if _count_anchor_confluences(setup.entry_price, template.anchors, setup.pair) >= 2:
        bd.anchor_confluence = 2

    if ema_coil:
        bd.ema_coil = 2

    if _is_near_hcom_lcom(setup.entry_price, template.anchors, setup.pair):
        bd.near_hcow_lcow = 2

    # ── Pattern bonus (from PatternDef) ───────────────────────────────────────

    bd.pattern_bonus = get_score_bonus(setup.pattern)

    # PARABOLIC_REVERSAL stacks an extra +2 when also near HCOW/LCOW
    if (
        setup.pattern == "PARABOLIC_REVERSAL"
        and _is_near_hcom_lcom(setup.entry_price, template.anchors, setup.pair)
    ):
        bd.para_level_stack = 2

    # ── Structural decoupled bonuses ─────────────────────────────────────────────
    # These bonuses fire based on pattern geometry, NOT calendar day.
    #
    # FRD/FGD structural: The Three-Day Rule prerequisite (prior_streak >= 3) IS
    # the edge — trapped breakout traders are committed regardless of weekday.
    # A Tuesday FGD after Fri-Mon-Tue downtrend has identical fuel to a Thursday
    # FRD.  Decouples +2 from the wed_thu_signal bonus (which still stacks on
    # top when the setup fires on Wed/Thu for maximum alignment confirmation).
    if setup.pattern in ("FIRST_RED_DAY", "FIRST_GREEN_DAY"):
        bd.frd_fgd_structural = 2

    # MFB structural: Monday sets the weekly opening range trap — the HOW/LOW is
    # locked from Day 1.  A Tuesday entry is still the first valid confirmation
    # bar.  Granting the structural equivalent of the wed_thu_signal bonus it
    # deserves but cannot receive by calendar definition.
    if setup.pattern == "MONDAY_FALSE_BREAK":
        bd.mfb_structural = 2

    # ── Three-box exhaustion bonus ───────────────────────────────────────────
    # When the setup's entry price aligns with a 3-box grid exhaustion level
    # (price has traversed 3 consecutive 25-pip boxes from the anchor and
    # stalled at a Major Round Number or Quarter level), the trap is fully built.
    # +2 because the institutional "Pump" or "Dump" has exhausted itself —
    # breakout traders are hopelessly trapped at the extreme.
    _tba = getattr(setup, '_three_box_analysis', None)
    if _tba is not None and _tba.at_exhaustion and _tba.boxes_completed >= 3:
        bd.three_box_exhaustion = 2

    # ── Minor bonuses ─────────────────────────────────────────────────────────

    if template.monthly_phase == "BACKSIDE":
        bd.backside_phase = 1

    if template.close_countdown.label == "SIGNAL_DAY":
        bd.signal_day_label = 1

    # ── Cap and write total ───────────────────────────────────────────────────

    raw = (
        bd.wed_thu_signal
        + bd.breakout_state
        + bd.tight_stop
        + bd.rr_3to1
        + bd.anchor_confluence
        + bd.ema_coil
        + bd.near_hcow_lcow
        + bd.pattern_bonus
        + bd.para_level_stack
        + bd.frd_fgd_structural
        + bd.mfb_structural
        + bd.three_box_exhaustion
        + bd.backside_phase
        + bd.signal_day_label
    )
    bd.total = min(raw, 14)
    return bd
