"""The Market tab: what's moving among the liquid F&O stocks (about 200, the ones with futures).

  indices       NIFTY, BANK NIFTY, SENSEX
  breadth       how many F&O stocks are up, down or flat today
  gainers       top 15 by % change today            losers   bottom 15
  volume        volume shockers: today's volume against the 20-day average, adjusted for how much of the
                session has gone (2× or more)
  52-week       new 52-week highs and lows today, and stocks within 2% of them
  breakouts     15-minute opening-range breakouts: the first 15-minute candle (9:15-9:30) sets the range; a
                later 15-minute candle closing above its high (or below its low) is a breakout. Also closes
                beyond yesterday's high/low. With the time, how far beyond, and the candle's volume
  OI buildup    current-month futures: price and open interest against yesterday
                (long build-up, short build-up, short covering, long unwinding)
  sectors       average % change of each sector's F&O stocks

Prices come in one request per kind (Dhan quotes, up to 1,000 instruments). The 15-minute candles are read
every 15 minutes, 20 seconds after each candle closes (about a minute for all stocks); yesterday's futures OI
once a day.
"""
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone

from picks import sector_of                               # the scanner's sector list

IST = timezone(timedelta(hours=5, minutes=30))
INDICES = ["NIFTY", "BANKNIFTY", "SENSEX"]        # the cards above the watchlist (owner: no FIN NIFTY, MIDCAP, VIX)
# "Good volume", the scanner's own liquidity rule: on average at least ₹200 crore and 5 lakh shares traded a day
# (last 20 sessions), and today at least 0.8x the usual volume for this time of day.
LIQ_DEFAULTS = {"min_turnover_cr": 200, "min_avg_lakh": 5, "min_rvol": 0.8}


def liquidity(daily, vol_today, frac, cfg):
    """turnover_cr, avg_lakh, rvol and good (None when there is no daily history to judge by)."""
    d = (daily or [])[-20:]
    if not d:
        return {"turnover_cr": None, "avg_lakh": None, "rvol": None, "liquid": None, "good": None}
    avg_v = sum(c["v"] for c in d) / len(d)
    turnover = sum(c["v"] * c["c"] for c in d) / len(d) / 1e7
    rvol = (vol_today / (avg_v * frac)) if avg_v and vol_today else None
    liquid = turnover >= cfg["min_turnover_cr"] and avg_v >= cfg["min_avg_lakh"] * 1e5
    good = liquid and (rvol is None or rvol >= cfg["min_rvol"])          # no volume yet (pre-open): judged on history
    return {"turnover_cr": round(turnover), "avg_lakh": round(avg_v / 1e5, 1), "rvol": round(rvol, 2) if rvol else None,
            "liquid": liquid, "good": good}
TOP = 15


def _rows(d):
    ks = ("timestamp", "open", "high", "low", "close", "volume")
    if not isinstance(d, dict) or not all(isinstance(d.get(k), list) for k in ks):
        return []
    oi = d.get("open_interest") if isinstance(d.get("open_interest"), list) else None
    out = [{"t": int(t), "o": float(o), "h": float(h), "l": float(l), "c": float(c), "v": float(v)}
           for t, o, h, l, c, v in zip(*(d[k] for k in ks))]
    if oi and len(oi) == len(out):
        for r, x in zip(out, oi):
            r["oi"] = float(x or 0)
    return out


def _date(t):
    return datetime.fromtimestamp(int(t), IST).strftime("%Y-%m-%d")


def buildup(pct, oi_pct):
    if oi_pct is None or abs(oi_pct) < 0.5 or abs(pct) < 0.2:
        return None
    if pct > 0:
        return "Long build-up" if oi_pct > 0 else "Short covering"
    return "Short build-up" if oi_pct > 0 else "Long unwinding"


def opening_range_breaks(candles, pdh=None, pdl=None):
    """Breakouts in one day's 15-minute candles (oldest first, all from the same day).
    The first candle is the opening range. Returns the first break up and the first break down."""
    if len(candles) < 2:
        return []
    orc = candles[0]
    avg_v = sum(c["v"] for c in candles) / len(candles) or 1
    out, done = [], set()
    for c in candles[1:]:
        t = datetime.fromtimestamp(c["t"], IST).strftime("%H:%M")
        tests = [("up", "15-min range high", orc["h"], c["c"] > orc["h"]),
                 ("down", "15-min range low", orc["l"], c["c"] < orc["l"])]
        if pdh:
            tests.append(("up", "Yesterday's high", pdh, c["c"] > pdh and c["o"] <= pdh))
        if pdl:
            tests.append(("down", "Yesterday's low", pdl, c["c"] < pdl and c["o"] >= pdl))
        for side, name, level, hit in tests:
            if hit and (side, name) not in done:
                done.add((side, name))
                out.append({"side": side, "level_name": name, "level": round(level, 2), "at": t,
                            "close": round(c["c"], 2), "beyond_pct": round(100 * (c["c"] - level) / level, 2),
                            "vol_x": round(c["v"] / avg_v, 1)})
    return out


