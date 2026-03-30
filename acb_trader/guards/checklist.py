"""
ACB Trader — Pre-Trade System Health Check (skill_psychology_guardrails.md)
Runs before every EOD signal layer. Any failure aborts the run.
"""

from __future__ import annotations
from datetime import datetime, date
from acb_trader.config import (
    ET, DAILY_LOSS_HALT_PCT, WEEKLY_DD_HALT_PCT, CONSEC_LOSS_HALT,
    MIN_TARGET_PIPS, ANCHOR_CONFLUENCE_PIPS, COIL_FORCE_PROMOTE_PATTERNS,
)
from acb_trader.db.models import (
    AccountState, SystemHealthResult, Setup, WeeklyTemplate, DiscardedSetup
)
from acb_trader.data.levels import get_pip_size, price_to_pips


def run_pre_trade_checklist(
    account: AccountState,
    broker_connected: bool,
    data_age_minutes: int,
    normal_spreads: dict[str, float],
    current_spreads: dict[str, float],
) -> SystemHealthResult:
    failures: list[str] = []
    warnings: list[str] = []
    now = datetime.now(ET)

    # ── HARD FAILURES ─────────────────────────────────────────────────────────
    if not broker_connected:
        failures.append("BROKER_DISCONNECTED")

    if account.daily_pnl_pct <= -DAILY_LOSS_HALT_PCT:
        failures.append(f"DAILY_LOSS_HALT: {account.daily_pnl_pct:.2%} loss today")

    if account.weekly_drawdown_pct <= -WEEKLY_DD_HALT_PCT:
        failures.append(f"WEEKLY_DD_HALT: {account.weekly_drawdown_pct:.2%} from week open")

    if account.consecutive_losses >= CONSEC_LOSS_HALT:
        failures.append(f"CONSEC_LOSS_HALT: {account.consecutive_losses} consecutive losses")

    if now.weekday() == 4:  # Friday
        failures.append("FRIDAY_NO_ENTRY: exit day only")

    # Entry date expiry — pending entries past their entry_date
    for s in account.pending_entries:
        if date.today() > s.entry_date:
            failures.append(f"ENTRY_DATE_EXPIRED: {s.pair} {s.pattern} expired {s.entry_date}")

    # NY close guard
    ny_close = now.replace(hour=17, minute=4, second=0, microsecond=0)
    if now < ny_close:
        failures.append(f"BEFORE_NY_CLOSE: {now.strftime('%H:%M ET')} — in-progress candle")

    # ── WARNINGS ──────────────────────────────────────────────────────────────
    for pair, spread in current_spreads.items():
        normal = normal_spreads.get(pair, 2.0)
        if spread > normal * 3:
            warnings.append(f"WIDE_SPREAD: {pair} {spread:.1f} pips ({spread/normal:.1f}× normal)")

    if account.balance > 0:
        unrealised_pct = (account.balance - account.equity) / account.balance
        if unrealised_pct > 0.015:
            warnings.append(f"UNREALISED_LOSS: {unrealised_pct:.2%} of balance in open drawdown")

    if data_age_minutes > 10:
        warnings.append(f"STALE_DATA: feed last updated {data_age_minutes}m ago")

    return SystemHealthResult(
        passed=len(failures) == 0,
        failures=failures,
        warnings=warnings,
        timestamp=now,
    )


def is_diddle(setup: Setup, template: WeeklyTemplate) -> bool:
    """
    Returns True if setup should be discarded as a science project.
    """
    pip = get_pip_size(setup.pair)

    # Direction conflicts with locked levels
    if setup.direction == "LONG"  and template.high_locked: return True
    if setup.direction == "SHORT" and template.low_locked:  return True

    # Entry not near any anchor level
    levels = [
        template.anchors.prior_week_high, template.anchors.prior_week_low,
        template.anchors.current_week_high, template.anchors.current_week_low,
        template.anchors.month_open,
    ]
    near_anchor = any(
        abs(setup.entry_price - lvl) <= ANCHOR_CONFLUENCE_PIPS * pip
        for lvl in levels if lvl > 0
    )
    if not near_anchor: return True

    # Target too close
    t1_pips = price_to_pips(abs(setup.target_1 - setup.entry_price), setup.pair)
    if t1_pips < MIN_TARGET_PIPS: return True

    # R:R < 2:1
    if setup.risk_pips > 0 and (t1_pips / setup.risk_pips) < 2.0: return True

    return False


