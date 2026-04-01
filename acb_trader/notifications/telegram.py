"""
ACB Trader — Telegram Notifications
EOD briefing + real-time state transition alerts.
Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID as environment variables.
"""

from __future__ import annotations
import os
import requests
from datetime import datetime
from acb_trader.config import ET, MONITOR_ONLY_PATTERNS
from acb_trader.db.models import Setup, TradeRecord, SystemHealthResult, WeeklyTemplate, WeeklyReviewReport


def _send(text: str) -> bool:
    token   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    proxy_url = os.environ.get("TELEGRAM_PROXY", "")
    if not token or not chat_id:
        print(f"[telegram] {text}")
        return True
    
    proxies = {}
    if proxy_url:
        proxies = {
            "http": proxy_url,
            "https": proxy_url,
        }

    kwargs = {
        "json": {"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
        "timeout": 10,
    }
    if proxies:
        kwargs["proxies"] = proxies

    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            **kwargs
        )
        return resp.ok
    except Exception as e:
        print(f"[telegram] Failed: {e}")
        return False


def send_eod_briefing(templates: list[WeeklyTemplate], setups: list[Setup]) -> bool:
    now = datetime.now(ET)
    lines = [
        f"🏛 <b>INSTITUTIONAL BIASED ANALYSIS</b>",
        f"📅 {now.strftime('%a %d %b %Y %H:%M ET')}\n",
        f"── <b>WEEKLY TEMPLATES</b> ──"
    ]

    for t in templates[:5]:
        direction_icon = "📈" if "LONG" in t.valid_directions and "SHORT" not in t.valid_directions else \
                         "📉" if "SHORT" in t.valid_directions and "LONG" not in t.valid_directions else "↔️"
        
        lines.append(
            f"{direction_icon} <b>{t.pair}</b> | {t.template_type}\n"
            f"   ⊢ Phase: {t.monthly_phase}\n"
            f"   ⊢ 3HC/LC: {t.close_countdown.label} (count: {t.close_countdown.count})\n"
            f"   ⊢ Locks: H:{'✅' if t.high_locked else '❌'} L:{'✅' if t.low_locked else '❌'}"
        )

    if setups:
        lines.append(f"\n── <b>ARMED FOR EXECUTION ({len(setups)})</b> ──")
        for s in setups[:5]:
            tier_icon = "💎" if s.trade_type == "FIVE_STAR_SCALABLE" else "⚡️"
            bias = "🟢 LONG" if s.direction == "LONG" else "🔴 SHORT"
            lines.append(
                f"{tier_icon} <b>{s.pair}</b> | {bias} | {s.pattern}\n"
                f"   ⊢ Score: {s.score}/14\n"
                f"   ⊢ Entry: {s.entry_price:.5f} | SL: {s.stop_price:.5f} | TP1: {s.target_1:.5f}"
            )
    else:
        lines.append("\n⏸ <b>NO HIGH-PROBABILITY SETUPS</b>\n   <i>Status: Sitting on hands / cash is a position.</i>")

    return _send("\n".join(lines))


def send_setup_armed(setup: Setup) -> bool:
    tier = "💎 <b>FIVE_STAR SCALABLE</b>" if setup.trade_type == "FIVE_STAR_SCALABLE" else "⚡️ <b>SESSION OPPORTUNITY</b>"
    bias = "🟢 LONG" if setup.direction == "LONG" else "🔴 SHORT"
    
    monitor_block = (
        "\n\n⚠️ <b>MONITOR ONLY — do not trade</b>\n"
        "Pattern currently under out-of-sample observation."
    ) if setup.pattern in MONITOR_ONLY_PATTERNS else ""
    
    msg = (
        f"🔔 <b>SETUP ARMED</b> | <b>{setup.pair}</b>\n"
        f"────────────────────\n"
        f"<b>Logic:</b>     {setup.pattern}\n"
        f"<b>Tier:</b>      {tier}\n"
        f"<b>Bias:</b>      {bias}\n"
        f"<b>Score:</b>     {setup.score}/14\n\n"
        f"<b>Entry:</b>     {setup.entry_price:.5f}\n"
        f"<b>Stop:</b>      {setup.stop_price:.5f}\n"
        f"<b>Target 1:</b>  {setup.target_1:.5f}\n"
        f"<b>Risk:</b>      {setup.risk_pips:.1f} pips\n"
        f"────────────────────\n"
        f"<i>Awaiting 5-min 20-EMA coil trigger...</i>"
        f"{monitor_block}"
    )
    return _send(msg)


