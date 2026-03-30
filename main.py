"""
ACB Trader — EOD Runner + Intraday Session Supervisor
EOD pipeline fires at 5:04 PM ET Mon–Thu.
Intraday supervisors fire at each institutional session open (Asia / London / NY).
"""

from __future__ import annotations
import os
import sys
import json
import dataclasses
import schedule
import threading
import time
from datetime import datetime, date, timedelta
import pandas as pd

from acb_trader.config import (
    ET, BASKETS, EOD_RUN_OFFSET_MIN, NY_CLOSE_HOUR,
    SESSION_WINDOWS, SESSION_PAIRS,
)
from acb_trader.data.feed import BrokerFeed
from acb_trader.data.calendar import get_blocking_events
from acb_trader.signals.classify import classify_market_state, rank_basket
from acb_trader.signals.watchlist import evaluate_watchlist
from acb_trader.signals.setups import detect_setups, assert_eod_complete
from acb_trader.execution.coil import has_ema_coil_htf
from acb_trader.execution.sizing import calculate_rr
from acb_trader.execution.session import run_intraday_session
from acb_trader.guards.checklist import run_pre_trade_checklist, passes_100_lot_test
from acb_trader.notifications.telegram import (
    send_eod_briefing, send_setup_armed, send_health_warnings, send_circuit_breaker,
    send_weekly_review,
)
from acb_trader.db.models import AccountState
from acb_trader.db.session_tracker import (
    compute_account_metrics, log_discard,
)
from acb_trader.signals.weekly import build_weekly_template, build_weekly_review


PAUSED_SETUPS_PATH = os.path.join(os.path.dirname(__file__), "paused_setups.json")

# ── SHARED SESSION STATE ──────────────────────────────────────────────────────
# _ARMED_SETUPS  — populated by run_eod(), consumed by session threads.
# _TRADED_PAIRS  — pairs already filled today; excluded from later sessions.
#                  Enforces the no-carry-forward rule across session boundaries.
_ARMED_SETUPS: list = []
_TRADED_PAIRS: set  = set()
_state_lock         = threading.Lock()


def _save_paused_setups(setups: list) -> None:
    """Persist news-paused setups to disk for the intraday re-arm check."""
    existing: list[dict] = []
    if os.path.exists(PAUSED_SETUPS_PATH):
        try:
            with open(PAUSED_SETUPS_PATH) as fh:
                existing = json.load(fh)
        except Exception:
            existing = []

    def _serialise(s) -> dict:
        d = dataclasses.asdict(s)
        # Convert non-serialisable types
        for k, v in d.items():
            if isinstance(v, (date, datetime)):
                d[k] = v.isoformat()
        # news_events contains NewsEvent dataclasses — already dict after asdict
        return d

    new_entries = [_serialise(s) for s in setups]
    # De-duplicate by pair+pattern+entry_date
    seen = {(e["pair"], e["pattern"], e["entry_date"]) for e in existing}
    for entry in new_entries:
        key = (entry["pair"], entry["pattern"], entry["entry_date"])
        if key not in seen:
            existing.append(entry)
            seen.add(key)

    with open(PAUSED_SETUPS_PATH, "w") as fh:
        json.dump(existing, fh, indent=2, default=str)
    print(f"[main] {len(new_entries)} setup(s) written to paused_setups.json")


# ── EOD PIPELINE ──────────────────────────────────────────────────────────────