def passes_100_lot_test(setup: Setup, template: WeeklyTemplate) -> bool:
    """
    Would a professional trade this with size?
    If True → force FIVE_STAR_SCALABLE tier regardless of score.

    EMA Coil Override (fast path): if a confirmed 15-min tight EMA coil exists
    at a weekly extreme for a structural reversal pattern, geometry IS the
    quality signal — no score threshold required.
    """
    pip = get_pip_size(setup.pair)

    major_levels = [
        template.anchors.current_week_high, template.anchors.current_week_low,
        template.anchors.current_hcow, template.anchors.current_lcow,
        template.anchors.prior_week_high, template.anchors.prior_week_low,
        template.anchors.month_open,
    ]
    at_extreme = any(
        abs(setup.entry_price - lv) <= 25 * pip for lv in major_levels if lv > 0
    )

    # ── EMA COIL OVERRIDE (Fast Path) ─────────────────────────────────────────
    # Confirmed 15-min EMA coil (9/20/50 spread <= 0.5×ATR14 for 3+ bars) at
    # the weekly extreme = potential energy fully loaded.  The coil is a harder
    # filter than any EOD point system.  Bypasses score >= 7 gate entirely.
    if (getattr(setup, 'ema_coil_confirmed', False)
            and setup.pattern in COIL_FORCE_PROMOTE_PATTERNS
            and at_extreme):
        return True

    # ── STANDARD PATH ─────────────────────────────────────────────────────────
    anchor_count = sum(
        1 for lvl in [
            template.anchors.prior_week_high, template.anchors.prior_week_low,
            template.anchors.current_hcow, template.anchors.current_lcow,
            template.anchors.month_open,
        ]
        if lvl > 0 and abs(setup.entry_price - lvl) <= ANCHOR_CONFLUENCE_PIPS * pip
    )
    stop_dist = price_to_pips(abs(setup.entry_price - setup.stop_price), setup.pair)
    priority_patterns = (
        "FIRST_RED_DAY", "FIRST_GREEN_DAY",
        "MONDAY_FALSE_BREAK", "PUMP_COIL_DUMP", "PARABOLIC_REVERSAL",
    )
    return (
        setup.score >= 7 and
        template.template_type in ("REVERSAL_WEEK", "NEW_MONTH_BREAKOUT") and
        stop_dist <= 0.75 * template.anchors.prior_week_high * 0.001 and  # proxy
        anchor_count >= 2 and
        setup.pattern in priority_patterns and
        (at_extreme or getattr(setup, 'ema_coil_confirmed', False))
    )


def generate_debrief(record) -> str:
    outcome = "✅ WIN" if record.r_multiple > 0 else "❌ LOSS"
    return (
        f"\n{'─'*40}\n"
        f"TRADE DEBRIEF — {record.pair} {record.direction}\n"
        f"{'─'*40}\n"
        f"Outcome:   {outcome} {record.r_multiple:+.2f}R ({record.pips:+.1f} pips)\n"
        f"Pattern:   {record.pattern}\n"
        f"Tier:      {record.trade_type}\n"
        f"Score:     {record.score}/14\n"
        f"Terminal:  {record.terminal_state}\n"
        f"Entry:     {record.entry_price} @ {record.entry_time.strftime('%H:%M ET')}\n"
        f"Exit:      {record.exit_price}  @ {record.exit_time.strftime('%H:%M ET')}\n"
        f"Stop was:  {record.stop_price}\n"
        f"T1 was:    {record.target_1}\n"
        f"Notes:     {record.notes}\n"
        f"{'─'*40}\n"
    )
