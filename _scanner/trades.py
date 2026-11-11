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


def features(pick):
    """What the pick looked like when it was given (for the learning journal)."""
    keys = ("side", "sector", "score", "parts", "vol_x", "broke_at", "retest", "gap", "nifty", "rvol", "pct", "at20",
            "gex", "setup", "signal", "trend", "event", "discount")
    f = {k: pick.get(k) for k in keys if pick.get(k) is not None}
    if isinstance(pick.get("zone"), dict):             # swing: which imbalance zone
        f["zone_type"], f["zone_state"] = pick["zone"].get("type"), pick["zone"].get("state")
    if isinstance(pick.get("oi"), dict):
        f["oi_kind"] = pick["oi"].get("kind")
    if pick.get("also"):
        f["also"] = True
    if (pick.get("plan") or {}).get("rr") is not None:
        f["rr"] = pick["plan"]["rr"]
    return f


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
            "held_at": p.get("held_at"), "start_at": p.get("start_at"), "exit_rule": p.get("exit_rule"),
            "status": "Waiting for trigger",
            "triggered_at": None, "t1_at": None, "exit_at": None, "exit": None,
            "result_r": None, "last": pick.get("price"),
            "option": pick.get("option"),
            "f": features(pick),
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
        if r.get("start_at"):
            start = r["start_at"]                                # e.g. the end of the opening range
        start = max(start, r["picked_at"] - 60)                  # nothing before the pick was given counts
        eod = r.get("exit_rule") == "eod"                        # stop or 3:15 only: no targets, no momentum exits
        slip = 0.0
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
                # A breakout whose level breaks before the entry is cancelled. An opening range breakout is a
                # standing stop order: its stop only counts once it is in the trade.
                if not eod and ((lo <= sl0) if bull else (hi >= sl0)):
                    state.update(status="Cancelled: stop hit before entry", exit_at=c["t"])
                    break
                if (hi >= entry) if bull else (lo <= entry):
                    state.update(status="Triggered", triggered_at=c["t"])
                    # Already past the entry when the candle opened: filled at the open, not at the entry.
                    slip = max(0.0, ((c["o"] - entry) if bull else (entry - c["o"])) / risk)
                    # Stopped in the same candle? A green candle goes open-low-high-close, a red one
                    # open-high-low-close: after a buy entry near the high, a green candle only stops it if it
                    # closes at the stop; a red one does if its low reaches it (mirror for a sell).
                    green = c["c"] >= c["o"]
                    if bull:
                        hit_stop = (c["c"] <= stop) if green else (lo <= stop)
                    else:
                        hit_stop = (hi >= stop) if green else (c["c"] >= stop)
                    if hit_stop:                                 # same candle went both ways
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
            if eod:
                if _hm(c["t"]) >= SQUARE_OFF:
                    state.update(status="Closed at 3:15", exit_at=c["t"], exit=c["c"], result_r=r_of(c["c"]))
                    break
                continue
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
        if slip and state["result_r"] is not None:
            state["result_r"] = round(state["result_r"] - slip, 2)
        state["slip_r"] = round(slip, 2) if slip else None
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


