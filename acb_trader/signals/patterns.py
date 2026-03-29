"""
acb_trader/signals/patterns.py
──────────────────────────────
Pattern Library — single source of truth for every ACB setup.

Each PatternDef carries:
  - name          : canonical string key used throughout the system
  - score_bonus   : points awarded in _score() for this pattern
  - rr_floor      : minimum R:R required (overrides global MIN_RR where lower)
  - min_score     : minimum total score to pass the quality gate
  - trade_type    : default trade tier (SESSION_TRADE or FIVE_STAR_SCALABLE)
  - monitor_only  : if True, arm alert fires but execution is blocked
  - description   : one-line human description (Telegram / logs)

Importing this module is the ONLY place pattern metadata should live.
Never hard-code a pattern name string outside of this file.

Usage
-----
    from acb_trader.signals.patterns import PATTERN, PatternDef

    p: PatternDef = PATTERN["FIRST_RED_DAY"]
    score += p.score_bonus
    if p.monitor_only:
        ...
"""

from __future__ import annotations
from dataclasses import dataclass, field


@dataclass(frozen=True)
class PatternDef:
    name:         str
    score_bonus:  int        # added in _score() when this pattern is detected
    rr_floor:     float      # minimum R:R to T1 (1.0 = 1:1, 2.0 = 2:1, etc.)
    min_score:    int        # quality gate; usually 7, may differ per pattern
    trade_type:   str        # "SESSION_TRADE" | "FIVE_STAR_SCALABLE"
    monitor_only: bool       # True → alert fires, no execution
    description:  str        # short human label


# ── PATTERN REGISTRY ─────────────────────────────────────────────────────────

_DEFS: list[PatternDef] = [
    PatternDef(
        name="PUMP_COIL_DUMP",
        score_bonus=0,          # no direct pattern bonus; earns points via other criteria
        rr_floor=2.0,
        min_score=7,
        trade_type="FIVE_STAR_SCALABLE",
        monitor_only=True,      # D1 displacement threshold unreachable; needs live data sample
        description="3HC/3LC close streak → coil → explosive breakdown/breakout",
    ),
    PatternDef(
        name="FIRST_RED_DAY",
        score_bonus=2,          # +2 per skill doc "First Red/Green Day"
        rr_floor=1.0,           # relaxed from 2:1 — FRD/FGD targets often produce 1–1.5R
        min_score=7,
        trade_type="SESSION_TRADE",
        monitor_only=False,
        description="First daily close against an established up-trend",
    ),
    PatternDef(
        name="FIRST_GREEN_DAY",
        score_bonus=2,          # mirrors FRD — skill doc applies to both
        rr_floor=1.0,
        min_score=7,
        trade_type="SESSION_TRADE",
        monitor_only=False,
        description="First daily close against an established down-trend",
    ),
    PatternDef(
        name="INSIDE_FALSE_BREAK",
        score_bonus=0,          # no direct bonus; quality comes from structural filters
        rr_floor=2.0,
        min_score=7,
        trade_type="SESSION_TRADE",
        monitor_only=False,
        description="Inside day whose range is broken, then price snaps back inside",
    ),
    PatternDef(
        name="PARABOLIC_REVERSAL",
        score_bonus=2,          # +2 only when ALSO near HCOW/LCOW (stacked in _score)
        rr_floor=2.0,
        min_score=7,
        trade_type="SESSION_TRADE",
        monitor_only=True,      # 4 trades, 25% WR, -2.91R — MONITOR ONLY pending more data
        description="Parabolic push into HCOW/LCOW structural level → false-break reversal",
    ),
    PatternDef(
        name="MONDAY_FALSE_BREAK",
        score_bonus=2,
        rr_floor=2.0,
        min_score=7,
        trade_type="SESSION_TRADE",
        monitor_only=False,
        description="Monday sweeps HOW or LOW and fails; fade the false break Tue–Thu",
    ),
    PatternDef(
        name="LOW_HANGING_FRUIT",
        score_bonus=1,          # +1 only (continuation, not reversal signal)
        rr_floor=2.0,
        min_score=7,
        trade_type="SESSION_TRADE",
        monitor_only=False,
        description="Explosive prior-session move → 50% pullback continuation entry",
    ),
]

# Build lookup dict — primary interface for the rest of the codebase
PATTERN: dict[str, PatternDef] = {p.name: p for p in _DEFS}

# Convenience sets
ALL_PATTERN_NAMES:     frozenset[str] = frozenset(PATTERN)
MONITOR_ONLY_PATTERNS: frozenset[str] = frozenset(
    p.name for p in _DEFS if p.monitor_only
)
RELAXED_RR_PATTERNS:   frozenset[str] = frozenset(
    p.name for p in _DEFS if p.rr_floor < 2.0
)


def get_score_bonus(pattern_name: str) -> int:
    """Return the base score bonus for a pattern. 0 if unknown."""
    return PATTERN[pattern_name].score_bonus if pattern_name in PATTERN else 0


def get_rr_floor(pattern_name: str) -> float:
    """Return the R:R floor for a pattern. Falls back to global 2:1."""
    return PATTERN[pattern_name].rr_floor if pattern_name in PATTERN else 2.0


def get_min_score(pattern_name: str) -> int:
    """Return the minimum quality-gate score for a pattern."""
    return PATTERN[pattern_name].min_score if pattern_name in PATTERN else 7


def is_monitor_only(pattern_name: str) -> bool:
    """True if this pattern should alert but not execute."""
    return PATTERN[pattern_name].monitor_only if pattern_name in PATTERN else False
