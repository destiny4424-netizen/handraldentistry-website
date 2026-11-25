"""Screener → "Check a stock": entry, stop-loss and targets for any stock, right now, by the Meridian scanner's own
rules (_scanner/intraday.py and _scanner/swing.py, imported as they are, like the auto-trader does).

- Intraday: the 15-minute key-level breakout (yesterday's / last week's high and low, daily swing levels).
  If the stock has broken a level today, the scanner's plan and which of its 5 checks pass. If not, the nearest
  level above and below with the plan if it breaks (entry at the level; the real entry is the breakout candle's
  high / low, so these are close, not exact).
- Swing: the scanner's daily-chart setup (trend, imbalance zone, price-action trigger), if there is one today.

Data: the desk's own Dhan connection (5-minute candles for the last week, ~200 daily candles; NIFTY's daily
once a day), so the scanner's Dhan quota isn't touched.
"""
import os
import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_scanner"))
import breakout                                          # noqa: E402  (the scanner's rules)
import intraday                                          # noqa: E402
import market                                            # noqa: E402
import picks                                             # noqa: E402
import swing                                             # noqa: E402
from analysis import _day, atr                           # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))
CHECKS = intraday.STAGES[1:6]                            # Broke a key level … Trend agrees


def rows(d):
    ks = ("timestamp", "open", "high", "low", "close", "volume")
    if not isinstance(d, dict) or not all(isinstance(d.get(k), list) for k in ks):
        return []
    return [{"t": int(t), "o": float(o), "h": float(h), "l": float(l), "c": float(c), "v": float(v)}
            for t, o, h, l, c, v in zip(*(d[k] for k in ks))]


def _r2(x):
    return round(float(x), 2)


def targets(keys, entry, risk, sign):
    """The scanner's targets: the next levels more than 1R away (T1 within 3R), else 1.5R and 3R."""
    ahead = sorted({round(l["price"], 2) for l in keys if (l["price"] - entry) * sign > 1.0 * risk},
                   key=lambda v: (v - entry) * sign)
    t1 = ahead[0] if ahead and (ahead[0] - entry) * sign <= 3 * risk else entry + sign * 1.5 * risk
    t2 = next((v for v in ahead if (v - t1) * sign > 0.5 * risk), entry + sign * 3 * risk)
    if (t2 - t1) * sign <= 0:
        t2 = t1 + sign * 1.5 * risk
    return t1, t2


def wait_plan(keys, price, a15, side):
    """No breakout yet: the plan if the nearest level on this side breaks."""
    sign = 1 if side == "bull" else -1
    near = [l for l in keys if (l["price"] - price) * sign > 0]
    if not near:
        return None
    lv = min(near, key=lambda l: abs(l["price"] - price))
    L = lv["price"]
    risk = 0.6 * a15                                    # the scanner's minimum stop distance (0.6 × 15-min ATR)
    entry, sl = L, L - sign * risk
    t1, t2 = targets(keys, entry, risk, sign)
    word = "Buy only after a 15-minute close above" if side == "bull" else "Sell only after a 15-minute close below"
    return {"side": "BUY" if side == "bull" else "SELL", "level": lv["name"], "entry": _r2(entry), "sl": _r2(sl),
            "t1": _r2(t1), "t2": _r2(t2), "away_pct": round(abs(L - price) / price * 100, 2),
            "action": f"{word} {L:.2f} ({lv['name']}) on 1.5× volume"}


