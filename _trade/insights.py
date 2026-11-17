"""Your own trading, from your Dhan fills: round trips, real P&L after charges, where you make
and lose money, and the habits that cost you.

Fills come from Dhan's trade history (with Dhan's own charges) and today's trade book
(charges estimated until the history has them). They are kept in trades.db in the data folder.
"""
import json
import sqlite3
import threading
from contextlib import contextmanager
from collections import defaultdict
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
CHARGE_FIELDS = ("brokerageCharges", "stt", "exchangeTransactionCharges", "sebiTax", "stampDuty", "serviceTax")
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _time(r):
    for k in ("exchangeTime", "createTime", "updateTime"):
        v = str(r.get(k) or "").strip()
        if len(v) >= 16 and v[:4].isdigit() and not v.startswith("0001"):
            return v[:19].replace("T", " ")
    return ""


def kind_of(seg, inst, opt):
    inst = (inst or "").upper()
    if seg in ("NSE_EQ", "BSE_EQ"):
        return "EQUITY"
    if inst in ("FUTIDX", "OPTIDX", "FUTSTK", "OPTSTK", "FUTCOM", "OPTFUT", "FUTCUR", "OPTCUR"):
        return inst
    if seg == "MCX_COMM":
        return "OPTFUT" if opt in ("CALL", "PUT", "CE", "PE") else "FUTCOM"
    if "CURRENCY" in seg:
        return "OPTCUR" if opt in ("CALL", "PUT", "CE", "PE") else "FUTCUR"
    return "OPTSTK" if opt in ("CALL", "PUT", "CE", "PE") else "FUTSTK"


def estimate_charges(kind, product, side, qty, price):
    """Approximate statutory charges plus Dhan brokerage for one fill (₹). Used only until
    Dhan's trade history gives the real numbers."""
    value = qty * price
    opt = kind.startswith("OPT")
    fut = kind.startswith("FUT")
    delivery = kind == "EQUITY" and product in ("CNC", "MTF")
    brokerage = 0.0 if delivery else min(20.0, value * 0.0003) if not opt else 20.0
    if delivery:
        stt = value * 0.001
        exch = value * 0.0000297
        stamp = value * 0.00015 if side == "BUY" else 0
    elif kind == "EQUITY":
        stt = value * 0.00025 if side == "SELL" else 0
        exch = value * 0.0000297
        stamp = value * 0.00003 if side == "BUY" else 0
    elif fut:
        stt = value * 0.0002 if side == "SELL" else 0
        exch = value * 0.0000173
        stamp = value * 0.00002 if side == "BUY" else 0
    else:
        stt = value * 0.001 if side == "SELL" else 0
        exch = value * 0.0003503
        stamp = value * 0.00003 if side == "BUY" else 0
    sebi = value * 0.000001
    gst = 0.18 * (brokerage + exch + sebi)
    return round(brokerage + stt + exch + stamp + sebi + gst, 2)


def normalize(r, source):
    """One Dhan trade (history or today's book) -> our fill dict, or None."""
    t = _time(r)
    qty = int(_f(r.get("tradedQuantity") or r.get("quantity")))
    price = _f(r.get("tradedPrice") or r.get("price"))
    side = str(r.get("transactionType") or "").upper()
    if not t or qty <= 0 or price <= 0 or side not in ("BUY", "SELL"):
        return None
    seg = str(r.get("exchangeSegment") or "")
    opt = str(r.get("drvOptionType") or "").upper()
    kind = kind_of(seg, r.get("instrument"), opt)
    product = str(r.get("productType") or "").upper()
    real = [r.get(k) for k in CHARGE_FIELDS]
    if source == "history" and any(v not in (None, "") for v in real):
        charges, est = round(sum(_f(v) for v in real), 2), False
    else:
        charges, est = estimate_charges(kind, product, side, qty, price), True
    oid = str(r.get("orderId") or "")
    xid = str(r.get("exchangeTradeId") or "")
    uid = f"{oid}|{xid}" if xid and xid not in ("0", "NA") else f"{oid}|{t}|{qty}|{price}"
    strike = _f(r.get("drvStrikePrice"))
    return {"uid": uid, "order_id": oid, "time": t, "date": t[:10], "seg": seg, "sid": str(r.get("securityId") or ""),
            "sym": str(r.get("customSymbol") or r.get("tradingSymbol") or "").strip(), "kind": kind,
            "product": product, "side": side, "qty": qty, "price": price, "charges": charges, "est": est,
            "expiry": str(r.get("drvExpiryDate") or "")[:10] if str(r.get("drvExpiryDate") or "")[:4] not in ("", "0001", "NA") else "",
            "strike": strike if strike > 0 else 0, "opt": "CE" if opt in ("CALL", "CE") else "PE" if opt in ("PUT", "PE") else "",
            "source": source}


