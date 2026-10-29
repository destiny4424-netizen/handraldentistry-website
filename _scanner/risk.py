"""Risk engine: how much to trade, and when to stop for the day.

Defaults for a ₹1,50,000 account (all changeable in Settings):
    - risk 1% of capital a trade (₹1,500): position size = that ÷ the distance to the stop
    - intraday position value at most 2× capital (intraday margin); BTST at most 1× (it is held overnight)
    - stop taking new intraday trades for the day at −3R (−3% of capital, ₹4,500)
    - at most 3 intraday trades open at the same time, and at most 5 new intraday trades a day
    - at most 2 BTST/STBT trades a night
Blocked picks are still shown (marked), but they are not taken into Today's trades or Performance.
"""
import math

DEFAULTS = {"capital": 150000, "risk_pct": 1.0, "daily_loss_pct": 3.0,
            "max_open": 3, "max_trades": 5, "btst_max": 2}
LIMITS = {"capital": (10000, 1e9), "risk_pct": (0.1, 5), "daily_loss_pct": (0.5, 20),
          "max_open": (1, 20), "max_trades": (1, 50), "btst_max": (0, 10)}
INTRADAY_LEVERAGE = 2.0


def config(settings):
    cfg = dict(DEFAULTS)
    for k in DEFAULTS:
        v = (settings or {}).get("risk_" + k)
        if v is not None:
            cfg[k] = v
    cfg["risk_amt"] = round(cfg["capital"] * cfg["risk_pct"] / 100)
    cfg["limit_r"] = round(-cfg["daily_loss_pct"] / cfg["risk_pct"], 2)
    return cfg


def day_status(rows, cfg):
    """Today's intraday risk: result so far in R, trades open, and whether new trades are allowed."""
    closed = sum(r["result_r"] for r in rows if r.get("result_r") is not None)
    running = sum(r.get("running_r") or 0 for r in rows if r.get("triggered_at") and r.get("exit_at") is None)
    day_r = round(closed + running, 2)
    open_n = sum(1 for r in rows if r.get("triggered_at") and r.get("exit_at") is None)
    taken = len(rows)
    blocked = None
    if day_r <= cfg["limit_r"]:
        blocked = f"Daily loss limit reached ({day_r:+.2f}R of {cfg['limit_r']}R): no new trades today"
    elif taken >= cfg["max_trades"]:
        blocked = f"{cfg['max_trades']} trades already taken today (the daily maximum)"
    elif open_n >= cfg["max_open"]:
        blocked = f"{open_n} trades already open (maximum {cfg['max_open']} at a time)"
    return {"day_r": day_r, "day_rs": round(day_r * cfg["risk_amt"]), "open": open_n, "taken": taken,
            "blocked": blocked, "status": "STOPPED" if blocked and day_r <= cfg["limit_r"] else "FULL" if blocked else "OK"}


def size_stock(plan, cfg, btst=False):
    """Shares to trade so that hitting the stop loses about the risk amount."""
    entry, sl = plan["entry"], plan["sl"]
    per = abs(entry - sl)
    if not per or not entry:
        return None
    qty = math.floor(cfg["risk_amt"] / per)
    cap = math.floor(cfg["capital"] * (1.0 if btst else INTRADAY_LEVERAGE) / entry)
    capped = qty > cap
    qty = min(qty, cap)
    if qty <= 0:
        return None
    return {"qty": qty, "value": round(qty * entry), "risk": round(qty * per), "capped": capped,
            "t1_gain": round(qty * abs(plan["t1"] - entry))}


def size_option(opt, cfg):
    """Lots of the option so that its stop loses about the risk amount."""
    if not opt or not opt.get("lot") or not opt.get("risk_lot"):
        return None
    lots = math.floor(cfg["risk_amt"] / opt["risk_lot"])
    out = {"lots": lots, "risk_lot": opt["risk_lot"], "cost_lot": round(opt["entry"] * opt["lot"])}
    if lots <= 0:
        out["note"] = f"1 lot risks ₹{opt['risk_lot']:,}, more than your ₹{cfg['risk_amt']:,} a trade: trade the stock instead"
    else:
        out.update(cost=round(lots * opt["entry"] * opt["lot"]), risk=lots * opt["risk_lot"])
    return out
