"""
tests/test_patterns_and_scoring.py
────────────────────────────────────
Unit tests for:
  - acb_trader.signals.patterns  (pattern library)
  - acb_trader.signals._scoring  (scoring hook)

Run with:   pytest acb_trader/tests/test_patterns_and_scoring.py -v
"""

import pytest
from unittest.mock import MagicMock
from datetime import date

# ── Pattern library tests ─────────────────────────────────────────────────────

from acb_trader.signals.patterns import (
    PATTERN, ALL_PATTERN_NAMES, MONITOR_ONLY_PATTERNS, RELAXED_RR_PATTERNS,
    get_score_bonus, get_rr_floor, get_min_score, is_monitor_only,
)


class TestPatternLibrary:

    def test_all_six_patterns_registered(self):
        expected = {
            "PUMP_COIL_DUMP", "FIRST_RED_DAY", "FIRST_GREEN_DAY",
            "INSIDE_FALSE_BREAK", "PARABOLIC_REVERSAL",
            "MONDAY_FALSE_BREAK", "LOW_HANGING_FRUIT",
        }
        assert expected == ALL_PATTERN_NAMES

    def test_frd_fgd_score_bonus_is_2(self):
        assert get_score_bonus("FIRST_RED_DAY") == 2
        assert get_score_bonus("FIRST_GREEN_DAY") == 2

    def test_frd_fgd_rr_floor_relaxed(self):
        assert get_rr_floor("FIRST_RED_DAY") == 1.0
        assert get_rr_floor("FIRST_GREEN_DAY") == 1.0

    def test_lhf_score_bonus_is_1(self):
        assert get_score_bonus("LOW_HANGING_FRUIT") == 1

    def test_pcd_has_no_direct_bonus(self):
        assert get_score_bonus("PUMP_COIL_DUMP") == 0

    def test_mfb_score_bonus_is_2(self):
        assert get_score_bonus("MONDAY_FALSE_BREAK") == 2

    def test_para_is_monitor_only(self):
        assert is_monitor_only("PARABOLIC_REVERSAL") is True

    def test_non_para_not_monitor_only(self):
        for name in ALL_PATTERN_NAMES - {"PARABOLIC_REVERSAL", "PUMP_COIL_DUMP"}:
            assert is_monitor_only(name) is False, f"{name} should not be monitor-only"

    def test_min_score_all_patterns_is_7(self):
        for name in ALL_PATTERN_NAMES:
            assert get_min_score(name) == 7

    def test_unknown_pattern_graceful_defaults(self):
        assert get_score_bonus("NONEXISTENT") == 0
        assert get_rr_floor("NONEXISTENT") == 2.0
        assert get_min_score("NONEXISTENT") == 7
        assert is_monitor_only("NONEXISTENT") is False

    def test_monitor_only_set_matches_config(self):
        """MONITOR_ONLY_PATTERNS in patterns.py should match MONITOR_ONLY_PATTERNS in config."""
        import acb_trader.config as cfg
        assert MONITOR_ONLY_PATTERNS == cfg.MONITOR_ONLY_PATTERNS

    def test_all_patterns_have_description(self):
        for name, p in PATTERN.items():
            assert p.description, f"{name} has empty description"


# ── Scoring hook tests ────────────────────────────────────────────────────────

from acb_trader.signals._scoring import score_setup, ScoreBreakdown


def _make_setup(
    pattern="FIRST_RED_DAY",
    entry=1.08500,
    stop=1.08750,
    t1=1.08000,
    pair="EURUSD",
    signal_date=date(2025, 3, 26),   # Wednesday → +2
    risk_pips=25.0,
):
    s = MagicMock()
    s.pattern = pattern
    s.entry_price = entry
    s.stop_price = stop
    s.target_1 = t1
    s.pair = pair
    s.signal_date = signal_date
    s.risk_pips = risk_pips
    s.score = 0
    return s


def _make_state(pair="EURUSD", state="BREAKOUT", atr14=0.0100):
    ms = MagicMock()
    ms.pair = pair
    ms.state = state
    ms.atr14 = atr14
    return ms