class Store:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS fills (uid TEXT PRIMARY KEY, date TEXT, source TEXT, data TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:                                   # commit, or roll back on error
                yield db
        finally:
            db.close()

    def meta(self, k, default=None):
        with self.lock, self._db() as db:
            row = db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_meta(self, k, v):
        with self.lock, self._db() as db:
            db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (k, json.dumps(v)))

    def put_history(self, rows):
        """Dhan trade-history rows; they replace today's-book rows of the same dates."""
        fills = [f for f in (normalize(r, "history") for r in rows) if f]
        dates = sorted({f["date"] for f in fills})
        with self.lock, self._db() as db:
            for d in dates:
                db.execute("DELETE FROM fills WHERE date=? AND source='day'", (d,))
            db.executemany("INSERT OR REPLACE INTO fills VALUES (?,?,?,?)",
                           [(f["uid"], f["date"], "history", json.dumps(f)) for f in fills])
        return len(fills)

    def put_day(self, rows, today):
        fills = [f for f in (normalize(r, "day") for r in rows) if f and f["date"] == today]
        with self.lock, self._db() as db:
            if db.execute("SELECT 1 FROM fills WHERE date=? AND source='history' LIMIT 1", (today,)).fetchone():
                return 0
            db.execute("DELETE FROM fills WHERE date=? AND source='day'", (today,))
            db.executemany("INSERT OR REPLACE INTO fills VALUES (?,?,?,?)",
                           [(f["uid"], f["date"], "day", json.dumps(f)) for f in fills])
        return len(fills)

    def fills(self, since=None):
        with self.lock, self._db() as db:
            q = "SELECT data FROM fills" + (" WHERE date>=?" if since else "")
            rows = db.execute(q, (since,) if since else ()).fetchall()
        out = [json.loads(r[0]) for r in rows]
        out.sort(key=lambda f: (f["time"], f["uid"]))
        return out

    def count(self):
        with self.lock, self._db() as db:
            return db.execute("SELECT COUNT(*), MIN(date), MAX(date) FROM fills").fetchone()


# ---- round trips --------------------------------------------------------------------------
def category(kind, product, side):
    if kind == "OPTIDX":
        return "Index options · bought" if side == "LONG" else "Index options · sold"
    if kind == "OPTSTK":
        return "Stock options · bought" if side == "LONG" else "Stock options · sold"
    if kind == "FUTIDX":
        return "Index futures"
    if kind == "FUTSTK":
        return "Stock futures"
    if kind == "EQUITY":
        return "Stocks · intraday" if product == "INTRADAY" else "Stocks · delivery"
    if kind in ("FUTCOM", "OPTFUT"):
        return "Commodity (MCX)"
    return "Currency"