class Movers:
    def __init__(self, dhan, instruments, now_fn, daily_fn, liq_fn=lambda: {}, quotes=None):
        """daily_fn(): {symbol: daily candles (oldest first, completed days)} - the auto-trader's cache.
        liq_fn(): saved changes to LIQ_DEFAULTS."""
        self.D, self.INST, self.now, self.daily_fn, self.liq_fn = dhan, instruments, now_fn, daily_fn, liq_fn
        self.quotes = quotes if quotes is not None else {}       # the app's live quotes (a fallback for index cards)
        self.snap, self.snap_at = None, 0
        self.breaks, self.breaks_at, self.breaks_day = [], None, ""
        self.prev_oi, self.prev_oi_day = {}, ""
        self.lock = threading.Lock()
        self.job = ""

    def universe(self):
        out = {}
        today = self.now().strftime("%Y-%m-%d")
        for ul, futs in self.INST.futs.items():
            k = self.INST.under.get(ul)
            if not (k and k.startswith("NSE_EQ:")):
                continue
            fut = next((fk for exp, fk in futs if exp >= today), None)
            out[ul] = (k, fut)
        return out

    def liq_cfg(self):
        return dict(LIQ_DEFAULTS, **(self.liq_fn() or {}))

    def fno_stocks(self):
        return {ul for ul in self.INST.futs if (self.INST.under.get(ul) or "").startswith("NSE_EQ:")}

    def liquidity_for(self, keys, quotes):
        """For watchlist rows: is it F&O, and is its volume good? Indices and F&O contracts always count."""
        daily = self.daily_fn() or {}
        fno = self.fno_stocks()
        cfg, frac = self.liq_cfg(), self.session_fraction()
        out = {}
        for k in keys:
            it = self.INST.get(k)
            if not it:
                continue
            if it["kind"] == "INDEX":
                out[k] = {"exempt": True, "good": True, "fno": False}
            elif it["kind"] != "EQUITY":
                out[k] = {"exempt": True, "good": True, "fno": True}
            else:
                is_fno = it["sym"] in fno
                vol = float((quotes.get(k) or {}).get("vol") or 0)
                lq = liquidity(daily.get(it["sym"]), vol, frac, cfg)
                lq["fno"] = is_fno
                lq["good"] = is_fno and lq["good"] is not False      # not F&O: never; no history yet: kept
                out[k] = lq
        return out

    def session_fraction(self):
        t = self.now()
        if t.weekday() >= 5:
            return 1.0
        m = (t.hour * 60 + t.minute) - (9 * 60 + 15)
        return 1.0 if m >= 375 or m < 0 else max(0.05, m / 375)

    # -- snapshot (gainers, losers, volume, 52-week, OI, sectors) -------------------------------------
    def snapshot(self, max_age=15):
        with self.lock:
            if self.snap and time.time() - self.snap_at < max_age:
                return self.snap
            try:
                self.snap = self._build()
                self.snap_at = time.time()
            except Exception as e:
                traceback.print_exc()
                if not self.snap:
                    return {"error": f"Couldn't read the market: {e}"}
            return self.snap

    def _build(self):
        uni = self.universe()
        eq = self.D.quote({"NSE_EQ": [k.split(":")[1] for k, _ in uni.values()]}, "quote")
        futs = [f.split(":")[1] for _, f in uni.values() if f]
        fq = self.D.quote({"NSE_FNO": futs}, "quote") if futs else {}
        idx_keys = [(n, self.INST.under.get(n)) for n in INDICES if self.INST.under.get(n)]
        iq = self.D.quote({"IDX_I": [k.split(":")[1] for _, k in idx_keys]}, "ohlc") if idx_keys else {}
        daily = self.daily_fn() or {}
        frac = self.session_fraction()
        cfg = self.liq_cfg()
        rows = []
        for sym, (k, fk) in uni.items():
            q = eq.get(tuple(k.split(":")))
            if not q or not q.get("last_price"):
                continue
            ltp = float(q["last_price"])
            o = q.get("ohlc") or {}
            prev = float(o.get("close") or 0)
            chg = float(q["net_change"]) if q.get("net_change") not in (None, "") else (ltp - prev if prev else 0)
            base = prev or (ltp - chg)
            pct = 100 * chg / base if base else 0
            vol = float(q.get("volume") or 0)
            d = daily.get(sym) or []
            lq = liquidity(d, vol, frac, cfg)
            rvol = lq["rvol"]
            yr = d[-250:]
            hi52 = max((c["h"] for c in yr), default=None)
            lo52 = min((c["l"] for c in yr), default=None)
            day_hi = max(float(o.get("high") or ltp), ltp)
            day_lo = min(float(o.get("low") or ltp), ltp)
            oi = oi_pct = None
            if fk:
                f = fq.get(tuple(fk.split(":"))) or {}
                oi = float(f.get("oi") or 0) or None
                p = self.prev_oi.get(fk)
                if oi and p:
                    oi_pct = round(100 * (oi - p) / p, 2)
            rows.append({"sym": sym, "key": k, "fut": fk, "sector": sector_of(sym), "ltp": round(ltp, 2),
                         "pct": round(pct, 2), "chg": round(chg, 2), "vol": vol,
                         "rvol": round(rvol, 2) if rvol else None,
                         "hi52": round(hi52, 2) if hi52 else None, "lo52": round(lo52, 2) if lo52 else None,
                         "new_high": bool(hi52 and day_hi >= hi52), "new_low": bool(lo52 and day_lo <= lo52),
                         "from_high_pct": round(100 * (ltp - hi52) / hi52, 2) if hi52 else None,
                         "from_low_pct": round(100 * (ltp - lo52) / lo52, 2) if lo52 else None,
                         "oi": oi, "oi_pct": oi_pct, "buildup": buildup(pct, oi_pct),
                         "turnover_cr": lq["turnover_cr"], "good": lq["good"]})
        by_pct = sorted(rows, key=lambda r: -r["pct"])
        good = [r for r in rows if r["good"] is not False]           # unknown (no history yet) stays in
        good_syms = {r["sym"] for r in good}
        by_pct_good = [r for r in by_pct if r["good"] is not False]
        highs = sorted([r for r in rows if r["from_high_pct"] is not None and (r["new_high"] or r["from_high_pct"] >= -2)],
                       key=lambda r: -(r["from_high_pct"] or -99))
        lows = sorted([r for r in rows if r["from_low_pct"] is not None and (r["new_low"] or r["from_low_pct"] <= 2)],
                      key=lambda r: r["from_low_pct"] or 99)
        sectors = {}
        for r in rows:
            sectors.setdefault(r["sector"], []).append(r["pct"])
        idx = []
        for n, k in idx_keys:
            q = iq.get(tuple(k.split(":"))) or {}
            ltp = float(q.get("last_price") or 0)
            prev = float((q.get("ohlc") or {}).get("close") or 0)
            if not ltp:                                    # after the close Dhan can send 0: use what we know
                ltp = float((self.quotes.get(k) or {}).get("ltp") or 0) or prev
            if not ltp:
                continue
            idx.append({"name": n, "key": k, "ltp": round(ltp, 2), "chg": round(ltp - prev, 2) if prev else None,
                        "pct": round(100 * (ltp - prev) / prev, 2) if prev and ltp else None})
        build = {}
        for r in rows:
            if r["buildup"]:
                build.setdefault(r["buildup"], []).append(r)
        for v in build.values():
            v.sort(key=lambda r: -abs(r["oi_pct"] or 0))
        return {
            "at": self.now().strftime("%H:%M:%S"), "count": len(rows), "indices": idx,
            "breadth": {"up": sum(1 for r in rows if r["pct"] > 0.05), "down": sum(1 for r in rows if r["pct"] < -0.05),
                        "flat": sum(1 for r in rows if -0.05 <= r["pct"] <= 0.05)},
            "gainers": by_pct_good[:TOP], "losers": by_pct_good[::-1][:TOP],
            "gainers_all": by_pct[:TOP], "losers_all": by_pct[::-1][:TOP],
            "volume": sorted([r for r in rows if (r["rvol"] or 0) >= 2 and r["good"] is not False], key=lambda r: -r["rvol"])[:TOP],
            "volume_all": sorted([r for r in rows if (r["rvol"] or 0) >= 2], key=lambda r: -r["rvol"])[:TOP],
            "highs": [r for r in highs if r["sym"] in good_syms][:TOP], "highs_all": highs[:TOP],
            "lows": [r for r in lows if r["sym"] in good_syms][:TOP], "lows_all": lows[:TOP],
            "buildup": {k: v[:10] for k, v in build.items()},
            "sectors": sorted([{"name": s, "pct": round(sum(v) / len(v), 2), "n": len(v),
                                "up": sum(1 for x in v if x > 0)} for s, v in sectors.items() if s != "Others"],
                              key=lambda s: -s["pct"]),
            "breakouts": [b for b in self.breaks if b["sym"] in good_syms], "breakouts_all": self.breaks,
            "breakouts_at": self.breaks_at,
            "liq": cfg, "good_count": len(good), "low_volume": sorted(r["sym"] for r in rows if r["good"] is False),
            "daily_ready": bool(daily), "oi_ready": bool(self.prev_oi), "job": self.job,
        }

    # -- background: 15-minute breakouts and yesterday's OI --------------------------------------------
    def load_prev_oi(self):
        day = self.now().strftime("%Y-%m-%d")
        if self.prev_oi_day == day and self.prev_oi:
            return
        frm = (self.now() - timedelta(days=10)).strftime("%Y-%m-%d")
        out = {}
        uni = self.universe()
        for i, (sym, (_, fk)) in enumerate(uni.items()):
            if not fk:
                continue
            self.job = f"Yesterday's futures OI {i + 1}/{len(uni)}"
            try:
                d = self.D.call("POST", "/charts/historical", {
                    "securityId": fk.split(":")[1], "exchangeSegment": "NSE_FNO", "instrument": "FUTSTK",
                    "expiryCode": 0, "oi": True, "fromDate": frm, "toDate": day})
            except Exception:
                continue
            done = [c for c in _rows(d) if _date(c["t"]) < day and c.get("oi")]
            if done:
                out[fk] = done[-1]["oi"]
        self.prev_oi, self.prev_oi_day = out, day
        self.job = ""
        self.snap_at = 0

    def scan_breakouts(self):
        t = self.now()
        daily = self.daily_fn() or {}
        found = []
        uni = self.universe()
        for i, (sym, (k, _)) in enumerate(uni.items()):
            self.job = f"15-minute candles {i + 1}/{len(uni)}"
            try:
                rows = _rows(self.D.intraday(k.split(":")[1], "NSE_EQ", "EQUITY", "15",
                                             (t - timedelta(days=5)).strftime("%Y-%m-%d 09:15:00"),
                                             t.strftime("%Y-%m-%d %H:%M:%S")))
            except Exception:
                continue
            closed = [c for c in rows if c["t"] + 900 <= t.timestamp() - 15]
            if not closed:
                continue
            last_day = _date(closed[-1]["t"])                    # today, or the last session before it
            day = [c for c in closed if _date(c["t"]) == last_day]
            d = [c for c in daily.get(sym) or [] if _date(c["t"]) < last_day]
            pdh, pdl = (d[-1]["h"], d[-1]["l"]) if d else (None, None)
            for b in opening_range_breaks(day, pdh, pdl):
                found.append(dict(b, sym=sym, key=k, sector=sector_of(sym), day=last_day))
        found.sort(key=lambda b: (b["at"], -abs(b["beyond_pct"])), reverse=True)
        self.breaks, self.breaks_at = found, t.strftime("%H:%M")
        self.breaks_day = t.strftime("%Y-%m-%d")
        self.job = ""
        self.snap_at = 0

    def loop(self, ready):
        """Background thread: OI once a day, breakouts every 15 minutes in market hours (and once at start)."""
        last_slot = None
        time.sleep(20)
        while True:
            try:
                if ready():
                    t = self.now()
                    if not self.prev_oi or (t.hour, t.minute) >= (8, 30):
                        self.load_prev_oi()              # once a day (it keeps the day it loaded)
                    slot = (t.strftime("%Y-%m-%d"), (t.hour * 60 + t.minute - 15) // 15)
                    market = t.weekday() < 5 and (9, 30) <= (t.hour, t.minute) <= (15, 45)
                    if (last_slot is None) or (market and slot != last_slot and t.second >= 20):
                        last_slot = slot
                        self.scan_breakouts()
            except Exception:
                traceback.print_exc()
                self.job = ""
            time.sleep(20)
