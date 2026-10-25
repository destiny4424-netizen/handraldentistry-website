"""Today's trades: every Top Pick is locked in when it first appears, with its entry,
stop and targets frozen, and then followed candle by candle until it ends."""
import json
from datetime import datetime, timedelta, timezone

from breakout import resample
from market import supertrend

IST = timezone(timedelta(hours=5, minutes=30))
NO_NEW_ENTRY = (15, 0)                                 # an untriggered trade expires at 15:00
SQUARE_OFF = (15, 15)                                  # open trades are closed at 15:15


def _hm(t):
    d = datetime.fromtimestamp(t, IST)
    return (d.hour, d.minute)


class TradeBook:
    def __init__(self, folder):
        self.folder = folder
        self.day = None
        self.rows = []

    def _path(self):
        return self.folder / f"trades-{self.day}.json"

    def _roll(self, day):
        if day == self.day:
            return
        self.day, self.rows = day, []
        try:
            self.rows = json.loads(self._path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass

    def save(self):
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            tmp = self._path().with_suffix(".tmp")
            tmp.write_text(json.dumps(self.rows), encoding="utf-8")
            tmp.replace(self._path())
        except OSError:
            pass

    def record(self, pick, now_ts):
        """Lock a pick in (once per stock and side per day). Returns True if it is new."""
        self._roll(datetime.fromtimestamp(now_ts, IST).strftime("%Y-%m-%d"))
        if any(r["symbol"] == pick["symbol"] and r["side"] == pick["side"] for r in self.rows):
            return False
        p = pick["plan"]
        self.rows.append({
            "symbol": pick["symbol"], "side": pick["side"], "picked_at": now_ts,
            "basis": p["basis"], "score": pick.get("score"), "sector": pick.get("sector"),
            "entry": round(p["entry"], 2), "sl": round(p["sl"], 2),
            "t1": round(p["t1"], 2), "t2": round(p["t2"], 2),
            "held_at": p.get("held_at"), "status": "Waiting for trigger",
            "triggered_at": None, "t1_at": None, "exit_at": None, "exit": None,
            "result_r": None, "last": pick.get("price"),
            "option": pick.get("option"),
            "card": {k: v for k, v in pick.items() if k not in ("spark", "locked_new", "since")},
        })
        return True

    def update(self, candles_by_symbol, now_ts):
        """Follow every trade through the 5-minute candles since it was picked."""
        self._roll(datetime.fromtimestamp(now_ts, IST).strftime("%Y-%m-%d"))
        for r in self.rows:
            cs = candles_by_symbol.get(r["symbol"])
            if cs:
                self._follow(r, cs, now_ts)
        self.save()

    def _follow(self, r, candles, now_ts):
        bull = r["side"] == "bull"
        entry, sl0, t1, t2 = r["entry"], r["sl"], r["t1"], r["t2"]
        risk = abs(entry - sl0) or 1e-9
        r_of = lambda price: round(((price - entry) if bull else (entry - price)) / risk, 2)
        # From the close of the 15-minute candle that held the level (the entry is its break).
        start = r["held_at"] + 900 if r.get("held_at") else r["picked_at"] - 300
        state = {"status": "Waiting for trigger", "triggered_at": None, "t1_at": None,
                 "exit_at": None, "exit": None, "result_r": None, "revised": None, "t2_now": t2}
        stop = sl0
        last = None
        # Momentum check at every 15-minute close after entry: VWAP and the 15-min Supertrend.
        day = datetime.fromtimestamp(r["picked_at"], IST).date()
        day_c = [c for c in candles if datetime.fromtimestamp(c["t"], IST).date() == day]
        vwap_at, pv, vv = {}, 0.0, 0.0
        for c in day_c:
            pv += (c["h"] + c["l"] + c["c"]) / 3 * c["v"]; vv += c["v"]
            vwap_at[c["t"]] = pv / vv if vv else c["c"]
        c15 = resample(day_c, 15)
        st_dir = {b["end"]: d for b, (_, d) in zip(c15, supertrend(c15))}
        for c in candles:
            if c["t"] < start or c["t"] + 300 > now_ts + 1:        # only closed candles after the pick
                continue
            last = c
            hi, lo = c["h"], c["l"]
            hit_stop = lo <= stop if bull else hi >= stop
            if state["triggered_at"] is None:
                if _hm(c["t"]) >= NO_NEW_ENTRY:
                    state.update(status="Not triggered", exit_at=c["t"])
                    break
                if (lo <= sl0) if bull else (hi >= sl0):        # broke the level before entry
                    state.update(status="Cancelled: stop hit before entry", exit_at=c["t"])
                    break
                if (hi >= entry) if bull else (lo <= entry):
                    state.update(status="Triggered", triggered_at=c["t"])
                    hit_stop = lo <= stop if bull else hi >= stop
                    if hit_stop:                                 # same candle went both ways: assume the stop
                        state.update(status="SL hit", exit_at=c["t"], exit=stop, result_r=r_of(stop))
                        break
                continue
            if hit_stop:                                         # stop first if a candle touches both
                if state["t1_at"]:
                    state.update(status="Stopped at cost after T1", exit_at=c["t"], exit=stop,
                                 result_r=round(r_of(t1) / 2, 2))  # half booked at T1, half at cost
                else:
                    state.update(status="SL hit", exit_at=c["t"], exit=stop, result_r=r_of(stop))
                break
            if state["t1_at"] is None and ((hi >= t1) if bull else (lo <= t1)):
                state.update(status="T1 hit · stop moved to cost", t1_at=c["t"])
                stop = entry
            if state["t1_at"] and ((hi >= t2) if bull else (lo <= t2)):
                state.update(status="Target hit (revised to T1)" if state["revised"] and t2 == t1 else "T2 hit",
                             exit_at=c["t"], exit=t2, result_r=round((r_of(t1) + r_of(t2)) / 2, 2))
                break
            if _hm(c["t"]) >= SQUARE_OFF:
                px = c["c"]
                res = r_of(px) if not state["t1_at"] else round((r_of(t1) + r_of(px)) / 2, 2)
                state.update(status="Closed at 3:15", exit_at=c["t"], exit=px, result_r=res)
                break
            end = c["t"] + 300
            if end in st_dir and end > state["triggered_at"] + 300:          # a 15-min candle just closed
                px, vw = c["c"], vwap_at.get(c["t"], c["c"])
                off_vwap = px < vw if bull else px > vw
                off_st = st_dir[end] == (-1 if bull else 1)
                hm = datetime.fromtimestamp(end, IST).strftime("%H:%M")
                if off_vwap and off_st:                                     # momentum gone: get out
                    res = r_of(px) if not state["t1_at"] else round((r_of(t1) + r_of(px)) / 2, 2)
                    state.update(status="Exited: momentum lost", exit_at=c["t"], exit=px, result_r=res,
                                 revised=f"{hm}: closed {'below' if bull else 'above'} VWAP and Supertrend turned, exit at {px:.2f}")
                    break
                if (off_vwap or off_st) and not state["revised"]:          # slowing: take less, protect more
                    why = "VWAP lost" if off_vwap else "Supertrend turned"
                    if state["t1_at"]:
                        res = round((r_of(t1) + r_of(px)) / 2, 2)
                        state.update(status="Booked rest: momentum slowing", exit_at=c["t"], exit=px, result_r=res,
                                     revised=f"{hm}: {why} after T1, booked the rest at {px:.2f}")
                        break
                    t2 = t1
                    note = f"{hm}: {why}, target cut to T1 {t1:.2f}"
                    if r_of(px) >= .5:
                        stop = entry
                        note += ", stop moved to cost"
                    state.update(revised=note, t2_now=t2)
        if state["triggered_at"] is None and state["exit_at"] is None and _hm(now_ts) >= NO_NEW_ENTRY:
            state.update(status="Not triggered", exit_at=now_ts)   # no candle broke it before 3 PM
        if state["triggered_at"] and state["exit_at"] is None and last and _hm(now_ts) >= SQUARE_OFF:
            px = last["c"]                                       # square off at the last price
            state.update(status="Closed at 3:15", exit_at=now_ts, exit=px,
                         result_r=r_of(px) if not state["t1_at"] else round((r_of(t1) + r_of(px)) / 2, 2))
        r.update(state)
        if last:
            r["last"] = last["c"]
            if state["triggered_at"] and state["exit_at"] is None:      # open: show the running result
                r["running_r"] = r_of(last["c"]) if not state["t1_at"] else round((r_of(t1) + r_of(last["c"])) / 2, 2)
            else:
                r.pop("running_r", None)

    def summary(self):
        done = [r for r in self.rows if r["result_r"] is not None]
        wins = [r for r in done if r["result_r"] > 0]
        return {"day": self.day, "count": len(self.rows),
                "triggered": sum(1 for r in self.rows if r["triggered_at"]),
                "open": sum(1 for r in self.rows if r["triggered_at"] and r["exit_at"] is None),
                "closed": len(done), "wins": len(wins), "losses": sum(1 for r in done if r["result_r"] < 0),
                "total_r": round(sum(r["result_r"] for r in done), 2),
                "win_rate": round(100 * len(wins) / len(done)) if done else None}

    def view(self):
        return {"rows": sorted(self.rows, key=lambda r: -r["picked_at"]), "summary": self.summary()}


BTST_EXIT = (11, 0)                                    # if neither target nor stop by 11:00 next day, exit


class BtstBook:
    """BTST/STBT picks: bought (or sold) near today's close, followed through the next session."""

    def __init__(self, folder):
        self.path = folder / "btst.json"
        try:
            self.rows = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.rows = []

    def save(self):
        cutoff = datetime.now(IST).timestamp() - 400 * 86400      # keep about a year for Performance
        self.rows = [r for r in self.rows if r["picked_at"] > cutoff]
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.rows), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass

    def record(self, pick, now_ts):
        day = datetime.fromtimestamp(now_ts, IST).strftime("%Y-%m-%d")
        if any(r["symbol"] == pick["symbol"] and r["day"] == day for r in self.rows):
            return False
        p = pick["plan"]
        self.rows.append({"symbol": pick["symbol"], "side": pick["side"], "day": day, "picked_at": now_ts,
                          "entry": round(p["entry"], 2), "sl": round(p["sl"], 2), "t1": round(p["t1"], 2),
                          "t2": round(p["t2"], 2), "status": "Holding overnight", "gap_pct": None,
                          "t1_at": None, "exit_at": None, "exit": None, "result_r": None, "last": pick["price"]})
        return True

    def update(self, candles_by_symbol, now_ts):
        for r in self.rows:
            if r["exit_at"]:
                continue
            cs = candles_by_symbol.get(r["symbol"])
            if cs:
                self._follow(r, cs, now_ts)
        self.save()

    def _follow(self, r, candles, now_ts):
        bull = r["side"] == "bull"
        entry, stop, t1, t2 = r["entry"], r["sl"], r["t1"], r["t2"]
        risk = abs(entry - stop) or 1e-9
        r_of = lambda px: round(((px - entry) if bull else (entry - px)) / risk, 2)
        pick_day = datetime.fromtimestamp(r["picked_at"], IST).date()
        nxt = [c for c in candles if datetime.fromtimestamp(c["t"], IST).date() > pick_day
               and c["t"] + 300 <= now_ts + 1]
        if not nxt:
            r["last"] = candles[-1]["c"]
            r["running_r"] = r_of(candles[-1]["c"])
            return
        day1 = datetime.fromtimestamp(nxt[0]["t"], IST).date()
        nxt = [c for c in nxt if datetime.fromtimestamp(c["t"], IST).date() == day1]   # the next session only
        r["gap_pct"] = round((nxt[0]["o"] - entry) / entry * 100, 2)
        r.update(status="Next day: open", t1_at=None, exit_at=None, exit=None, result_r=None)
        for c in nxt:
            hit_stop = c["l"] <= stop if bull else c["h"] >= stop
            if hit_stop:
                px = min(stop, c["o"]) if bull else max(stop, c["o"])   # a gap through the stop fills at the open
                res = r_of(px) if not r["t1_at"] else round((r_of(t1) + r_of(px)) / 2, 2)
                r.update(status="Stopped at cost after T1" if r["t1_at"] else "SL hit", exit_at=c["t"], exit=px, result_r=res)
                break
            if not r["t1_at"] and (c["h"] >= t1 if bull else c["l"] <= t1):
                r.update(status="T1 hit · stop moved to cost", t1_at=c["t"])
                stop = entry
            if r["t1_at"] and (c["h"] >= t2 if bull else c["l"] <= t2):
                r.update(status="T2 hit", exit_at=c["t"], exit=t2, result_r=round((r_of(t1) + r_of(t2)) / 2, 2))
                break
            if _hm(c["t"]) >= BTST_EXIT:
                px = c["c"]
                res = r_of(px) if not r["t1_at"] else round((r_of(t1) + r_of(px)) / 2, 2)
                r.update(status="Exited 11:00", exit_at=c["t"], exit=px, result_r=res)
                break
        r["last"] = nxt[-1]["c"]
        if r["exit_at"] is None:
            r["running_r"] = r_of(nxt[-1]["c"]) if not r["t1_at"] else round((r_of(t1) + r_of(nxt[-1]["c"])) / 2, 2)
        else:
            r.pop("running_r", None)

    def view(self):
        done = [r for r in self.rows if r["result_r"] is not None]
        wins = [r for r in done if r["result_r"] > 0]
        return {"rows": sorted(self.rows, key=lambda r: -r["picked_at"])[:30],
                "summary": {"count": len(self.rows), "closed": len(done), "wins": len(wins),
                            "losses": sum(1 for r in done if r["result_r"] < 0),
                            "total_r": round(sum(r["result_r"] for r in done), 2),
                            "win_rate": round(100 * len(wins) / len(done)) if done else None}}