def round_trips(fills):
    """Group fills into trades: from a flat position back to flat, per instrument and product.
    Returns (closed trades, open trades)."""
    books = {}
    closed = []
    for f in fills:
        prod = "INTRADAY" if f["product"] == "INTRADAY" else ("DELIVERY" if f["kind"] == "EQUITY" else "CARRY")
        key = (f["seg"], f["sid"], prod)
        b = books.get(key)
        signed = f["qty"] if f["side"] == "BUY" else -f["qty"]
        if b is None or b["net"] == 0:
            if prod == "DELIVERY" and signed < 0:
                continue                               # selling shares bought before the import
            b = books[key] = {"key": key, "sym": f["sym"], "kind": f["kind"], "product": f["product"],
                              "side": "LONG" if signed > 0 else "SHORT", "net": 0, "fills": [],
                              "buy_val": 0.0, "sell_val": 0.0, "buy_qty": 0, "sell_qty": 0, "charges": 0.0,
                              "max_qty": 0, "avg": 0.0, "adds": 0, "avg_down": False, "expiry": f["expiry"],
                              "opt": f["opt"], "strike": f["strike"], "est": False}
        qty_left = f["qty"]
        # A fill that flips the position closes this trade and opens a new one with the rest.
        if b["net"] != 0 and (b["net"] > 0) != (signed > 0) and abs(signed) > abs(b["net"]):
            close_qty = abs(b["net"])
            part = dict(f, qty=close_qty, charges=f["charges"] * close_qty / f["qty"])
            _apply(b, part)
            closed.append(_finish(b))
            qty_left = f["qty"] - close_qty
            b = books[key] = {"key": key, "sym": f["sym"], "kind": f["kind"], "product": f["product"],
                              "side": "LONG" if signed > 0 else "SHORT", "net": 0, "fills": [],
                              "buy_val": 0.0, "sell_val": 0.0, "buy_qty": 0, "sell_qty": 0, "charges": 0.0,
                              "max_qty": 0, "avg": 0.0, "adds": 0, "avg_down": False, "expiry": f["expiry"],
                              "opt": f["opt"], "strike": f["strike"], "est": False}
            f = dict(f, qty=qty_left, charges=f["charges"] * qty_left / (close_qty + qty_left))
        _apply(b, f)
        if b["net"] == 0:
            closed.append(_finish(b))
    open_ = [b for b in books.values() if b["net"] != 0]
    return closed, open_


def _apply(b, f):
    signed = f["qty"] if f["side"] == "BUY" else -f["qty"]
    adding = b["net"] == 0 or (b["net"] > 0) == (signed > 0)
    if adding and b["net"] != 0:
        b["adds"] += 1
        worse = f["price"] < b["avg"] if b["net"] > 0 else f["price"] > b["avg"]
        if worse:
            b["avg_down"] = True
    if adding:
        tot = abs(b["net"]) + f["qty"]
        b["avg"] = (b["avg"] * abs(b["net"]) + f["price"] * f["qty"]) / tot
    b["net"] += signed
    b["max_qty"] = max(b["max_qty"], abs(b["net"]))
    if f["side"] == "BUY":
        b["buy_val"] += f["qty"] * f["price"]
        b["buy_qty"] += f["qty"]
    else:
        b["sell_val"] += f["qty"] * f["price"]
        b["sell_qty"] += f["qty"]
    b["charges"] += f["charges"]
    b["est"] = b["est"] or f.get("est", False)
    b["fills"].append(f)


def _finish(b):
    fills = b["fills"]
    t0, t1 = fills[0]["time"], fills[-1]["time"]
    gross = b["sell_val"] - b["buy_val"]
    entry_val = b["buy_val"] if b["side"] == "LONG" else b["sell_val"]
    try:
        held = (datetime.strptime(t1, "%Y-%m-%d %H:%M:%S") - datetime.strptime(t0, "%Y-%m-%d %H:%M:%S")).total_seconds() / 60
    except ValueError:
        held = 0
    return {"sym": b["sym"], "kind": b["kind"], "product": b["product"], "side": b["side"],
            "cat": category(b["kind"], b["product"], b["side"]),
            "entry": t0, "exit": t1, "date": t0[:10], "held_min": round(held, 1), "qty": b["max_qty"],
            "entry_value": round(entry_val, 2), "gross": round(gross, 2), "charges": round(b["charges"], 2),
            "net": round(gross - b["charges"], 2), "fills": len(fills), "adds": b["adds"], "avg_down": b["avg_down"],
            "expiry_day": bool(b["expiry"]) and b["expiry"] == t0[:10] and b["kind"].startswith("OPT"),
            "est": b["est"], "opt": b["opt"]}


# ---- statistics ---------------------------------------------------------------------------
def summary(trades):
    n = len(trades)
    wins = [t for t in trades if t["net"] > 0]
    losses = [t for t in trades if t["net"] <= 0]
    gw = sum(t["net"] for t in wins)
    gl = -sum(t["net"] for t in losses)
    net = sum(t["net"] for t in trades)
    return {"trades": n, "wins": len(wins), "losses": len(losses),
            "win_rate": round(100 * len(wins) / n, 1) if n else 0,
            "net": round(net, 2), "gross": round(sum(t["gross"] for t in trades), 2),
            "charges": round(sum(t["charges"] for t in trades), 2),
            "avg_win": round(gw / len(wins), 2) if wins else 0, "avg_loss": round(-gl / len(losses), 2) if losses else 0,
            "profit_factor": round(gw / gl, 2) if gl > 0 else (None if not wins else 99),
            "expectancy": round(net / n, 2) if n else 0,
            "best": max((t["net"] for t in trades), default=0), "worst": min((t["net"] for t in trades), default=0)}