def run_eod(feed: BrokerFeed):
    now = datetime.now(ET)
    print(f"\n{'='*60}")
    print(f"ACB EOD RUN — {now.strftime('%A %d %b %Y %H:%M ET')}")
    print(f"{'='*60}")

    # ── Skip weekends ─────────────────────────────────────────────────────────
    if now.weekday() >= 5:
        print("[main] Weekend — skipping run")
        return

    # ── Step 0: EOD guard ─────────────────────────────────────────────────────
    try:
        assert_eod_complete()
    except RuntimeError as e:
        print(f"[main] {e}")
        return

    # ── Step 0b: Account & system health ─────────────────────────────────────
    acc_raw = feed.get_account()
    balance = acc_raw["balance"]
    daily_pnl_pct, weekly_dd_pct, consec_losses = compute_account_metrics(balance)

    account = AccountState(
        balance=balance,
        equity=acc_raw["equity"],
        daily_pnl_pct=daily_pnl_pct,
        weekly_drawdown_pct=weekly_dd_pct,
        consecutive_losses=consec_losses,
    )

    normal_spreads = {p: 1.5 for p in ["EURUSD", "GBPUSD"]}
    current_spreads = {p: feed.get_spread(p) for p in ["EURUSD", "GBPUSD"]}

    health = run_pre_trade_checklist(
        account=account,
        broker_connected=feed.is_connected(),
        data_age_minutes=feed.last_update_age_minutes(),
        normal_spreads=normal_spreads,
        current_spreads=current_spreads,
    )
    send_health_warnings(health)

    if not health.passed:
        for f in health.failures:
            print(f"  ❌ {f}")
        send_circuit_breaker("\n".join(health.failures))
        return

    current_week  = now.isocalendar().week
    current_month = now.month
    days_into_month = sum(
        1 for d in pd.date_range(
            start=date(now.year, now.month, 1), end=now.date(), freq="B"
        )
    )

    all_setups   = []
    all_templates = []
    discarded_log = []

    # ── Step 1–3: Classify → Watchlist → Weekly (per basket) ─────────────────
    for basket_name, pairs in BASKETS.items():
        print(f"\n── Basket: {basket_name} ──")
        states = {}

        for pair in pairs:
            try:
                daily_ohlcv   = feed.get_daily_ohlcv(pair, count=60)
                intraday_1min = feed.get_1min_today(pair)
                session_1min  = intraday_1min  # simplification

                # Step 1: Classify
                state = classify_market_state(
                    pair=pair,
                    daily_ohlcv=daily_ohlcv,
                    intraday_1min=intraday_1min,
                    session_1min=session_1min,
                    current_week=current_week,
                    current_month=current_month,
                )
                states[pair] = state

                # Step 2: Watchlist filter
                wl = evaluate_watchlist(
                    state=state,
                    daily_ohlcv=daily_ohlcv,
                    prior_week_high=state.how,
                    prior_week_low=state.low_of_week,
                    prior_month_high=state.hom,
                    prior_month_low=state.lom,
                )

                if not wl.on_watchlist:
                    print(f"  {pair}: not on watchlist")
                    continue

                print(f"  {pair}: {state.state}/{state.substate} cib={state.cib} streak={state.close_streak}")

                # Step 3: Weekly template
                template = build_weekly_template(
                    pair=pair,
                    daily_ohlcv=daily_ohlcv,
                    current_week=current_week,
                    current_month=current_month,
                    days_into_month=days_into_month,
                    atr14=state.atr14,
                    close_streak=state.close_streak,
                    cib_direction=state.cib_direction,
                )
                all_templates.append(template)

                # EMA coil (higher time frame check)
                # timeframe="DAILY" applies the 0.75 × ATR14 professional-boundary multiplier.
                # The strict 0.5 × intraday multiplier is only used once the system arms
                # and switches to 15-min monitoring for the 5-min 20 EMA entry trigger.
                h4_ohlcv = feed.get_ohlcv(pair, "H4", count=50) if hasattr(feed, "get_ohlcv") else daily_ohlcv
                ema_coil = has_ema_coil_htf(h4_ohlcv, state.atr14, timeframe="DAILY")

                # 15-min bars for FRD/FGD coil-stop calculation
                m15_ohlcv = feed.get_15min_bars(pair, count=48)

                # Step 4: Detect setups
                setups, discarded = detect_setups(
                    state=state,
                    template=template,
                    daily_ohlcv=daily_ohlcv,
                    ema_coil=ema_coil,
                    m15_ohlcv=m15_ohlcv,
                )
                discarded_log.extend(discarded)
                # Persist discards for weekly review hindsight analysis
                for d in discarded:
                    log_discard(d)

                # Step 4b: News filter — pause (not abort) setups blocked by MRN.
                # Paused setups are written to paused_setups.json for intraday re-arm
                # after the news spike settles into a 5-min EMA coil.
                clean_setups: list = []
                paused_setups: list = []
                for s in setups:
                    entry_dt = datetime(
                        s.entry_date.year, s.entry_date.month, s.entry_date.day,
                        8, 30, tzinfo=ET,   # NY session open proxy
                    )
                    blocking = get_blocking_events(s.pair, entry_dt)
                    if blocking:
                        s.news_events = blocking
                        paused_setups.append(s)
                        titles = [e.title for e in blocking]
                        print(f"  {s.pair} {s.pattern}: NEWS PAUSED — {titles}")
                    else:
                        clean_setups.append(s)
                setups = clean_setups
                if paused_setups:
                    _save_paused_setups(paused_setups)

                # Force FIVE_STAR if passes 100-lot test
                for s in setups:
                    if passes_100_lot_test(s, template):
                        s.trade_type = "FIVE_STAR_SCALABLE"

                all_setups.extend(setups)

            except Exception as e:
                print(f"  {pair}: ERROR — {e}")
                continue

        # Rank basket
        rank_basket(states, pairs)

    # ── Sort all setups by score ───────────────────────────────────────────────
    all_setups.sort(key=lambda s: s.score, reverse=True)

    # ── Step 5: Alert ─────────────────────────────────────────────────────────
    print(f"\n── Results ──")
    print(f"  Setups found:    {len(all_setups)}")
    print(f"  Setups discarded: {len(discarded_log)}")

    if all_setups:
        for s in all_setups:
            rr = calculate_rr(s)
            print(f"  ★ {s.pair} {s.direction} {s.pattern} score={s.score} R:R={rr} tier={s.trade_type}")
            send_setup_armed(s)
    else:
        print("  No valid setups — sit on hands today")

    send_eod_briefing(all_templates[:5], all_setups[:5])

    # ── Arm the session supervisors ───────────────────────────────────────────
    # Store today's setups so each session launcher can claim its slice.
    # _TRADED_PAIRS is cleared so tonight's Asia session starts clean.
    with _state_lock:
        _ARMED_SETUPS.clear()
        _ARMED_SETUPS.extend(all_setups)
        _TRADED_PAIRS.clear()
    print(f"[main] {len(all_setups)} setup(s) armed for intraday sessions")

    print(f"\n[main] EOD run complete {datetime.now(ET).strftime('%H:%M ET')}")