def _stats(rows):
    """Totals for a list of trades (rows with result_r when finished)."""
    done = [r for r in rows if r.get("result_r") is not None]
    wins = [r["result_r"] for r in done if r["result_r"] > 0]
    losses = [r["result_r"] for r in done if r["result_r"] < 0]
    streak = worst_streak = 0
    for r in sorted(done, key=lambda r: r.get("exit_at") or r["picked_at"]):
        streak = streak + 1 if r["result_r"] < 0 else 0
        worst_streak = max(worst_streak, streak)
    return {"picks": len(rows), "triggered": sum(1 for r in rows if r.get("triggered_at") or r.get("kind") == "btst"),
            "closed": len(done), "wins": len(wins), "losses": len(losses),
            "win_rate": round(100 * len(wins) / len(done)) if done else None,
            "total_r": round(sum(r["result_r"] for r in done), 2),
            "avg_r": round(sum(r["result_r"] for r in done) / len(done), 2) if done else None,
            "best": max((r["result_r"] for r in done), default=None),
            "worst": min((r["result_r"] for r in done), default=None),
            "profit_factor": round(sum(wins) / -sum(losses), 2) if losses else None,
            "losing_streak": worst_streak}


def month_report(folder, month, btst_rows):
    """Performance for one month (YYYY-MM): intraday days from the daily files, BTST from its book."""
    intraday_days, intraday_rows = [], []
    for f in sorted(folder.glob(f"trades-{month}-*.json")):
        try:
            rows = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        day = f.stem[len("trades-"):]
        for r in rows:
            r["day"] = day
        intraday_rows += rows
        intraday_days.append(dict(_stats(rows), day=day))
    btst = [dict(r, kind="btst") for r in btst_rows if r.get("day", "").startswith(month)]
    btst_days = {}
    for r in btst:
        btst_days.setdefault(r["day"], []).append(r)
    months = sorted({f.stem[7:14] for f in folder.glob("trades-*.json")} |
                    {r["day"][:7] for r in btst_rows if r.get("day")} | {month}, reverse=True)
    keep = ("day", "symbol", "side", "status", "entry", "sl", "t1", "t2", "result_r", "revised", "picked_at",
            "triggered_at", "exit", "gap_pct")
    return {
        "month": month, "months": months,
        "intraday": {"summary": _stats(intraday_rows), "days": intraday_days,
                     "trades": [{k: r.get(k) for k in keep} for r in sorted(intraday_rows, key=lambda r: -r["picked_at"])]},
        "btst": {"summary": _stats(btst),
                 "days": [dict(_stats(v), day=d) for d, v in sorted(btst_days.items())],
                 "trades": [{k: r.get(k) for k in keep} for r in sorted(btst, key=lambda r: -r["picked_at"])]},
    }