def _bucket_hour(t):
    hm = t["entry"][11:16]
    for edge, label in (("09:30", "9:15–9:30"), ("10:00", "9:30–10:00"), ("11:00", "10–11"), ("12:00", "11–12"),
                        ("13:00", "12–1 PM"), ("14:00", "1–2 PM"), ("14:30", "2–2:30 PM"), ("15:00", "2:30–3 PM")):
        if hm < edge:
            return label
    return "After 3 PM"


def _bucket_hold(t):
    m = t["held_min"]
    if t["entry"][:10] != t["exit"][:10]:
        return "Overnight or longer"
    return "Under 5 min" if m < 5 else "5–15 min" if m < 15 else "15–60 min" if m < 60 else "Over 1 hour"


def breakdown(trades, keyfn, order=None):
    groups = defaultdict(list)
    for t in trades:
        groups[keyfn(t)].append(t)
    rows = [dict(summary(v), label=k) for k, v in groups.items()]
    if order:
        rows.sort(key=lambda r: order.index(r["label"]) if r["label"] in order else 99)
    else:
        rows.sort(key=lambda r: -r["net"])
    return rows


HOUR_ORDER = ["9:15–9:30", "9:30–10:00", "10–11", "11–12", "12–1 PM", "1–2 PM", "2–2:30 PM", "2:30–3 PM", "After 3 PM"]
HOLD_ORDER = ["Under 5 min", "5–15 min", "15–60 min", "Over 1 hour", "Overnight or longer"]


def rupees(v):
    s = "−" if v < 0 else ""
    v = abs(v)
    if v >= 1e7:
        return f"{s}₹{v / 1e7:.2f} Cr"
    if v >= 1e5:
        return f"{s}₹{v / 1e5:.2f} L"
    return f"{s}₹{v:,.0f}"


