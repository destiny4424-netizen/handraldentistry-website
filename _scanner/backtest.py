"""Backtest: replay the live intraday and BTST setups over past sessions.

For every past trading day it rebuilds what the scanner would have seen at each 15-minute scan
(9:30 ... 14:00, only candles that had closed by then), runs the same intraday.build() and records
the picks exactly like the live Today's trades, then follows them through that day's 5-minute candles
(entry trigger, T1/T2, stop, momentum review, 3:15 close). BTST is picked at 15:00 with btst.candidates()
and followed into the next session (target, stop or 11:00).

Not replayed (no history for them): futures OI, news/events, option prices. The liquid-stock list is
today's. Results are in R (multiples of the risk), before brokerage and slippage.
"""
import random
from array import array
from datetime import datetime, timedelta, timezone

import btst
import intraday
import market
import trades
from analysis import _day
from breakout import htf_levels, resample
from picks import sector_of, sector_table

IST = timezone(timedelta(hours=5, minutes=30))
SCANS = [(9 * 60 + 30) + 15 * i for i in range(19)]    # minutes after midnight: 9:30 ... 14:00
BTST_AT = 15 * 60


class _DayBook(trades.TradeBook):
    """Today's-trades book kept in memory (no files)."""

    def _roll(self, day):
        if day != self.day:
            self.day, self.rows = day, []

    def save(self):
        pass


class _BtstBook(trades.BtstBook):
    def __init__(self):
        self.rows = []

    def save(self):
        pass


def _ts(day_num, minutes):
    return day_num * 86400 - 19800 + minutes * 60


def _max_drawdown(results):
    peak = cum = dd = 0.0
    for r in results:
        cum += r
        peak = max(peak, cum)
        dd = min(dd, cum - peak)
    return round(dd, 2)


def _report(rows):
    rows = sorted(rows, key=lambda r: r["picked_at"])
    by_day = {}
    for r in rows:
        by_day.setdefault(r["day"], []).append(r)
    keep = ("day", "symbol", "side", "status", "entry", "sl", "t1", "t2", "result_r", "revised", "picked_at",
            "triggered_at", "exit", "gap_pct", "basis")
    sm = trades._stats(rows)
    sm["max_drawdown"] = _max_drawdown([r["result_r"] for r in rows if r.get("result_r") is not None])
    sm["days"] = len(by_day)
    return {"summary": sm, "days": [dict(trades._stats(v), day=d) for d, v in sorted(by_day.items())],
            "trades": [{k: r.get(k) for k in keep} for r in reversed(rows)]}


def pack(candles):
    """Candles -> a flat array of doubles (t, o, h, l, c, v): ~48 bytes a candle instead of ~500."""
    a = array("d")
    for c in candles:
        a.extend((c["t"], c["o"], c["h"], c["l"], c["c"], c["v"]))
    return a


def _unpack(a, i, j):
    return [{"t": int(a[k]), "o": a[k + 1], "h": a[k + 2], "l": a[k + 3], "c": a[k + 4], "v": a[k + 5]}
            for k in range(i * 6, j * 6, 6)]


