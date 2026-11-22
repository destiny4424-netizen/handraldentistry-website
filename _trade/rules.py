"""Rules mode: four rules for manual trades, enforced by the app.

  1. Level first      an order that opens a position needs a key level: yesterday's or last week's
                      high/low, a daily swing high/low, or an imbalance zone (FVG, inverse FVG, BPR, gap).
                      Price must be near it (within 0.3 of a daily ATR). No level, no trade.
  2. Confirmation     optional per order (on by default): the order waits at the server until a closed
                      15-minute candle reacts at the level (touches it and closes back beyond it, in the trade's
                      direction) and only then goes to Dhan. Expires at 3 PM, or if the stop trades first.
  3. Risk first       a stop-loss is required. The quantity is worked out from it (1% of capital at risk) and
                      can't be bigger. The order goes to Dhan as a Super order, with its stop.
  4. Routine          a plan for the day (from 9 AM) and a short journal after the close, kept day by day.

Closing a position is never blocked. For futures and options the level is on the underlying (the index or the
stock); the stop and the size are on what you trade. Rules can't be switched off while the market is open.
"""
import json
import math
import os
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone

from swing import pivots, zones                          # the scanner's daily-chart tools

IST = timezone(timedelta(hours=5, minutes=30))
DEFAULTS = {"on": True, "risk_pct": 1.0, "capital": 150000, "near_atr": 0.3, "confirm_default": True}


def atr(d, n=14):
    out, prev, avg = [], None, None
    for k in d:
        tr = k["h"] - k["l"] if prev is None else max(k["h"] - k["l"], abs(k["h"] - prev), abs(k["l"] - prev))
        avg = tr if avg is None else (avg * (n - 1) + tr) / n
        out.append(avg)
        prev = k["c"]
    return out[-1] if out else 0


def _rows(d):
    ks = ("timestamp", "open", "high", "low", "close", "volume")
    if not isinstance(d, dict) or not all(isinstance(d.get(k), list) for k in ks):
        return []
    return [{"t": int(t), "o": float(o), "h": float(h), "l": float(l), "c": float(c), "v": float(v)}
            for t, o, h, l, c, v in zip(*(d[k] for k in ks))]


def _day(t):
    return datetime.fromtimestamp(int(t), IST)


def find_levels(daily, ltp, near_atr=0.3, today=None):
    """Key levels from completed daily candles (oldest first), nearest to the price first."""
    if len(daily) < 20 or not ltp:
        return {"atr": 0, "levels": []}
    a = atr(daily) or ltp * 0.02
    raw = []
    y = daily[-1]
    raw += [("PDH", "Yesterday's high", y["h"]), ("PDL", "Yesterday's low", y["l"])]
    weeks = {}
    for c in daily:
        weeks.setdefault(_day(c["t"]).isocalendar()[:2], []).append(c)
    this_week = tuple((today or datetime.now(IST)).isocalendar()[:2])
    prev_weeks = [w for w in sorted(weeks) if w < this_week]
    if prev_weeks:
        last_week = weeks[prev_weeks[-1]]
        raw += [("PWH", "Last week's high", max(c["h"] for c in last_week)),
                ("PWL", "Last week's low", min(c["l"] for c in last_week))]
    hi, lo = pivots(daily[-60:], k=3)
    for _, p in hi[-4:]:
        raw.append(("SWH", "Swing high", p))
    for _, p in lo[-4:]:
        raw.append(("SWL", "Swing low", p))
    for z in zones(daily[-80:], a):
        if z["side"] == "bull":
            raw.append(("ZONE", f"Bullish {z['type']} (support)", z["top"]))
        else:
            raw.append(("ZONE", f"Bearish {z['type']} (resistance)", z["bottom"]))
    rank = {"PDH": 0, "PDL": 0, "PWH": 1, "PWL": 1, "SWH": 2, "SWL": 2, "ZONE": 3}
    merged = []
    for code, name, p in sorted(raw, key=lambda x: x[2]):
        if merged and abs(p - merged[-1]["price"]) <= 0.15 * a:
            m = merged[-1]
            if name not in m["names"]:
                m["names"].append(name)
                m["names"].sort(key=lambda n: min(rank[c] for c, nm, _ in raw if nm == n))
                m["code"] = min((m["code"], code), key=lambda c: rank[c])
            continue
        merged.append({"code": code, "names": [name], "price": round(p, 2)})
    for m in merged:                                   # "Yesterday's low +2": the most important name first
        m["strength"] = len(m["names"])
        m["name"] = m["names"][0] + (f" +{len(m['names']) - 1}" if len(m["names"]) > 1 else "")
        m["detail"] = " · ".join(m["names"])
    tol = near_atr * a
    for m in merged:
        m["dist"] = round(ltp - m["price"], 2)
        m["dist_pct"] = round(100 * (ltp - m["price"]) / ltp, 2)
        m["near"] = abs(ltp - m["price"]) <= tol
        m["id"] = f"{m['code']}@{m['price']}"
    merged.sort(key=lambda m: abs(m["dist"]))
    return {"atr": round(a, 2), "tol": round(tol, 2), "levels": merged[:12]}