def habits(trades, max_trades_day=5):
    """Plain-language findings about how you trade, each with what it cost (or made)."""
    out = []
    if len(trades) < 5:
        return out
    trades = sorted(trades, key=lambda t: t["entry"])
    base = summary(trades)

    def add(key, title, text, impact, good=False, evidence=None):
        out.append({"key": key, "title": title, "text": text, "impact": round(impact, 2), "good": good,
                    "evidence": evidence or {}})

    # 1. Overtrading
    by_day = defaultdict(list)
    for t in trades:
        by_day[t["date"]].append(t)
    busy = [d for d, v in by_day.items() if len(v) > max_trades_day]
    if len(busy) >= 2 and len(by_day) - len(busy) >= 2:
        busy_net = sum(t["net"] for d in busy for t in by_day[d])
        calm = [d for d in by_day if d not in busy]
        calm_net = sum(t["net"] for d in calm for t in by_day[d])
        per_busy, per_calm = busy_net / len(busy), calm_net / len(calm)
        if per_busy < per_calm:
            add("overtrading", "Too many trades in a day",
                f"On the {len(busy)} days you took more than {max_trades_day} trades you averaged {rupees(per_busy)} a day; "
                f"on the other {len(calm)} days, {rupees(per_calm)}. Stop after {max_trades_day} trades.",
                busy_net - per_calm * len(busy), evidence={"busy_days": len(busy), "calm_days": len(calm)})

    # 2. Revenge trading: a new trade within 15 minutes of a losing exit on the same day
    revenge, after_loss_sizes, normal_sizes = [], [], []
    closed_so_far = []
    for t in trades:
        recent = [c for c in closed_so_far if c["exit"][:10] == t["entry"][:10] and c["exit"] <= t["entry"]]
        last = max(recent, key=lambda c: c["exit"]) if recent else None
        if last and last["net"] < 0:
            gap = (datetime.strptime(t["entry"], "%Y-%m-%d %H:%M:%S") -
                   datetime.strptime(last["exit"], "%Y-%m-%d %H:%M:%S")).total_seconds() / 60
            if gap <= 15:
                revenge.append(t)
            after_loss_sizes.append(t["entry_value"])
        else:
            normal_sizes.append(t["entry_value"])
        closed_so_far.append(t)
    if len(revenge) >= 3:
        s = summary(revenge)
        if s["net"] < 0 or s["win_rate"] < base["win_rate"]:
            add("revenge", "Jumping back in after a loss",
                f"{len(revenge)} trades started within 15 minutes of a losing trade. They won {s['win_rate']:.0f}% of the time "
                f"(your usual is {base['win_rate']:.0f}%) and made {rupees(s['net'])}. After a loss, wait 15 minutes.",
                s["net"] if s["net"] < 0 else s["net"] - base["expectancy"] * len(revenge))
    if len(after_loss_sizes) >= 3 and normal_sizes:
        a, n = sum(after_loss_sizes) / len(after_loss_sizes), sum(normal_sizes) / len(normal_sizes)
        if n > 0 and a > 1.3 * n:
            add("size_after_loss", "Bigger trades after a loss",
                f"After a losing trade your next trade is {a / n:.1f}× your usual size ({rupees(a)} vs {rupees(n)}). "
                f"Keep the size the same.", 0)

    # 3. Holding losers, cutting winners
    wins = [t for t in trades if t["net"] > 0]
    losses = [t for t in trades if t["net"] <= 0]
    if len(wins) >= 3 and len(losses) >= 3:
        hw = sum(t["held_min"] for t in wins) / len(wins)
        hl = sum(t["held_min"] for t in losses) / len(losses)
        if hl > 1.5 * hw and hl > 5:
            add("hold_losers", "Holding losers longer than winners",
                f"You hold a losing trade {hl:.0f} min on average, but a winner only {hw:.0f} min. "
                f"Losers get time to grow; winners don't. Exit losers at your stop.", 0,
                evidence={"loss_hold": hl, "win_hold": hw})
        aw, al = base["avg_win"], -base["avg_loss"]
        if al > 0 and aw < al:
            need = 100 * al / (aw + al)
            add("small_wins", "Small wins, big losses",
                f"Your average win is {rupees(aw)} and your average loss {rupees(al)}. With wins that small you need to "
                f"win {need:.0f}% of trades to break even; you win {base['win_rate']:.0f}%.",
                0, evidence={"breakeven_win_rate": need})
        top = sorted(losses, key=lambda t: t["net"])[:5]
        total_loss = -sum(t["net"] for t in losses)
        if len(losses) >= 10 and total_loss > 0:
            share = -sum(t["net"] for t in top) / total_loss
            if share > 0.4:
                add("big_losses", "A few big losses do most of the damage",
                    f"Your 5 biggest losses cost {rupees(-sum(t['net'] for t in top))}, {share * 100:.0f}% of everything "
                    f"you lost. A stop-loss on every trade caps these.", sum(t["net"] for t in top))

    # 4. Averaging down
    avg_down = [t for t in trades if t["avg_down"]]
    if len(avg_down) >= 3:
        s = summary(avg_down)
        if s["net"] < 0:
            add("avg_down", "Adding to losing trades",
                f"{len(avg_down)} times you added to a trade that was going against you. Those trades lost "
                f"{rupees(s['net'])} in total. Don't average down.", s["net"])

    # 5. Times of day
    for label, test, advice in (("First 15 minutes", lambda t: t["entry"][11:16] < "09:30", "Let the market settle until 9:30."),
                                ("After 2:30 PM", lambda t: t["entry"][11:16] >= "14:30" and t["product"] == "INTRADAY",
                                 "Stop taking new intraday trades at 2:30.")):
        grp = [t for t in trades if test(t)]
        if len(grp) >= 5:
            s = summary(grp)
            if s["net"] < 0:
                add("time_" + label[:5].lower().replace(" ", ""), f"Trades in the {label.lower()}" if label.startswith("First") else f"Trades {label.lower()}",
                    f"{len(grp)} trades, {s['win_rate']:.0f}% won, {rupees(s['net'])} in total. {advice}", s["net"])

    # 6. Expiry-day options
    exp = [t for t in trades if t["expiry_day"]]
    if len(exp) >= 5:
        s = summary(exp)
        if s["net"] < 0:
            add("expiry", "Options on expiry day",
                f"{len(exp)} option trades on the contract's own expiry day lost {rupees(s['net'])} "
                f"({s['win_rate']:.0f}% won). Time decay is fastest that day.", s["net"])

    # 7. What works and what doesn't
    cats = breakdown(trades, lambda t: t["cat"])
    for c in cats:
        if c["trades"] >= 5 and c["net"] < 0 and c["net"] == min(x["net"] for x in cats):
            add("worst_cat", f"{c['label']} lose money",
                f"{c['trades']} trades, {c['win_rate']:.0f}% won, {rupees(c['net'])}. Trade these smaller or stop.", c["net"])
        if c["trades"] >= 5 and c["net"] > 0 and c["net"] == max(x["net"] for x in cats):
            add("best_cat", f"Your edge: {c['label'].lower()}",
                f"{c['trades']} trades, {c['win_rate']:.0f}% won, {rupees(c['net'])}. Spend more of your attention here.",
                c["net"], good=True)

    # 8. Charges
    if base["gross"] > 0 and base["charges"] > 0.3 * base["gross"]:
        add("charges", "Charges eat your profit",
            f"Charges and taxes were {rupees(base['charges'])}, {100 * base['charges'] / base['gross']:.0f}% of your gross profit. "
            f"Fewer, better trades keep more of it.", -base["charges"])
    elif base["gross"] <= 0 and base["charges"] > 0:
        add("charges", "Charges on top of losses",
            f"Before charges you made {rupees(base['gross'])}; charges and taxes cost another {rupees(base['charges'])}.",
            -base["charges"])

    out.sort(key=lambda h: (h["good"], h["impact"]))
    return out


