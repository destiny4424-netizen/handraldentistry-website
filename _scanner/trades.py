"""Today's trades: every Top Pick is locked in when it first appears, with its entry,
stop and targets frozen, and then followed candle by candle until it ends."""
import json
from datetime import datetime, timedelta, timezone

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
                 "exit_at": None, "exit": None, "result_r": None}
        stop = sl0
        last = None
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
                state.update(status="T2 hit", exit_at=c["t"], exit=t2,
                             result_r=round((r_of(t1) + r_of(t2)) / 2, 2))
                break
            if _hm(c["t"]) >= SQUARE_OFF:
                px = c["c"]
                res = r_of(px) if not state["t1_at"] else round((r_of(t1) + r_of(px)) / 2, 2)
                state.update(status="Closed at 3:15", exit_at=c["t"], exit=px, result_r=res)
                break
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