def send_state_change(pair: str, old_state: str, new_state: str, price: float) -> bool:
    icons = {
        "ACTIVE": "✅", "PARTIAL_EXIT": "💰", "STOPPED_OUT": "❌",
        "BREAKEVEN_CLOSE": "⚖️", "FORCE_CLOSE": "🚪",
        "FULL_TARGET_CLOSE": "🏆", "TRAIL_CLOSE": "🎯", "EXPIRED": "⏰",
    }
    icon = icons.get(new_state, "➡️")
    
    # State-specific flavor text
    sub_text = ""
    if new_state == "ACTIVE": sub_text = "\n<i>Position live on MT5. SL/TP set.</i>"
    elif new_state == "PARTIAL_EXIT": sub_text = "\n<b>Risk Neutral.</b> Stop moved to Breakeven."
    elif new_state == "FULL_TARGET_CLOSE": sub_text = "\n<b>Predator cycle complete.</b> Cash is locked."
    
    return _send(
        f"{icon} <b>{pair} UPDATE</b>\n"
        f"State: {old_state} ➡️ <b>{new_state}</b>\n"
        f"Price: <b>{price:.5f}</b> @ {datetime.now(ET).strftime('%H:%M ET')}"
        f"{sub_text}"
    )


def send_trade_debrief(record: TradeRecord) -> bool:
    outcome = "✅ WIN" if record.r_multiple > 0 else "❌ LOSS"
    return _send(
        f"{outcome} <b>DEBRIEF — {record.pair}</b>\n"
        f"{record.r_multiple:+.2f}R | {record.pips:+.1f} pips\n"
        f"Pattern: {record.pattern} | {record.trade_type}\n"
        f"Score: {record.score}/14 | Terminal: {record.terminal_state}"
    )


def send_circuit_breaker(reason: str) -> bool:
    return _send(f"🛑 <b>CIRCUIT BREAKER</b>\n{reason}")


def send_health_warnings(result: SystemHealthResult) -> bool:
    if result.passed and not result.warnings:
        return True
    status = "✅ PASSED" if result.passed else "❌ FAILED"
    lines = [f"🏥 <b>SYSTEM HEALTH</b>: {status}"]
    for f in result.failures:
        lines.append(f"  ❌ {f}")
    for w in result.warnings:
        lines.append(f"  ⚠️ {w}")
    return _send("\n".join(lines))


def send_weekly_review(report: WeeklyReviewReport) -> bool:
    """
    Friday end-of-week review sent to Telegram.
    Covers P&L, win rate, per-pattern breakdown, best/worst trade,
    and a hindsight look at discarded setups.
    """
    outcome   = "✅" if report.total_r >= 0 else "❌"
    pnl_str   = f"{report.weekly_dd_pct:+.2%}" if report.weekly_dd_pct is not None else "N/A"
    week_label = (
        f"{report.week_start.strftime('%d %b')} – "
        f"{report.week_end.strftime('%d %b %Y')}"
    )

    lines = [
        f"📅 <b>WEEKLY REVIEW</b> — {week_label}\n",
        (
            f"📊 <b>P&amp;L:</b> {outcome} {report.total_r:+.2f}R "
            f"| {report.total_pips:+.1f} pips | Account: {pnl_str}"
        ),
        f"🎯 <b>Win Rate:</b> {report.wins}/{report.total_trades}"
        + (f" ({report.win_rate:.0%})" if report.total_trades else ""),
    ]

    if report.pattern_breakdown:
        lines.append("\n🔍 <b>Pattern Breakdown:</b>")
        for pattern, stats in sorted(
            report.pattern_breakdown.items(),
            key=lambda kv: kv[1]["total_r"],
            reverse=True,
        ):
            wr = stats["wins"] / stats["trades"] if stats["trades"] else 0.0
            lines.append(
                f"   {pattern}: {stats['trades']}T "
                f"| {wr:.0%} WR "
                f"| {stats['total_r']:+.2f}R"
            )

    if report.best_trade:
        lines.append(f"\n🏆 <b>Best:</b>  {report.best_trade}")
    if report.worst_trade:
        lines.append(f"💀 <b>Worst:</b> {report.worst_trade}")

    if report.discards_total > 0:
        lines.append(
            f"\n🗑 <b>Discards Hindsight:</b> "
            f"{report.discards_would_have_hit}/{report.discards_total} "
            f"would have hit T1"
        )

    if report.total_trades == 0:
        lines.append("\n⏸ <b>No trades taken this week</b> — sat on hands")

    return _send("\n".join(lines))


def get_updates(offset: int | None = None) -> list:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    proxy_url = os.environ.get("TELEGRAM_PROXY", "")
    if not token:
        return []

    proxies = {}
    if proxy_url:
        proxies = {"http": proxy_url, "https": proxy_url}

    params = {"timeout": 30}
    if offset:
        params["offset"] = offset

    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{token}/getUpdates",
            params=params,
            timeout=35,
            proxies=proxies if proxies else None,
        )
        if resp.ok:
            return resp.json().get("result", [])
    except Exception:
        pass
    return []