def reaction(candle, level, tol, bull):
    """A closed 15-minute candle that touched the level and closed back beyond it in the trade's direction."""
    if bull:
        return candle["l"] <= level + tol and candle["c"] > level and candle["c"] > candle["o"]
    return candle["h"] >= level - tol and candle["c"] < level and candle["c"] < candle["o"]


class Rules:
    def __init__(self, data_dir, dhan, instruments, quotes, now_fn, audit=lambda *a, **k: None, demo=False):
        self.demo = demo
        self.path = os.path.join(data_dir, "rules.json")
        self.D, self.INST, self.QUOTES, self.now, self.audit = dhan, instruments, quotes, now_fn, audit
        self.lock = threading.RLock()
        self.daily = {}                                   # underlying key -> (day, candles)
        self.m15 = {}                                     # underlying key -> (time, candles)
        self.prices = {}                                  # key -> (last price, time), for levels and the plan
        self.place_fn = None                              # set by the server: places a checked order
        try:
            self.s = json.load(open(self.path))
        except (OSError, ValueError):
            self.s = {}
        for k, v in (("settings", {}), ("armed", []), ("sent", {}), ("journal", {}), ("log", [])):
            self.s.setdefault(k, v)

    def save(self):
        with self.lock:
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self.s, f, indent=1)
            os.replace(tmp, self.path)

    def cfg(self):
        return dict(DEFAULTS, **self.s["settings"])

    def market_open(self):
        t = self.now()
        return t.weekday() < 5 and (9, 15) <= (t.hour, t.minute) < (15, 30)

    def locked(self):
        """Settings can't change while the market is open (not on the demo account, so it can be tried any time)."""
        return self.market_open() and not self.demo

    def today(self):
        return self.now().strftime("%Y-%m-%d")

    def update_settings(self, new):
        if self.locked():
            return "Rules can't be changed while the market is open (9:15–3:30)."
        lim = {"risk_pct": (0.1, 2.0), "capital": (10000, 1e8), "near_atr": (0.1, 1.0)}
        clean = {}
        for k, (lo, hi) in lim.items():
            if new.get(k) not in (None, ""):
                v = float(new[k])
                if not lo <= v <= hi:
                    return f"{k.replace('_', ' ')} must be between {lo:g} and {hi:g}."
                clean[k] = v
        for k in ("on", "confirm_default"):
            if k in new:
                clean[k] = bool(new[k])
        with self.lock:
            self.s["settings"].update(clean)
            self.save()
        self.audit("rules_settings", change=clean)
        return ""

    # -- levels --------------------------------------------------------------------------------------
    def underlying(self, it):
        if it["kind"] in ("EQUITY", "INDEX"):
            return it["key"]
        return self.INST.under.get(it["ul"])

    def prefetch(self, keys):
        """One price request for many instruments (Dhan allows one a second)."""
        need = [k for k in keys if not ((self.QUOTES.get(k) or {}).get("ltp") and time.time() - self.QUOTES[k].get("t", 0) < 60)
                and not (k in self.prices and time.time() - self.prices[k][1] < 60)]
        by = {}
        for k in need:
            seg, sid = k.split(":")
            by.setdefault(seg, []).append(sid)
        if not by:
            return
        try:
            for (seg, sid), v in self.D.quote(by, "ltp").items():
                if v.get("last_price"):                  # its own cache: the app's full quotes stay as they are
                    self.prices[f"{seg}:{sid}"] = (float(v["last_price"]), time.time())
        except Exception:
            pass

    def ltp(self, key):
        q = self.QUOTES.get(key)
        if q and q.get("ltp") and time.time() - q.get("t", 0) < 60:
            return float(q["ltp"])
        if key in self.prices and time.time() - self.prices[key][1] < 60:
            return self.prices[key][0]
        seg, sid = key.split(":")
        try:
            v = self.D.quote({seg: [sid]}, "ltp").get((seg, sid)) or {}
        except Exception:
            return None
        return float(v.get("last_price") or 0) or None

    def daily_of(self, ukey):
        day = self.today()
        hit = self.daily.get(ukey)
        if hit and hit[0] == day:
            return hit[1]
        it = self.INST.get(ukey)
        seg, sid = ukey.split(":")
        t = self.now()
        rows = _rows(self.D.daily(sid, seg, "INDEX" if seg == "IDX_I" else "EQUITY",
                                  (t - timedelta(days=160)).strftime("%Y-%m-%d"), day))
        rows = [c for c in rows if _day(c["t"]).strftime("%Y-%m-%d") < day]
        self.daily[ukey] = (day, rows)
        return rows

    def levels_for(self, key):
        it = self.INST.get(key)
        if not it:
            return {"error": "Unknown instrument"}
        ukey = self.underlying(it)
        if not ukey:
            return {"error": "No underlying found for this contract, so its levels can't be worked out."}
        try:
            d = self.daily_of(ukey)
        except Exception as e:
            return {"error": f"Couldn't read daily candles: {e}"}
        px = self.ltp(ukey)
        out = find_levels(d, px, self.cfg()["near_atr"], self.now())
        u = self.INST.get(ukey)
        out.update(ukey=ukey, under=u["sym"] if u else ukey, ltp=px, on_underlying=ukey != key)
        return out

    # -- checks ----------------------------------------------------------------------------------------
    def opened_today(self):
        return [x for x in self.s["sent"].get(self.today(), []) if x.get("opening")]

    def max_qty(self, entry, stop, lot):
        risk_unit = abs(entry - stop)
        if risk_unit <= 0:
            return 0
        c = self.cfg()
        q = math.floor(c["capital"] * c["risk_pct"] / 100 / risk_unit)
        return (q // lot) * lot if lot > 1 else q

    def check(self, b, o, it, max_trades, internal=False):
        """Problems with an order that opens a position, and what the rules worked out for it."""
        c = self.cfg()
        probs, info = [], {"rules": True}
        if not c["on"]:
            return [], {"rules": False}
        n = len(self.opened_today()) + sum(1 for a in self.s["armed"] if a["day"] == self.today())
        if max_trades and n >= max_trades and not internal:
            probs.append(f"You've taken {n} trades today: your limit is {int(max_trades)}. Done for today.")
        sup = o.get("super") or {}
        stop = float(sup.get("stopLossPrice") or 0)
        if o["orderType"] not in ("LIMIT", "MARKET"):
            probs.append("In Rules mode, enter with a Limit or Market order; the stop-loss goes in with it.")
        if not stop:
            probs.append("Rule 3: set your stop-loss before you enter. The size is worked out from it.")
        entry = float(sup.get("price") or o.get("price") or 0)
        lot = it["lot"] if it["seg"] not in ("NSE_EQ", "BSE_EQ") else 1
        if stop and entry:
            mq = self.max_qty(entry, stop, lot)
            risk_amt = c["capital"] * c["risk_pct"] / 100
            info.update(max_qty=mq, risk_allowed=round(risk_amt, 2), risk_unit=round(abs(entry - stop), 2))
            if mq <= 0:
                probs.append(f"Even {'1 lot' if lot > 1 else '1 share'} risks {abs(entry - stop) * lot:,.0f}, more than "
                             f"your ₹{risk_amt:,.0f}. Choose a closer stop, or skip this one.")
            elif o["quantity"] > mq:
                probs.append(f"Rule 3: at most {mq:,} {'units' if lot > 1 else 'shares'} with this stop "
                             f"(₹{risk_amt:,.0f} risk). You asked for {o['quantity']:,}.")
        if not internal:
            lv = self.levels_for(it["key"])
            info["levels"] = lv
            want = str(b.get("level") or "")
            chosen = next((x for x in lv.get("levels", []) if x["id"] == want), None)
            if lv.get("error"):
                probs.append(lv["error"])
            elif not chosen:
                near = [x for x in lv.get("levels", []) if x["near"]]
                probs.append("Rule 1: pick the level you're trading." if near else
                             "Rule 1: no level near the price, so no trade. " +
                             (f"Nearest: {lv['levels'][0]['name']} {lv['levels'][0]['price']:,} "
                              f"({abs(lv['levels'][0]['dist_pct'])}% away)." if lv.get("levels") else ""))
            elif not chosen["near"]:
                probs.append(f"Rule 1: {chosen['name']} {chosen['price']:,} is {abs(chosen['dist_pct'])}% away, "
                             "not near enough. Wait for price to come to it.")
            else:
                info["level"] = chosen
        return probs, info

    def record_sent(self, o, it, reply, level, opening, confirmed=False):
        rows = reply if isinstance(reply, list) else [reply]
        with self.lock:
            self.s["sent"].setdefault(self.today(), []).append({
                "at": self.now().strftime("%H:%M"), "sym": it["name"], "side": o["transactionType"],
                "qty": o["quantity"], "opening": opening, "level": (level or {}).get("name"),
                "level_price": (level or {}).get("price"), "confirmed": confirmed,
                "stop": (o.get("super") or {}).get("stopLossPrice"),
                "order_ids": [str(r.get("orderId")) for r in rows if isinstance(r, dict) and r.get("orderId")]})
            for d in sorted(self.s["sent"])[:-60]:
                self.s["sent"].pop(d)
            self.save()

    # -- confirmation (rule 2) -------------------------------------------------------------------------
    def arm(self, b, o, it, level, bull):
        a = {"id": f"c{int(time.time() * 1000)}", "day": self.today(), "at": self.now().strftime("%H:%M"),
             "since": int(self.now().timestamp()), "ticket": {k: v for k, v in b.items() if k not in ("confirm",)},
             "key": it["key"], "name": it["name"], "ukey": level["_ukey"], "under": level["_under"],
             "level": {k: level[k] for k in ("id", "name", "price")}, "tol": level["_tol"], "bull": bull,
             "side": o["transactionType"], "qty": o["quantity"], "stop": o["super"]["stopLossPrice"],
             "risk_unit": abs(o["super"]["price"] - o["super"]["stopLossPrice"]), "status": "waiting",
             "lot": it["lot"] if it["seg"] not in ("NSE_EQ", "BSE_EQ") else 1}
        with self.lock:
            self.s["armed"].append(a)
            self.save()
        self.audit("rules_armed", sym=it["sym"], level=a["level"], side=a["side"], qty=a["qty"])
        return a

    def cancel(self, cid):
        with self.lock:
            for a in self.s["armed"]:
                if a["id"] == cid and a["status"] == "waiting":
                    a["status"] = "cancelled"
                    self.save()
                    return True
        return False

    def candles15(self, ukey):
        hit = self.m15.get(ukey)
        if hit and time.time() - hit[0] < 60:
            return hit[1]
        seg, sid = ukey.split(":")
        t = self.now()
        rows = _rows(self.D.intraday(sid, seg, "INDEX" if seg == "IDX_I" else "EQUITY", "15",
                                     t.strftime("%Y-%m-%d 09:15:00"), t.strftime("%Y-%m-%d %H:%M:%S")))
        self.m15[ukey] = (time.time(), rows)
        return rows

    def tick(self):
        """Every 30 s in market hours: check the orders waiting for confirmation."""
        waiting = [a for a in self.s["armed"] if a["status"] == "waiting"]
        if not waiting:
            return
        t = self.now()
        for a in waiting:
            try:
                if a["day"] != self.today() or (t.hour, t.minute) >= (15, 0):
                    a["status"] = "expired"
                    self._log(a, "No confirmation by 3 PM: not taken.")
                    continue
                px = self.ltp(a["key"])
                if px and ((a["side"] == "BUY" and px <= a["stop"]) or (a["side"] == "SELL" and px >= a["stop"])):
                    a["status"] = "invalid"
                    self._log(a, f"Price {px} reached the stop before any confirmation: not taken.")
                    continue
                closed = [c for c in self.candles15(a["ukey"])            # 15-min candles that closed after arming
                          if a["since"] < c["t"] + 900 <= t.timestamp() - 15]
                hit = next((c for c in closed if reaction(c, a["level"]["price"], a["tol"], a["bull"])), None)
                if not hit:
                    continue
                if not px:
                    continue
                if abs(px - a["stop"]) > 2 * a["risk_unit"]:
                    a["status"] = "invalid"
                    self._log(a, f"Confirmed, but price {px} ran too far from the stop: risk would double. Not taken.")
                    continue
                a["status"] = "confirmed"
                a["confirmed_at"] = t.strftime("%H:%M")
                qty = min(a["qty"], self.max_qty(px, a["stop"], a.get("lot", 1)))   # risk stays within 1% at today's price
                if qty <= 0:
                    a["status"] = "invalid"
                    self._log(a, "Confirmed, but at this price even the smallest size risks too much. Not taken.")
                    continue
                tk = dict(a["ticket"], type="MARKET", price=0, qty=qty)
                code, r = self.place_fn(tk, internal=True, level=a["level"])
                if code == 200:
                    a["status"] = "sent"
                    self._log(a, f"Confirmed on the {_day(hit['t']).strftime('%H:%M')} candle (close {hit['c']}). Order sent.")
                else:
                    a["status"] = "failed"
                    self._log(a, f"Confirmed, but the order failed: {r.get('error')}")
            except Exception as e:
                traceback.print_exc()
                a["note"] = f"Check failed: {e}"
        self.save()

    def _log(self, a, msg):
        a["note"] = msg
        self.s["log"].insert(0, {"at": self.now().strftime("%d %b %H:%M"), "sym": a["name"], "msg": msg})
        del self.s["log"][100:]
        self.audit("rules_" + a["status"], sym=a["name"], msg=msg)

    # -- routine (rule 4) ------------------------------------------------------------------------------
    def plan(self, watch_keys, bot_view, positions, limits, day_pnl):
        day = self.today()
        days = sorted(d for d in self.s["journal"] if d < day)
        last = self.s["journal"][days[-1]] if days else None
        near = []
        self.prefetch([k for k in watch_keys[:15] if (self.INST.get(k) or {}).get("kind") in ("EQUITY", "INDEX")])
        for k in watch_keys[:15]:
            it = self.INST.get(k)
            if not it or it["kind"] not in ("EQUITY", "INDEX"):
                continue
            lv = self.levels_for(k)
            for x in (lv.get("levels") or [])[:3]:
                if x["near"]:
                    near.append({"key": k, "sym": it["sym"], "level": x["name"], "price": x["price"], "dist_pct": x["dist_pct"]})
                    break
        lim = float(limits.get("daily_loss_limit") or 0)
        return {"day": day, "rules": self.cfg(), "fix_today": (last or {}).get("fix"), "last_day": days[-1] if days else None,
                "trades_left": max(0, int(limits.get("max_trades_day") or 5) - len(self.opened_today())),
                "max_trades": int(limits.get("max_trades_day") or 5),
                "loss_left": round(lim + min(0.0, day_pnl), 2) if lim else None, "day_pnl": round(day_pnl, 2),
                "bot_setups": (bot_view or {}).get("setups", []), "bot_positions": (bot_view or {}).get("positions", []),
                "open_positions": len([p for p in positions if int(p.get("netQty") or 0)]),
                "near_levels": near, "waiting": [a for a in self.s["armed"] if a["status"] == "waiting"],
                "journal_done": day in self.s["journal"], "streak": self.streak()}

    def journal_view(self, day, trades):
        sent = self.s["sent"].get(day, [])
        ids = {i: x for x in sent for i in x["order_ids"]}
        rows = []
        for t in trades:
            src = ids.get(str(t.get("entry_order") or ""))
            rows.append(dict(t, in_app=bool(src), level=(src or {}).get("level"), confirmed=(src or {}).get("confirmed"),
                             stop=(src or {}).get("stop")))
        outside = [r for r in rows if not r["in_app"]]
        return {"day": day, "trades": rows, "entry": self.s["journal"].get(day),
                "auto_notes": ([f"{len(outside)} trade(s) placed outside this app: no rules check on them."] if outside else []) +
                              [x["msg"] for x in self.s["log"] if x["at"].startswith(self.now().strftime("%d %b"))][:6],
                "streak": self.streak()}

    def save_journal(self, day, b):
        e = {"followed": str(b.get("followed") or "")[:10], "good": str(b.get("good") or "")[:600],
             "fix": str(b.get("fix") or "")[:600], "mood": int(b.get("mood") or 0) if str(b.get("mood") or "0").isdigit() else 0,
             "saved": self.now().strftime("%d %b %H:%M")}
        if e["followed"] not in ("yes", "partly", "no"):
            return "Say whether you followed your rules today."
        with self.lock:
            self.s["journal"][day] = e
            self.save()
        return ""

    def streak(self):
        """Days in a row with a journal entry: today (if written) and the weekdays before it."""
        d = self.now().date()
        n = 0
        if d.strftime("%Y-%m-%d") in self.s["journal"]:
            n = 1
        d -= timedelta(days=1)
        while True:
            if d.weekday() >= 5:
                d -= timedelta(days=1)
                continue
            if d.strftime("%Y-%m-%d") in self.s["journal"]:
                n += 1
                d -= timedelta(days=1)
            else:
                return n