# ── WEEKLY REVIEW ────────────────────────────────────────────────────────────

def run_weekly_review(feed: BrokerFeed):
    """
    Friday 17:30 ET: aggregate the week's trades & discards, send Telegram review.
    Weekly P&L % is pulled from session_tracker's Monday-open balance.
    """
    now = datetime.now(ET)
    print(f"\n{'='*60}")
    print(f"ACB WEEKLY REVIEW — {now.strftime('%A %d %b %Y %H:%M ET')}")
    print(f"{'='*60}")

    monday = now.date() - timedelta(days=now.weekday())  # ISO Monday of current week

    # Weekly DD from session_tracker (set on Monday morning, compared to now)
    weekly_dd_pct = 0.0
    try:
        acc = feed.get_account()
        _, weekly_dd_pct, _ = compute_account_metrics(acc["balance"])
    except Exception as e:
        print(f"[weekly_review] Could not fetch account metrics: {e}")

    report = build_weekly_review(monday, weekly_dd_pct)
    send_weekly_review(report)

    print(
        f"[main] Weekly review sent — "
        f"{report.wins}/{report.total_trades} wins | "
        f"{report.total_r:+.2f}R | "
        f"{now.strftime('%H:%M ET')}"
    )


# ── SESSION LAUNCHER ─────────────────────────────────────────────────────────

def launch_session(session_name: str, feed: BrokerFeed) -> None:
    """
    Fire the intraday session supervisor in a daemon thread.

    Filters _ARMED_SETUPS to only the pairs valid for this session.
    Excludes any pair already filled in an earlier session today (_TRADED_PAIRS).
    If no setups remain after filtering, exits immediately — no thread spawned.
    Records filled pairs back into _TRADED_PAIRS on session completion so that
    the same pair cannot be entered again in a subsequent session today.
    """
    now = datetime.now(ET)

    # Hard gate: never run on weekends
    if now.weekday() >= 5:
        print(f"[main] {session_name}: weekend — skipped")
        return

    session_pairs = set(SESSION_PAIRS.get(session_name, []))

    with _state_lock:
        available = [
            s for s in _ARMED_SETUPS
            if s.pair in session_pairs and s.pair not in _TRADED_PAIRS
        ]

    if not available:
        print(f"[main] {session_name}: no armed setups — standing down")
        return

    print(
        f"[main] {session_name} session OPEN — launching supervisor "
        f"({len(available)} setup(s): {[s.pair for s in available]})"
    )

    def _run():
        try:
            records = run_intraday_session(available, session_name, feed)
            if records:
                filled = {r.pair for r in records}
                with _state_lock:
                    _TRADED_PAIRS.update(filled)
                print(
                    f"[main] {session_name} complete — "
                    f"{len(records)} trade(s) closed: {list(filled)}"
                )
            else:
                print(f"[main] {session_name} complete — no fills")
        except Exception:
            import traceback
            print(f"[main] {session_name} supervisor crashed:")
            traceback.print_exc()

    t = threading.Thread(target=_run, name=f"session-{session_name}", daemon=True)
    t.start()