class SwingBook:
    """Swing trades: picked at a daily close, entered when price breaks the trigger candle within
    TRIGGER_DAYS sessions, then followed day by day on daily candles: T1 (half booked, stop to cost),
    T2, the stop, or a time stop after HOLD_DAYS sessions."""
    TRIGGER_DAYS, HOLD_DAYS = 3, 10
    FILE, KIND = "swing.json", "swing"
    GAP_SKIP = None                                    # skip the trade if a day opens this many R past the entry

    def __init__(self, folder=None):
        self.path = folder / self.FILE if folder else None
        try:
            self.rows = json.loads(self.path.read_text(encoding="utf-8")) if self.path else []
        except (OSError, ValueError):
            self.rows = []

    def save(self):
        if not self.path:
            return
        cutoff = datetime.now(IST).timestamp() - 400 * 86400
        self.rows = [r for r in self.rows if r["picked_at"] > cutoff]
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.rows), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass

    def open_rows(self):
        return [r for r in self.rows if r.get("exit_at") is None]

    def record(self, pick, now_ts):
        day = datetime.fromtimestamp(now_ts, IST).strftime("%Y-%m-%d")
        if any(r["symbol"] == pick["symbol"] and (r["day"] == day or r.get("exit_at") is None) for r in self.rows):
            return False                               # one live swing trade per stock
        p = pick["plan"]
        self.rows.append({"symbol": pick["symbol"], "side": pick["side"], "day": day, "picked_at": now_ts, "kind": self.KIND,
                          "basis": p.get("basis"), "entry": round(p["entry"], 2), "sl": round(p["sl"], 2),
                          "t1": round(p["t1"], 2), "t2": round(p["t2"], 2), "status": "Waiting for trigger",
                          "triggered_at": None, "t1_at": None, "exit_at": None, "exit": None, "result_r": None,
                          "last": pick.get("price"), "zone_text": pick.get("zone_text"), "f": features(pick)})
        return True

    def update(self, daily_by_symbol, now_ts):
        for r in self.rows:
            if r.get("exit_at") is not None:
                continue
            d = daily_by_symbol.get(r["symbol"])
            if d:
                self._follow(r, d, now_ts)
        self.save()

    def _follow(self, r, daily, now_ts):
        bull = r["side"] == "bull"
        entry, stop, t1, t2 = r["entry"], r["sl"], r["t1"], r["t2"]
        risk = abs(entry - stop) or 1e-9
        r_of = lambda px: ((px - entry) if bull else (entry - px)) / risk
        pick_day = datetime.fromtimestamp(r["picked_at"], IST).date()
        after = [c for c in daily if datetime.fromtimestamp(c["t"], IST).date() > pick_day
                 and c["t"] <= now_ts]
        st = {"status": "Waiting for trigger", "triggered_at": None, "t1_at": None, "exit_at": None,
              "exit": None, "result_r": None}
        slip, held, last = 0.0, 0, None
        for i, c in enumerate(after):
            last = c
            day = datetime.fromtimestamp(c["t"], IST).strftime("%d %b")
            if st["triggered_at"] is None:
                if i >= self.TRIGGER_DAYS:
                    st.update(status=f"Not triggered in {self.TRIGGER_DAYS} sessions", exit_at=c["t"])
                    break
                if self.GAP_SKIP and ((c["o"] - entry) if bull else (entry - c["o"])) > self.GAP_SKIP * risk:
                    st.update(status=f"Skipped: opened past the entry {day}", exit_at=c["t"])
                    break
                if (c["h"] >= entry) if bull else (c["l"] <= entry):
                    st.update(status="Triggered", triggered_at=c["t"])
                    slip = max(0.0, ((c["o"] - entry) if bull else (entry - c["o"])) / risk)
                    green = c["c"] >= c["o"]
                    stopped = ((c["c"] <= stop) if green else (c["l"] <= stop)) if bull else \
                              ((c["h"] >= stop) if green else (c["c"] >= stop))
                    if stopped:
                        st.update(status=f"SL hit {day}", exit_at=c["t"], exit=stop, result_r=r_of(stop))
                        break
                continue
            held += 1
            if (c["l"] <= stop) if bull else (c["h"] >= stop):
                px = min(stop, c["o"]) if bull else max(stop, c["o"])
                res = r_of(px) if not st["t1_at"] else (r_of(t1) + r_of(px)) / 2
                st.update(status=("Stopped at cost after T1 " if st["t1_at"] else "SL hit ") + day, exit_at=c["t"], exit=px, result_r=res)
                break
            if not st["t1_at"] and ((c["h"] >= t1) if bull else (c["l"] <= t1)):
                st.update(status=f"T1 hit {day} · stop to cost", t1_at=c["t"])
                stop = entry
            if st["t1_at"] and ((c["h"] >= t2) if bull else (c["l"] <= t2)):
                st.update(status=f"T2 hit {day}", exit_at=c["t"], exit=t2, result_r=(r_of(t1) + r_of(t2)) / 2)
                break
            if held >= self.HOLD_DAYS:
                res = r_of(c["c"]) if not st["t1_at"] else (r_of(t1) + r_of(c["c"])) / 2
                st.update(status=f"Time stop ({self.HOLD_DAYS} sessions) {day}", exit_at=c["t"], exit=c["c"], result_r=res)
                break
        if st["result_r"] is not None:
            st["result_r"] = round(st["result_r"] - slip, 2)
        r.update(st)
        r["held"] = held
        if last:
            r["last"] = last["c"]
            if st["triggered_at"] and st["exit_at"] is None:
                r["running_r"] = round(r_of(last["c"]) if not st["t1_at"] else (r_of(t1) + r_of(last["c"])) / 2, 2)
            else:
                r.pop("running_r", None)

    def view(self):
        done = [r for r in self.rows if r["result_r"] is not None]
        wins = [r for r in done if r["result_r"] > 0]
        return {"rows": sorted(self.rows, key=lambda r: -r["picked_at"])[:40],
                "summary": {"count": len(self.rows), "closed": len(done), "wins": len(wins),
                            "open": sum(1 for r in self.rows if r.get("triggered_at") and r.get("exit_at") is None),
                            "losses": sum(1 for r in done if r["result_r"] < 0),
                            "total_r": round(sum(r["result_r"] for r in done), 2),
                            "win_rate": round(100 * len(wins) / len(done)) if done else None}}


class BnfBook(SwingBook):
    """Bank Nifty option swing trades (levels on Bank Nifty): trigger within 2 sessions, out after 6."""
    TRIGGER_DAYS, HOLD_DAYS = 2, 6
    FILE, KIND = "bnf.json", "bnf"
    GAP_SKIP = 0.5


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