def _make_template(monthly_phase="FRONTSIDE", countdown_label="NOT_YET"):
    anchors = MagicMock()
    anchors.monthly_phase = monthly_phase
    anchors.prior_week_high = 1.09000
    anchors.prior_week_low  = 1.07500
    anchors.prior_week_hcow = 1.08900
    anchors.prior_week_lcow = 1.07600
    anchors.current_week_high = 1.08800
    anchors.current_week_low  = 1.07700
    anchors.month_open = 1.08000   # same as T1 → anchor confluence hit
    anchors.prior_month_hcom = 0.0
    anchors.prior_month_lcom = 0.0
    anchors.current_hcow = 0.0
    anchors.current_lcow = 0.0

    countdown = MagicMock()
    countdown.label = countdown_label

    t = MagicMock()
    t.anchors = anchors
    t.close_countdown = countdown
    return t


class TestScoringHook:

    def test_returns_score_breakdown(self):
        bd = score_setup(_make_setup(), _make_state(), _make_template(), ema_coil=False)
        assert isinstance(bd, ScoreBreakdown)

    def test_total_capped_at_14(self):
        # Give everything possible: Wed, BREAKOUT, tight stop, 3:1 RR,
        # 2+ anchors, EMA coil, HCOW/LCOW, FRD bonus, backside, signal_day
        bd = score_setup(
            _make_setup(risk_pips=3.0),   # tiny stop → tight stop bonus + high RR
            _make_state(state="BREAKOUT", atr14=0.0100),
            _make_template(monthly_phase="BACKSIDE", countdown_label="SIGNAL_DAY"),
            ema_coil=True,
        )
        assert bd.total <= 14

    def test_wed_thu_bonus_fires_on_wednesday(self):
        bd = score_setup(
            _make_setup(signal_date=date(2025, 3, 26)),  # Wednesday
            _make_state(), _make_template(), ema_coil=False,
        )
        assert bd.wed_thu_signal == 2

    def test_wed_thu_bonus_fires_on_thursday(self):
        bd = score_setup(
            _make_setup(signal_date=date(2025, 3, 27)),  # Thursday
            _make_state(), _make_template(), ema_coil=False,
        )
        assert bd.wed_thu_signal == 2

    def test_wed_thu_bonus_absent_on_tuesday(self):
        bd = score_setup(
            _make_setup(signal_date=date(2025, 3, 25)),  # Tuesday
            _make_state(), _make_template(), ema_coil=False,
        )
        assert bd.wed_thu_signal == 0

    def test_frd_pattern_bonus_included(self):
        bd = score_setup(_make_setup(pattern="FIRST_RED_DAY"),
                         _make_state(), _make_template(), ema_coil=False)
        assert bd.pattern_bonus == 2

    def test_fgd_pattern_bonus_included(self):
        bd = score_setup(_make_setup(pattern="FIRST_GREEN_DAY"),
                         _make_state(), _make_template(), ema_coil=False)
        assert bd.pattern_bonus == 2

    def test_lhf_pattern_bonus_is_1(self):
        bd = score_setup(_make_setup(pattern="LOW_HANGING_FRUIT"),
                         _make_state(), _make_template(), ema_coil=False)
        assert bd.pattern_bonus == 1

    def test_ema_coil_adds_2(self):
        without = score_setup(_make_setup(), _make_state(), _make_template(), ema_coil=False)
        with_   = score_setup(_make_setup(), _make_state(), _make_template(), ema_coil=True)
        assert with_.ema_coil == 2
        assert with_.total == without.total + 2

    def test_backside_adds_1(self):
        front = score_setup(_make_setup(), _make_state(),
                            _make_template(monthly_phase="FRONTSIDE"), ema_coil=False)
        back  = score_setup(_make_setup(), _make_state(),
                            _make_template(monthly_phase="BACKSIDE"), ema_coil=False)
        assert back.backside_phase == 1
        assert back.total == front.total + 1

    def test_signal_day_label_adds_1(self):
        no  = score_setup(_make_setup(), _make_state(),
                          _make_template(countdown_label="NOT_YET"), ema_coil=False)
        yes = score_setup(_make_setup(), _make_state(),
                          _make_template(countdown_label="SIGNAL_DAY"), ema_coil=False)
        assert yes.signal_day_label == 1
        assert yes.total == no.total + 1

    def test_breakdown_lines_non_empty(self):
        bd = score_setup(_make_setup(), _make_state(), _make_template(), ema_coil=False)
        lines = bd.lines()
        assert any("TOTAL" in l for l in lines)
        assert any("+2" in l or "+1" in l for l in lines)

    def test_string_signal_date_parsed(self):
        s = _make_setup()
        s.signal_date = "2025-03-26"   # Wednesday as string
        bd = score_setup(s, _make_state(), _make_template(), ema_coil=False)
        assert bd.wed_thu_signal == 2