def equity_curve(trades):
    by_day = defaultdict(float)
    for t in trades:
        by_day[t["exit"][:10]] += t["net"]
    run, peak, dd, curve = 0.0, 0.0, 0.0, []
    for d in sorted(by_day):
        run += by_day[d]
        peak = max(peak, run)
        dd = min(dd, run - peak)
        curve.append({"date": d, "day": round(by_day[d], 2), "total": round(run, 2)})
    return curve, round(dd, 2)


def day_card(trades, date, max_trades_day=5):
    today = [t for t in trades if t["exit"][:10] == date]
    s = summary(today)
    notes = []
    if s["trades"] > max_trades_day:
        notes.append(f"{s['trades']} trades: more than your {max_trades_day}.")
    ad = [t for t in today if t["avg_down"]]
    if ad:
        notes.append(f"Added to a losing trade {len(ad)}×.")
    first = [t for t in today if t["entry"][11:16] < "09:30"]
    if first:
        notes.append(f"{len(first)} trade(s) in the first 15 minutes ({rupees(sum(t['net'] for t in first))}).")
    if s["charges"] and s["gross"] > 0 and s["charges"] > 0.3 * s["gross"]:
        notes.append(f"Charges took {100 * s['charges'] / s['gross']:.0f}% of the gross.")
    return {"date": date, "summary": s, "notes": notes,
            "trades": sorted(today, key=lambda t: t["entry"])}


def _ends(rows, top=8, bottom=5):
    return rows if len(rows) <= top + bottom else rows[:top] + rows[-bottom:]


def report(fills, days=None, max_trades_day=5, today=None):
    today = today or datetime.now(IST).strftime("%Y-%m-%d")
    closed, open_ = round_trips(fills)
    if days:
        since = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=days)).strftime("%Y-%m-%d")
        closed = [t for t in closed if t["exit"][:10] >= since]
    curve, max_dd = equity_curve(closed)
    return {
        "summary": summary(closed), "max_drawdown": max_dd, "curve": curve,
        "by_category": breakdown(closed, lambda t: t["cat"]),
        "by_hour": breakdown(closed, _bucket_hour, HOUR_ORDER),
        "by_weekday": breakdown(closed, lambda t: WEEKDAYS[datetime.strptime(t["date"], "%Y-%m-%d").weekday()], WEEKDAYS),
        "by_hold": breakdown(closed, _bucket_hold, HOLD_ORDER),
        "by_side": breakdown(closed, lambda t: "Long (bought first)" if t["side"] == "LONG" else "Short (sold first)"),
        "by_symbol": _ends(breakdown(closed, lambda t: t["sym"])),
        "habits": habits(closed, max_trades_day),
        "today": day_card(closed, today, max_trades_day),
        "recent": sorted(closed, key=lambda t: t["exit"], reverse=True)[:60],
        "open": len(open_), "estimated": any(t["est"] for t in closed),
    }