class Lookup:
    def __init__(self, D, now_fn=time.time):
        self.D, self.now_fn = D, now_fn
        self.cache = {}                                  # key -> (time, result)
        self.nifty = (None, [])                          # (day, NIFTY daily)

    def _nifty(self, nifty_id, frm, to):
        day = datetime.now(IST).strftime("%Y-%m-%d")
        if self.nifty[0] != day and nifty_id:
            try:
                self.nifty = (day, rows(self.D.daily(nifty_id, "IDX_I", "INDEX", frm, to)))
            except Exception:
                self.nifty = (day, [])
        return self.nifty[1]

    def check(self, sym, key, ltp=None, nifty_id=None, demo=False):
        hit = self.cache.get(key)
        if hit and time.time() - hit[0] < 30:
            return hit[1]
        seg, sid = key.split(":")
        t = datetime.now(IST)
        c5 = rows(self.D.intraday(sid, seg, "EQUITY", "5", (t - timedelta(days=7)).strftime("%Y-%m-%d 09:15:00"),
                                  t.strftime("%Y-%m-%d %H:%M:%S")))
        frm, to = (t - timedelta(days=300)).strftime("%Y-%m-%d"), (t + timedelta(days=1)).strftime("%Y-%m-%d")
        daily = rows(self.D.daily(sid, seg, "EQUITY", frm, to))
        if not c5 or len(daily) < 30:
            out = {"ok": False, "sym": sym, "error": f"Not enough candles from Dhan for {sym} yet."}
            self.cache[key] = (time.time(), out)
            return out
        now_ts = (c5[-1]["t"] + 300) if demo else self.now_fn()
        price = float(ltp or c5[-1]["c"])
        session = _day(c5[-1]["t"])
        live = session == _day(int(time.time())) and not demo
        pre_open = not demo and session < _day(int(now_ts))   # before 9:15: plan today from the last session
        if pre_open:
            last = [c for c in c5 if _day(c["t"]) == session]
            if not any(_day(x["t"]) == session for x in daily):
                daily = daily + [{"t": last[0]["t"], "o": last[0]["o"], "h": max(c["h"] for c in last),
                                  "l": min(c["l"] for c in last), "c": last[-1]["c"], "v": sum(c["v"] for c in last)}]
        out = {"ok": True, "sym": sym, "key": key, "price": _r2(price),
               "at": datetime.fromtimestamp(c5[-1]["t"] + 300, IST).strftime("%d %b %H:%M"), "live": live,
               "pre_open": pre_open,
               "intraday": self._intraday(c5, daily, _day(int(now_ts)) if pre_open else session, price, now_ts, pre_open),
               "swing": self._swing(sym, c5, daily, session, self._nifty(nifty_id, frm, to))}
        self.cache[key] = (time.time(), out)
        return out

    def _intraday(self, c5, daily, session, price, now_ts, pre_open=False):
        c15 = breakout.resample(c5, 15)
        levels = breakout.htf_levels(daily, session)
        prev = [d for d in daily if _day(d["t"]) < session]
        pdh, pdl = (prev[-1]["h"], prev[-1]["l"]) if prev else (None, None)
        vwap = market.vwap(c5)
        late = datetime.fromtimestamp(now_ts, IST).strftime("%H:%M") > intraday.LAST_ENTRY
        b, stage, side = (None, 0, None) if pre_open else intraday.analyse(c15, pdh, pdl, price, vwap, now_ts, levels)
        keys = [dict(l) for l in levels]
        for name, v in (("PDH", pdh), ("PDL", pdl)):
            if v and not any(abs(l["price"] - v) / v < 0.002 for l in keys):
                keys.append({"name": name, "price": v, "weight": 4, "hits": 1})
        res = {"late": late and not pre_open, "vwap": None if pre_open or not vwap else _r2(vwap),
               "levels": sorted(({"name": l["name"], "price": _r2(l["price"])} for l in keys
                                 if abs(l["price"] - price) / price < 0.06), key=lambda l: -l["price"])[:8]}
        if b:
            p = b["plan"]
            fails = [CHECKS[i] for i in range(len(CHECKS)) if i >= stage]
            res.update(found=True, valid=stage >= 5, side="BUY" if side == "bull" else "SELL",
                       entry=p["entry"], sl=p["sl"], t1=p["t1"], t2=p["t2"], basis=p["basis"], action=p["action"],
                       status=p["status"], rr=p["rr"], risk_pct=p["risk_pct"], extended=b.get("extended"),
                       checks=[{"name": n, "ok": i < stage} for i, n in enumerate(CHECKS)],
                       verdict="All 5 checks pass: this is a scanner pick." if stage >= 5 else
                       f"Broke {b['level_name']} but fails: {', '.join(fails)}. Not a scanner pick.")
            return res
        a15 = (atr(c15)[-1] if len(c15) > 14 else None) or price * 0.004
        res.update(found=False, verdict="Market not open yet. Today's levels to watch:" if pre_open else
                   "No key level broken today. Wait for one of these:",
                   plans=[x for x in (wait_plan(keys, price, a15, "bull"), wait_plan(keys, price, a15, "bear")) if x])
        return res

    def _swing(self, sym, c5, daily, session, nifty):
        d = [x for x in daily if _day(x["t"]) < session]
        today = [c for c in c5 if _day(c["t"]) == session]   # before 9:15 this is the last session's candle
        if today:                                        # today's daily candle so far, from the 5-minute ones
            d.append({"t": today[0]["t"], "o": today[0]["o"], "h": max(c["h"] for c in today),
                      "l": min(c["l"] for c in today), "c": today[-1]["c"], "v": sum(c["v"] for c in today)})
        nd = [x for x in nifty if _day(x["t"]) <= session]
        try:
            c = swing.analyse(sym, d[-130:], nd or None, None, None, picks.sector_of(sym))
        except Exception as e:                           # the scanner's rule should never break the lookup
            return {"found": False, "verdict": f"Swing check failed: {e}"}
        if not c:
            return {"found": False, "verdict": "No swing setup on the daily chart today."}
        p = c["plan"]
        return {"found": True, "side": "BUY" if c["side"] == "bull" else "SELL", "entry": p["entry"], "sl": p["sl"],
                "t1": p["t1"], "t2": p["t2"], "basis": p.get("basis"), "action": p.get("action"),
                "score": c.get("score"), "why": (c.get("why") or [])[:4], "rr": p.get("rr")}