# ── Live option prices for open Bank Nifty trades ──────────────────────────────────────────
def option_marks(row, ltp):
    """Record the option's real price at each stage (approximate: within the 5-minute check) and its
    result: per lot in ₹ and in R of the planned option risk (half booked at T1, like the plan)."""
    if ltp is None:
        return
    row["opt_ltp"] = round(ltp, 2)
    if not row.get("triggered_at"):
        return
    if row.get("opt_fill") is None:
        row["opt_fill"] = round(ltp, 2)
    if row.get("t1_at") and row.get("opt_t1_px") is None:
        row["opt_t1_px"] = round(ltp, 2)
    if row.get("exit_at") and row.get("result_r") is not None and row.get("opt_exit") is None:
        row["opt_exit"] = round(ltp, 2)
    fill, t1px = row["opt_fill"], row.get("opt_t1_px")
    px = row.get("opt_exit") if row.get("opt_exit") is not None else ltp
    pts = ((t1px - fill) + (px - fill)) / 2 if t1px is not None else px - fill
    row["opt_pnl_lot"] = round(pts * (row.get("opt_lot") or 1))
    risk = (row.get("opt_entry") or 0) - (row.get("opt_sl") or 0)
    row["opt_r"] = round(pts / risk, 2) if risk > 0 else None


# ── Trades you took yourself ───────────────────────────────────────────────────────────────
def mine_result(row, kind):
    """Your real result for a trade you marked as taken: R against the planned risk, and ₹.
    Bank Nifty: prices under 10,000 are option prices (risk = the option's planned entry - SL)."""
    m = row.get("mine")
    if not m:
        return None
    entry, ex = m["entry"], m.get("exit")
    opt = kind == "bnf" and entry < 10000
    sign = 1 if (opt or row["side"] == "bull") else -1
    if ex is None:                                     # not closed by you yet: the trade's own exit / price now
        if opt:
            ex = row.get("opt_exit") if row.get("opt_exit") is not None else row.get("opt_ltp")
        else:
            ex = row.get("exit") if row.get("exit") is not None else row.get("last")
        open_ = m.get("exit") is None and row.get("exit_at") is None
    else:
        open_ = False
    risk = ((row.get("opt_entry") or 0) - (row.get("opt_sl") or 0)) if opt else abs(row["entry"] - row["sl"])
    if ex is None or not risk or risk <= 0:
        return {"r": None, "rs": None, "open": True}
    pts = (ex - entry) * sign
    qty = m.get("qty")
    return {"r": round(pts / risk, 2), "rs": round(pts * qty) if qty else None, "open": open_, "exit": ex}


def mine_report(folder, month, swing_rows, bnf_rows):
    """Every trade you marked as taken this month, with your result next to the scanner's (paper) result,
    and what the triggered trades you didn't take made on paper."""
    rows = []
    for f in sorted(folder.glob(f"trades-{month}-*.json")):
        try:
            for r in json.loads(f.read_text(encoding="utf-8")):
                rows.append(("intra", r))
        except (OSError, ValueError):
            continue
    rows += [("swing", r) for r in swing_rows if r.get("day", "").startswith(month)]
    rows += [("bnf", r) for r in bnf_rows if r.get("day", "").startswith(month)]
    taken, missed = [], []
    for kind, r in rows:
        day = datetime.fromtimestamp(r["picked_at"], IST).strftime("%Y-%m-%d")
        if r.get("mine"):
            res = mine_result(r, kind) or {}
            taken.append({"kind": kind, "day": day, "symbol": r["symbol"], "side": r["side"], "option": r.get("option"),
                          "picked_at": r["picked_at"], "mine": r["mine"], "real_r": res.get("r"), "real_rs": res.get("rs"),
                          "open": res.get("open"), "paper_r": r.get("opt_r") if kind == "bnf" and r["mine"]["entry"] < 10000
                          and r.get("opt_r") is not None else r.get("result_r"), "status": r.get("status")})
        elif r.get("triggered_at") and r.get("result_r") is not None:
            missed.append(r["result_r"])
    done = [t for t in taken if t["real_r"] is not None and not t["open"]]
    paper = [t["paper_r"] for t in done if t["paper_r"] is not None]
    return {"trades": sorted(taken, key=lambda t: -t["picked_at"]),
            "summary": {"taken": len(taken), "closed": len(done),
                        "wins": sum(1 for t in done if t["real_r"] > 0),
                        "real_r": round(sum(t["real_r"] for t in done), 2),
                        "real_rs": sum(t["real_rs"] for t in done if t["real_rs"] is not None),
                        "paper_r": round(sum(paper), 2) if paper else None,
                        "missed": len(missed), "missed_r": round(sum(missed), 2)}}


def set_mine(rows, picked_at, symbol, data):
    """Mark a trade as taken (data = {entry, exit, qty}) or unmark it (data = None). True if found."""
    for r in rows:
        if abs(r["picked_at"] - picked_at) < 1 and r["symbol"] == symbol:
            if data:
                r["mine"] = data
            else:
                r.pop("mine", None)
            return True
    return False
