"""Discipline guard: watches the trades YOU took (marked with Took it) and enforces your own rules.

  - Exit-now alarm: when your stop is hit (the option's own price for an option, the stock's price for a
    stock), an urgent alert, repeated every 5 minutes while the trade is still open, with what holding is
    costing you over the planned loss.
  - Stop-loss reminder: 2 minutes after you mark a trade, if you didn't tick "SL order placed in Dhan".
  - Intraday time rules: at 2:30 PM a trade that isn't working is flagged; at 3:10 PM: square off.
  - Circuit breakers on your real results:
      2 losing trades today, or a loss of 3% of capital today  -> done for the day (no new-trade alerts)
      a loss of 6% of capital this week                         -> half size (risk per trade halved)
      a loss of 10% of capital this month                       -> paper only (alerts marked PAPER)
The scanner can't stop an order you place with your broker; it stops sending you new trades and keeps
reminding you of your own rules.
"""
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
NAG_EVERY = 5 * 60                                     # seconds between repeated exit alarms
NAG_MAX = 12
DAY_LOSSES, DAY_LOSS_PCT, WEEK_LOSS_PCT, MONTH_LOSS_PCT = 2, 3.0, 6.0, 10.0


def default_option_stop(entry, plan_opt):
    """Your option's stop: the plan's % loss from entry to stop applied to your own fill, else -30%."""
    try:
        if plan_opt and plan_opt.get("entry") and plan_opt.get("sl") and plan_opt["sl"] < plan_opt["entry"]:
            return round(entry * plan_opt["sl"] / plan_opt["entry"], 2)
    except (TypeError, ZeroDivisionError):
        pass
    return round(entry * 0.70, 2)


def breached(m, row, opt):
    """Is your stop hit? Options: the option's price at or below your option stop, or the stock's own
    plan stopped out. Stocks: the price beyond your stop."""
    px, sl = m.get("ltp"), m.get("sl")
    if opt:
        if px is not None and sl is not None and px <= sl:
            return True
        return (row.get("status") or "").startswith("SL hit")
    if px is None or sl is None:
        return False
    return px <= sl if row["side"] == "bull" else px >= sl


def loss_now(m, opt, side):
    """₹ lost now vs the ₹ planned at the stop (positive numbers)."""
    qty = m.get("qty") or 0
    sign = 1 if (opt or side == "bull") else -1
    now = (m["entry"] - m["ltp"]) * sign * qty if m.get("ltp") is not None else None
    planned = abs(m["entry"] - m["sl"]) * qty if m.get("sl") is not None else None
    return now, planned


def name_of(m, row, opt):
    return (m.get("opt") or {}).get("label") or row["symbol"] if opt else row["symbol"]


def watch(row, kind, opt, now_ts, hm):
    """Alerts due for one open real trade (and updates its alarm state). Returns [(text, buttons?)]."""
    m = row["mine"]
    out = []
    nm = name_of(m, row, opt)
    if m.get("sl_placed") is False and not m.get("remind_sent") and now_ts - m.get("at", now_ts) >= 120:
        m["remind_sent"] = True
        out.append((f"⚠ Place your stop-loss in Dhan · {nm}\nYou marked this trade without a stop order. "
                    f"Put the SL at {m['sl']:,.2f} now (or use a Super Order next time).", False))
    if breached(m, row, opt):
        lost, planned = loss_now(m, opt, row["side"])
        extra = f"\nPlanned loss ₹{planned:,.0f}; now ₹{lost:,.0f}." if lost is not None and planned is not None else ""
        if not m.get("breach_at"):
            m["breach_at"], m["nag_at"], m["nags"] = now_ts, now_ts, 0
            out.append((f"🔴 EXIT NOW · {nm}\nYour stop {m['sl']:,.2f} is hit (now {m.get('ltp', 0):,.2f}). "
                        f"Exit at market. Don't hold, don't average.{extra}", True))
        elif now_ts - m.get("nag_at", 0) >= NAG_EVERY and m.get("nags", 0) < NAG_MAX:
            m["nag_at"], m["nags"] = now_ts, m.get("nags", 0) + 1
            mins = int((now_ts - m["breach_at"]) / 60)
            out.append((f"🔴 Still open {mins} min past your stop · {nm}{extra}\nEvery minute you hold costs more. "
                        "Exit now and mark it closed.", True))
    if kind == "intra":
        if hm >= "14:30" and not m.get("t230") and m.get("ltp") is not None:
            m["t230"] = True
            lost, planned = loss_now(m, opt, row["side"])
            if lost is not None and planned and lost > -0.3 * planned:   # not at least +0.3R by 2:30
                out.append((f"⏱ 2:30 PM · {nm} isn't working\nIntraday rule: a trade that hasn't moved by now is "
                            "closed (options lose value every hour).", True))
        if hm >= "15:10" and not m.get("t310"):
            m["t310"] = True
            out.append((f"⏱ Square off · {nm}\nIt's 3:10 PM: close the intraday trade now.", True))
    return out


def period_results(trades, now):
    """Your closed real trades by today / this week / this month: count, losses, ₹."""
    d0 = now.date()
    wk0 = d0 - timedelta(days=d0.weekday())
    out = {"day": {"n": 0, "losses": 0, "rs": 0}, "week": {"n": 0, "losses": 0, "rs": 0}, "month": {"n": 0, "losses": 0, "rs": 0}}
    for t in trades:
        if t.get("rs") is None or t.get("open"):
            continue
        d = datetime.fromtimestamp(t["closed_at"], IST).date()
        for key, ok in (("day", d == d0), ("week", d >= wk0), ("month", d.year == d0.year and d.month == d0.month)):
            if ok:
                out[key]["n"] += 1
                out[key]["rs"] += t["rs"]
                out[key]["losses"] += t["rs"] < 0
    return out


def guard_state(trades, capital, now):
    p = period_results(trades, now)
    st = {"periods": p, "lock": False, "mult": 1.0, "paper": False, "why": []}
    if p["day"]["losses"] >= DAY_LOSSES or p["day"]["rs"] <= -capital * DAY_LOSS_PCT / 100:
        st["lock"] = True
        st["why"].append(f"{p['day']['losses']} losing trades / ₹{p['day']['rs']:,.0f} today: done for the day")
    if p["week"]["rs"] <= -capital * WEEK_LOSS_PCT / 100:
        st["mult"] = 0.5
        st["why"].append(f"₹{p['week']['rs']:,.0f} this week: half size until next week")
    if p["month"]["rs"] <= -capital * MONTH_LOSS_PCT / 100:
        st["paper"] = True
        st["why"].append(f"₹{p['month']['rs']:,.0f} this month: paper only until next month")
    return st