def run(data, nifty5, n_picks=6, sessions=60, progress=None):
    """data: {symbol: {"c5": 5-minute candles (list or pack()ed), "daily": daily candles}},
    nifty5: NIFTY 5-minute candles."""
    say = progress or (lambda msg: None)
    by_sym = {}
    for sym in list(data):
        d = data[sym]
        a = d["c5"] if isinstance(d["c5"], array) else pack(d["c5"])
        idx, start, cur = {}, 0, None
        for n in range(len(a) // 6):
            dn = _day(int(a[n * 6]))
            if dn != cur:
                if cur is not None:
                    idx[cur] = (start, n)
                cur, start = dn, n
        if cur is not None:
            idx[cur] = (start, len(a) // 6)
        by_sym[sym] = {"a": a, "idx": idx, "daily": d["daily"]}
    days = sorted({_day(c["t"]) for c in nifty5}) or sorted({dn for s in by_sym.values() for dn in s["idx"]})
    test_days = days[4:][-sessions:]                   # the first sessions only warm up volume averages
    day5 = lambda s, dn: _unpack(s["a"], *s["idx"][dn]) if dn in s["idx"] else []
    nifty_per = {}
    for c in nifty5:
        nifty_per.setdefault(_day(c["t"]), []).append(c)

    intra_rows, bbook = [], _BtstBook()
    for k, day in enumerate(test_days):
        say(f"Backtesting {datetime.fromtimestamp(_ts(day, 600), IST):%d %b} ({k + 1}/{len(test_days)})…")
        win = [d for d in days if d <= day][-5:]       # the live scanner looks at ~5 sessions
        ctx = {}
        for sym, s in by_sym.items():
            if day not in s["idx"]:
                continue
            before = [d for d in s["daily"] if _day(d["t"]) < day]
            if len(before) < 25:
                continue
            today5 = day5(s, day)
            prior5 = [c for d in win[:-1] for c in day5(s, d)]
            c15w = resample(prior5 + today5, 15)
            ctx[sym] = {"today5": today5, "prior5": prior5, "c5w": prior5 + today5,
                        "prior15": [c for c in c15w if _day(c["t"]) < day],
                        "today15": [c for c in c15w if _day(c["t"]) == day], "daily": before,
                        "pdh": before[-1]["h"], "pdl": before[-1]["l"], "pdc": before[-1]["c"],
                        "levels": htf_levels(s["daily"], day)}
        # Yesterday's BTST trades: follow them through this session.
        bbook.update({sym: x["c5w"] for sym, x in ctx.items()}, _ts(day, 15 * 60 + 30))
        nday = nifty_per.get(day, [])
        nwin = [c for d in win for c in nifty_per.get(d, [])]
        book = _DayBook(None)

        def snapshot(now):
            snap = {}
            for sym, x in ctx.items():
                now5 = [c for c in x["today5"] if c["t"] + 300 <= now]
                if not now5:
                    continue
                upto = x["prior5"] + now5
                price = upto[-1]["c"]
                snap[sym] = {"res": {"price": price, "pct": (price - x["pdc"]) / x["pdc"] * 100,
                                     "vwap": market.vwap(upto), "rvol": market.rvol(upto, x["daily"])},
                             "upto": upto}
            return snap

        def nifty_trend(now):
            v = market.index_view("NIFTY 50", [c for c in nwin if c["t"] + 300 <= now]) if nday else None
            return v["trend"] if v else None

        for m in SCANS:
            now = _ts(day, m)
            snap = snapshot(now)
            funnel = {sym: {"res": v["res"], "levels": ctx[sym]["levels"], "pdh": ctx[sym]["pdh"],
                            "pdl": ctx[sym]["pdl"], "now": now,
                            "c15": ctx[sym]["prior15"] + [c for c in ctx[sym]["today15"] if c["end"] <= now]}
                      for sym, v in snap.items()}
            if not funnel:
                continue
            _, finish = intraday.build(funnel, n_picks, True, nifty_trend(now))
            for c in finish({})["picks"]:
                book.record(c, now)
        book.update({sym: x["c5w"] for sym, x in ctx.items()}, _ts(day, 15 * 60 + 30))
        for r in book.rows:
            r["day"] = datetime.fromtimestamp(_ts(day, 600), IST).strftime("%Y-%m-%d")
            r["basis"] = (r.get("card") or {}).get("plan", {}).get("basis")
            r.pop("card", None)
        intra_rows += book.rows

        # BTST at 15:00, followed into the next session.
        now = _ts(day, BTST_AT)
        snap = snapshot(now)
        if snap:
            results = {s: v["res"] for s, v in snap.items()}
            sectors = {r["name"]: r["pct"] for r in sector_table({s: v["pct"] for s, v in results.items()})}
            cands = btst.candidates(results, {s: v["upto"] for s, v in snap.items()},
                                    {s: ctx[s]["daily"] for s in snap}, {s: ctx[s]["levels"] for s in snap},
                                    {s: sectors.get(sector_of(s), 0) for s in snap}, nifty_trend(now) or "Sideways", now)
            for c in btst.finish(cands[:15], {}, 5):
                if bbook.record(c, now):
                    # Bought near the close (3:15-3:25), not at 3:00: use the 3:20 candle's close.
                    late = [x for x in ctx[c["symbol"]]["today5"] if x["t"] <= _ts(day, 15 * 60 + 20)]
                    row = bbook.rows[-1]
                    px = late[-1]["c"] if late else row["entry"]
                    if (px - row["sl"]) * (1 if c["side"] == "bull" else -1) > 0:
                        row["entry"] = round(px, 2)
    btst_rows = [dict(r, kind="btst") for r in bbook.rows if r.get("result_r") is not None]
    first = datetime.fromtimestamp(_ts(test_days[0], 600), IST).strftime("%Y-%m-%d") if test_days else None
    last = datetime.fromtimestamp(_ts(test_days[-1], 600), IST).strftime("%Y-%m-%d") if test_days else None
    return {"from": first, "to": last, "sessions": len(test_days), "stocks": len(by_sym),
            "intraday": _report(intra_rows), "btst": _report(btst_rows),
            "notes": "Same rules as the live scanner, replayed scan by scan on past candles. Not replayed: futures OI, "
                     "news/events and option prices. Results in R, before brokerage and slippage."}


def demo_data(symbols, sessions=45, seed=7):
    """Made-up 5-minute history with occasional breakouts, for trying the backtest without keys."""
    today = datetime.now(IST).date()
    days, d = [], today
    while len(days) < sessions:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            days.append(d)
    days.reverse()
    out = {}
    for sym in list(symbols) + ["NIFTY"]:
        rng = random.Random(f"{sym}{seed}")
        price = rng.uniform(200, 4000)
        c5, daily = [], []
        for d in days:
            t0 = int(datetime(d.year, d.month, d.day, 9, 15, tzinfo=IST).timestamp())
            drift = rng.gauss(0, 0.0004) if rng.random() < 0.35 else 0
            vmul = 1 + 2000 * abs(drift)                     # big moves come with big volume
            day_c = []
            for i in range(75):
                o = price
                c = o * (1 + drift + rng.gauss(0, 0.0015))
                h = max(o, c) * (1 + abs(rng.gauss(0, 0.0008)))
                l = min(o, c) * (1 - abs(rng.gauss(0, 0.0008)))
                v = int(rng.uniform(4e4, 1.2e5) * (3 if i < 3 else 1) * vmul * (1 + 40 * abs(c - o) / o))
                day_c.append({"t": t0 + i * 300, "o": round(o, 2), "h": round(h, 2), "l": round(l, 2), "c": round(c, 2), "v": v})
                price = c
            c5 += day_c
            daily.append({"t": t0 - 33300, "o": day_c[0]["o"], "h": max(x["h"] for x in day_c),
                          "l": min(x["l"] for x in day_c), "c": day_c[-1]["c"], "v": sum(x["v"] for x in day_c)})
        # 30 extra quiet days before, so 20/60-day levels exist
        p0 = daily[0]["o"]
        early = []
        for i in range(30, 0, -1):
            t = daily[0]["t"] - i * 86400
            o = p0 * (1 + rng.gauss(0, 0.01))
            early.append({"t": t, "o": o, "h": o * 1.008, "l": o * 0.992, "c": o * (1 + rng.gauss(0, 0.004)), "v": daily[0]["v"]})
        out[sym] = {"c5": c5, "daily": early + daily}
    nifty = out.pop("NIFTY")["c5"]
    return out, nifty