# ── SCHEDULER ─────────────────────────────────────────────────────────────────

def start_scheduler(feed: BrokerFeed):
    """
    Full institutional schedule (all times ET, assumes machine clock = ET):

    Mon–Fri  17:04   EOD pipeline → arms _ARMED_SETUPS
    Mon–Thu  19:00   Asia session supervisor
    Tue–Sat  01:00   London session supervisor   (next calendar day after EOD)
    Tue–Sat  07:00   New York FX session
    Tue–Sat  09:30   New York Equity session
    Fri      17:30   Weekly review

    Session threads are daemon threads — the scheduler loop remains unblocked.
    Weekend gate inside launch_session() prevents Saturday from doing work.
    """
    eod_time    = f"{NY_CLOSE_HOUR:02d}:{EOD_RUN_OFFSET_MIN:02d}"   # "17:04"
    review_time = "17:30"

    # ── EOD pipeline ─────────────────────────────────────────────────────────
    for day in ("monday", "tuesday", "wednesday", "thursday", "friday"):
        getattr(schedule.every(), day).at(eod_time).do(run_eod, feed=feed)

    # ── Weekly review (Friday only) ───────────────────────────────────────────
    schedule.every().friday.at(review_time).do(run_weekly_review, feed=feed)

    # ── Asia session  19:00 ET  Mon–Thu (same evening as EOD) ────────────────
    for day in ("monday", "tuesday", "wednesday", "thursday"):
        getattr(schedule.every(), day).at("19:00").do(
            launch_session, session_name="ASIA", feed=feed
        )

    # ── London session  01:00 ET  Tue–Fri (early morning, after prior-day EOD)
    for day in ("tuesday", "wednesday", "thursday", "friday"):
        getattr(schedule.every(), day).at("01:00").do(
            launch_session, session_name="LONDON", feed=feed
        )

    # ── New York FX session  07:00 ET  Tue–Fri ───────────────────────────────
    for day in ("tuesday", "wednesday", "thursday", "friday"):
        getattr(schedule.every(), day).at("07:00").do(
            launch_session, session_name="NEW_YORK_FX", feed=feed
        )

    # ── New York Equity session  09:30 ET  Tue–Fri ───────────────────────────
    for day in ("tuesday", "wednesday", "thursday", "friday"):
        getattr(schedule.every(), day).at("09:30").do(
            launch_session, session_name="NEW_YORK_EQ", feed=feed
        )

    print(
        f"[main] Scheduler armed\n"
        f"  EOD:        {eod_time} ET  Mon–Fri\n"
        f"  Asia:       19:00 ET  Mon–Thu\n"
        f"  London:     01:00 ET  Tue–Fri\n"
        f"  NY FX:      07:00 ET  Tue–Fri\n"
        f"  NY Equity:  09:30 ET  Tue–Fri\n"
        f"  Review:     {review_time} ET  Fri"
    )

    while True:
        schedule.run_pending()
        time.sleep(30)


# ── ENTRY POINT ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="ACB Trader EOD Runner")
    parser.add_argument("--now",    action="store_true", help="Run EOD pipeline immediately")
    parser.add_argument("--login",  type=int,   default=int(os.environ.get("MT5_LOGIN",  "0")))
    parser.add_argument("--password", default=os.environ.get("MT5_PASSWORD", ""))
    parser.add_argument("--server",   default=os.environ.get("MT5_SERVER",   ""))
    args = parser.parse_args()

    feed = BrokerFeed(login=args.login, password=args.password, server=args.server)
    if not feed.connect():
        print("[main] Broker connection failed — running in paper/backtest mode")

    if args.now:
        run_eod(feed)
    else:
        start_scheduler(feed)
