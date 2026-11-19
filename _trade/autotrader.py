"""Auto-trader: swing buys on MTF, the top one or two setups a day, with hard limits.

The setup is the Meridian scanner's Swing rule set (_scanner/swing.py: daily-chart trend, an imbalance zone,
a price-action trigger). Longs only, because MTF can't be sold short.

Each trading day:
  15:10  scores every liquid F&O stock on today's forming daily candle and keeps the best 1-2 buy setups
         that pass the minimum score (chosen by the backtest). A setup is valid for 3 sessions.
  9:16   places a buy stop-limit (MTF) above the trigger day's high for each valid setup. It isn't chased:
         if the stock already opened more than half the risk above the entry, that day is skipped.
  fill   at once, a stop-loss for the whole position goes to Dhan as a Forever (GTT) order, so it stays at
         the exchange even if this server is off. If Dhan refuses the GTT, a day stop-loss order is used and
         placed again every morning.
  live   every 20 seconds: at T1 half is sold and the stop moves to cost; at T2 the rest is sold. After 10
         sessions anything left is sold at 15:15.

Size: 1% of capital at risk per trade (risk ÷ distance to the stop). Each position is at most 1× capital and all
of them together at most 2× (that is where MTF's margin is used). Hard stops: the bot stops by itself after a
drawdown of 8% of capital. Settings can't be changed while the market is open; Pause and Stop always work.

It starts in PAPER mode (live prices, no orders). Live needs a backtest that made money after costs on days it
wasn't tuned on, and at least 10 paper sessions.
"""
import json
import math
import os
import sys
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "_scanner"))
import swing                                            # noqa: E402  (the scanner's swing rules)

IST = timezone(timedelta(hours=5, minutes=30))
DEFAULTS = {"capital": 150000, "risk_pct": 1.0, "max_positions": 2, "max_new_per_day": 2,
            "min_score": None, "per_trade_cap_x": 1.0, "total_cap_x": 2.0, "max_drawdown_pct": 8.0,
            "paper_sessions_required": 10, "cost_pct": 0.25, "mtf_interest_day_pct": 0.04, "hands_off": True}
HIST_DAYS = 720                                          # calendar days of daily candles kept per stock (about 2 years)
WINDOW = 130                                             # sessions passed to swing.analyse (live and backtest)
SCORES = [0, 50, 55, 60, 65, 70]


def r2(x):
    return round(float(x), 2)


def tick_round(p, tick=0.05):
    return round(round(p / tick) * tick, 2)


def fmt_rs(v):
    return ("−" if v < 0 else "") + f"₹{abs(v):,.0f}"


# ---- the swing replay used by the backtest ---------------------------------------------------------
def simulate(hist, nifty, score_min, cfg, start=None, end=None):
    """Replay the bot on daily candles. hist: {sym: [daily candles]}, all ending on the same last day.
    Signals at a day's close, entries on the next 3 sessions, stops/targets on daily highs and lows
    (stop first when a day touches both). Returns the list of finished trades."""
    dates = sorted({_d(c["t"]) for d in hist.values() for c in d})
    idx = {s: {_d(c["t"]): i for i, c in enumerate(d)} for s, d in hist.items()}
    nidx = {_d(c["t"]): i for i, c in enumerate(nifty or [])}
    trades, open_, pending = [], [], []
    rules = {"sides": "bull", "score_min": score_min}
    for di, day in enumerate(dates):
        if di < 60 or (start and day < start) or (end and day > end):
            continue
        # 1. pending entries
        still = []
        for p in pending:
            i = idx[p["sym"]].get(day)
            if i is None:
                still.append(p)
                continue
            c = hist[p["sym"]][i]
            plan = p["plan"]
            risk = plan["entry"] - plan["sl"]
            if c["o"] > plan["entry"] + 0.5 * risk:
                p["days"] += 1                           # gapped too far above: not chased
            elif c["h"] >= plan["entry"] and len(open_) < cfg["max_positions"]:
                fill = max(c["o"], plan["entry"])
                qty = size(fill, plan["sl"], cfg, sum(o["qty"] * o["entry"] for o in open_))
                if qty:
                    pos = {"sym": p["sym"], "entry": fill, "sl": plan["sl"], "t1": plan["t1"], "t2": plan["t2"],
                           "qty": qty, "left": qty, "booked": 0.0, "day0": di, "score": p["score"], "date": day,
                           "half": False, "risk": fill - plan["sl"]}
                    open_.append(pos)
                    _manage(pos, c, di, day, trades, open_, cfg, first=True)
                    continue
                p["days"] += 1
            else:
                p["days"] += 1
            if p["days"] < swing.TRIGGER_DAYS:
                still.append(p)
        pending = still
        # 2. open positions
        for pos in list(open_):
            i = idx[pos["sym"]].get(day)
            if i is None or pos["day0"] == di:
                continue
            _manage(pos, hist[pos["sym"]][i], di, day, trades, open_, cfg)
        # 3. signals at today's close
        free = cfg["max_positions"] - len(open_) - len(pending)
        if free <= 0:
            continue
        held = {o["sym"] for o in open_} | {p["sym"] for p in pending}
        nd = nifty[max(0, nidx[day] - WINDOW + 1):nidx[day] + 1] if day in nidx else None
        cands = []
        for s, d in hist.items():
            i = idx[s].get(day)
            if i is None or i < 60 or s in held:
                continue
            c = swing.analyse(s, d[max(0, i - WINDOW + 1):i + 1], nd, None, rules)
            if c and c["side"] == "bull":
                cands.append(c)
        for c in swing.rank(cands, min(free, cfg["max_new_per_day"])):
            pending.append({"sym": c["symbol"], "plan": c["plan"], "score": c["score"], "days": 0})
    for pos in open_:                                     # still open at the end: marked at the last close
        d = hist[pos["sym"]]
        _close(pos, d[-1]["c"], len(dates) - 1, dates[-1], "open at end", trades, cfg)
    return trades


def _d(t):
    return datetime.fromtimestamp(int(t), IST).strftime("%Y-%m-%d")


def size(entry, sl, cfg, exposure=0.0):
    risk = entry - sl
    if risk <= 0:
        return 0
    cap = cfg["capital"]
    qty = math.floor(cap * cfg["risk_pct"] / 100 / risk)
    qty = min(qty, math.floor(cap * cfg["per_trade_cap_x"] / entry),
              math.floor(max(0.0, cap * cfg["total_cap_x"] - exposure) / entry))
    return max(0, qty)


def _manage(pos, c, di, day, trades, open_, cfg, first=False):
    """One daily candle for an open position: stop first, then T1 (half, stop to cost), then T2."""
    if c["o"] <= pos["sl"] and not first:
        return _close(pos, c["o"], di, day, "stop (gap)", trades, cfg, open_)
    if c["l"] <= pos["sl"]:
        return _close(pos, pos["sl"], di, day, "stop" if not pos["half"] else "stop at cost", trades, cfg, open_)
    if not pos["half"] and c["h"] >= pos["t1"]:
        half = pos["left"] // 2
        if half:
            pos["booked"] += half * (pos["t1"] - pos["entry"])
            pos["left"] -= half
        pos["half"] = True
        pos["sl"] = pos["entry"]
    if pos["half"] and c["h"] >= pos["t2"]:
        return _close(pos, pos["t2"], di, day, "target 2", trades, cfg, open_)
    if di - pos["day0"] >= swing.HOLD_DAYS:
        return _close(pos, c["c"], di, day, "10 sessions", trades, cfg, open_)


def _close(pos, price, di, day, why, trades, cfg, open_=None):
    gross = pos["booked"] + pos["left"] * (price - pos["entry"])
    value = pos["qty"] * pos["entry"]
    days = max(1, di - pos["day0"])
    funded = max(0.0, value - cfg["capital"] / cfg["max_positions"])
    cost = value * cfg["cost_pct"] / 100 + funded * cfg["mtf_interest_day_pct"] / 100 * days
    trades.append({"sym": pos["sym"], "date": pos["date"], "exit_date": day, "entry": r2(pos["entry"]),
                   "exit": r2(price), "qty": pos["qty"], "score": pos["score"], "why": why, "days": days,
                   "gross": r2(gross), "cost": r2(cost), "net": r2(gross - cost),
                   "r": round((gross - cost) / (pos["risk"] * pos["qty"]), 2) if pos["risk"] * pos["qty"] else 0})
    if open_ is not None and pos in open_:
        open_.remove(pos)


def stats(trades, sessions):
    n = len(trades)
    wins = [t for t in trades if t["net"] > 0]
    net = sum(t["net"] for t in trades)
    gw = sum(t["net"] for t in wins)
    gl = -sum(t["net"] for t in trades if t["net"] <= 0)
    run = peak = dd = 0.0
    for t in sorted(trades, key=lambda t: t["exit_date"]):
        run += t["net"]
        peak = max(peak, run)
        dd = min(dd, run - peak)
    rs = [t["r"] for t in trades]
    mean = sum(rs) / n if n else 0
    sd = math.sqrt(sum((x - mean) ** 2 for x in rs) / (n - 1)) if n > 1 else 0
    return {"trades": n, "win_rate": round(100 * len(wins) / n, 1) if n else 0, "net": r2(net),
            "t_stat": round(mean / sd * math.sqrt(n), 2) if sd else 0,
            "per_day": r2(net / sessions) if sessions else 0, "avg_r": round(sum(t["r"] for t in trades) / n, 2) if n else 0,
            "profit_factor": round(gw / gl, 2) if gl else None, "max_dd": r2(dd), "costs": r2(sum(t["cost"] for t in trades))}


def choose(hist, nifty, cfg, progress=lambda m: None):
    """Try each minimum score on the earlier two thirds of the history, keep the best by ₹ a day, and check it
    on the last third (never used to choose). Passed = the later days made money too, with enough trades."""
    dates = sorted({_d(c["t"]) for d in hist.values() for c in d})
    usable = dates[60:]
    if len(usable) < 90:
        return {"passed": False, "why": "Not enough history yet (needs about 7 months of daily candles).", "rows": []}
    cut = usable[int(len(usable) * 2 / 3)]
    rows = []
    for s in SCORES:
        progress(f"Testing minimum score {s}")
        tr = simulate(hist, nifty, s, cfg)
        early = [t for t in tr if t["date"] < cut]
        late = [t for t in tr if t["date"] >= cut]
        n_early = sum(1 for d in usable if d < cut)
        rows.append({"score": s, "all": stats(tr, len(usable)), "early": stats(early, n_early),
                     "late": stats(late, len(usable) - n_early), "trades": tr})
    best = max(rows, key=lambda r: r["early"]["per_day"])
    late = best["late"]
    robust = sum(1 for r in rows if r["late"]["net"] > 0) * 2 > len(rows)
    checks = [(best["early"]["net"] > 0, "the chosen setting lost money even on the days it was chosen on"),
              (late["net"] > 0, "it lost money on the later days it wasn't chosen on"),
              (late["trades"] >= 20, f"only {late['trades']} trades on the later days (20+ needed)"),
              ((late["profit_factor"] or 0) >= 1.15, f"profit factor on the later days {late['profit_factor']} (1.15+ needed)"),
              (late["t_stat"] >= 1.65, f"the later days' result could be luck (t-stat {late['t_stat']}, 1.65+ needed)"),
              (robust, "most other settings lost money on the later days, so the winner may just be luck")]
    fails = [w for ok, w in checks if not ok]
    passed = not fails
    why = (f"Minimum score {best['score']}: {best['all']['trades']} trades, {best['all']['win_rate']}% won, "
           f"{fmt_rs(best['all']['net'])} after costs; the later third (not used to choose) made {fmt_rs(late['net'])} "
           f"in {late['trades']} trades.")
    if not passed:
        why = "No edge yet: " + "; ".join(fails) + ". " + why + " The bot stays on paper."
    return {"passed": passed, "why": why, "score": best["score"], "split": cut, "from": usable[0], "to": usable[-1],
            "rows": [{k: v for k, v in r.items() if k != "trades"} for r in rows],
            "trades": best["trades"][-40:], "best": {k: v for k, v in best.items() if k != "trades"}}


# ---- the bot ----------------------------------------------------------------------------------------
class AutoTrader:
    def __init__(self, data_dir, dhan, instruments, quotes, now_fn, audit=lambda *a, **k: None, demo=False):
        self.path = os.path.join(data_dir, "autotrader.json")
        self.hist_path = os.path.join(data_dir, "daily-history.json")
        self.D, self.INST, self.QUOTES, self.now, self.audit, self.demo = dhan, instruments, quotes, now_fn, audit, demo
        self.lock = threading.RLock()
        self.s = self._load()
        self.hist, self.nifty = {}, []
        self.hist_day = ""
        self.job = {"running": False, "msg": ""}
        try:
            h = json.load(open(self.hist_path))
            self.hist, self.nifty, self.hist_day = h["hist"], h["nifty"], h["day"]
        except (OSError, ValueError, KeyError):
            pass

    # -- state
    def _load(self):
        try:
            s = json.load(open(self.path))
        except (OSError, ValueError):
            s = {}
        s.setdefault("mode", "paper")
        s.setdefault("armed", False)
        s.setdefault("halted", "")
        s.setdefault("settings", {})
        for k in ("setups", "positions", "closed", "log", "paper_days", "orders"):
            s.setdefault(k, [])
        s.setdefault("backtest", None)
        s.setdefault("done", {})
        return s

    def save(self):
        with self.lock:
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self.s, f, indent=1)
            os.replace(tmp, self.path)

    def cfg(self):
        c = dict(DEFAULTS, **self.s["settings"])
        if c["min_score"] is None:
            c["min_score"] = (self.s["backtest"] or {}).get("score", 60)
        return c

    def log(self, msg, kind="info"):
        t = self.now()
        self.s["log"].insert(0, {"at": t.strftime("%d %b %H:%M"), "msg": msg, "kind": kind})
        del self.s["log"][300:]
        self.audit("auto_" + kind, msg=msg, mode=self.s["mode"])

    @staticmethod
    def market_open(t):
        return t.weekday() < 5 and (9, 15) <= (t.hour, t.minute) < (15, 30)

    def locked(self):
        return self.market_open(self.now())

    # -- controls (from the app)
    def update_settings(self, new):
        if self.locked():
            return "Settings are locked while the market is open (9:15–3:30). Change them after the close."
        clean = {}
        lim = {"capital": (10000, 1e8), "risk_pct": (0.1, 2.0), "max_positions": (1, 2), "max_new_per_day": (1, 2),
               "min_score": (0, 100), "per_trade_cap_x": (0.2, 2.0), "total_cap_x": (0.2, 4.0),
               "max_drawdown_pct": (2, 20), "paper_sessions_required": (0, 60)}
        for k, (lo, hi) in lim.items():
            if k in new and new[k] not in (None, ""):
                v = float(new[k])
                if not lo <= v <= hi:
                    return f"{k.replace('_', ' ')} must be between {lo:g} and {hi:g}."
                clean[k] = int(v) if k in ("max_positions", "max_new_per_day", "paper_sessions_required") else v
            elif k == "min_score" and k in new:
                clean[k] = None
        if "hands_off" in new and self.locked():
            return "Hands off can only be changed after the close."
        if "hands_off" in new:
            clean["hands_off"] = bool(new["hands_off"])
        with self.lock:
            self.s["settings"].update(clean)
            self.log("Settings changed: " + ", ".join(f"{k} {v}" for k, v in clean.items()))
            self.save()
        return ""

    def arm(self, on):
        with self.lock:
            if on and self.s["halted"] and self.locked():
                return "The bot stopped itself today; it can be started again after the close."
            if not on and self.s["armed"] and self.locked():
                self.s["paused_day"] = self.now().strftime("%Y-%m-%d")   # hands-off holds until the close
            self.s["armed"] = bool(on)
            if on:
                self.s["halted"] = ""
            self.log("Bot started" if on else "Bot paused: no new trades (open positions keep their stops)")
            self.save()
        return ""

    def hands_off(self):
        """While the bot is on, manual orders may only close positions (unless switched off after hours)."""
        if not self.cfg()["hands_off"]:
            return False
        paused_today = self.s.get("paused_day") == self.now().strftime("%Y-%m-%d") and self.locked()
        return bool(self.s["armed"] or paused_today)

    def live_ready(self):
        bt = self.s["backtest"] or {}
        c = self.cfg()
        need = c["paper_sessions_required"]
        reasons = []
        if not bt.get("passed"):
            reasons.append("the backtest hasn't shown an edge after costs" if bt else "the backtest hasn't run yet")
        if len(self.s["paper_days"]) < need:
            reasons.append(f"{len(self.s['paper_days'])} of {need} paper sessions done")
        if self.demo:
            reasons.append("this is the demo account")
        return reasons

    def set_mode(self, mode, confirm=""):
        if mode == "live":
            if self.locked():
                return "Switch to live after the close."
            why = self.live_ready()
            if why:
                return "Not yet: " + "; ".join(why) + "."
            if confirm != "LIVE":
                return "Type LIVE to confirm."
        if mode not in ("paper", "live"):
            return "Unknown mode"
        with self.lock:
            if self.s["positions"] and mode != self.s["mode"]:
                return "Wait until the bot's open positions are closed."
            self.s["mode"] = mode
            self.s["setups"] = []
            self.log(f"Mode: {mode.upper()}", "warn" if mode == "live" else "info")
            self.save()
        return ""

    def close_all(self, reason="closed from the app"):
        with self.lock:
            for p in list(self.s["positions"]):
                self._exit(p, p["left"], reason)
            self.s["setups"] = []
            self.s["armed"] = False
            self.save()
        return ""

    # -- data
    def universe(self):
        out = {}
        for ul in self.INST.futs:
            k = self.INST.under.get(ul)
            if k and k.startswith("NSE_EQ:"):
                out[ul] = k
        return out

    def refresh_history(self, force=False):
        day = self.now().strftime("%Y-%m-%d")
        if self.hist_day == day and self.hist and not force:
            return
        uni = self.universe()
        t = self.now()
        frm = (t - timedelta(days=HIST_DAYS)).strftime("%Y-%m-%d")
        hist = {}
        for i, (s, k) in enumerate(sorted(uni.items())):
            self.job["msg"] = f"Daily candles {i + 1}/{len(uni)}"
            try:
                d = self.D.daily(k.split(":")[1], "NSE_EQ", "EQUITY", frm, day)
            except Exception:
                continue
            rows = _rows(d)
            rows = [c for c in rows if _d(c["t"]) < day]          # today is added live from quotes
            if len(rows) >= 80:
                hist[s] = rows
            time.sleep(0.05)
        nk = self.INST.under.get("NIFTY")
        try:
            self.nifty = [c for c in _rows(self.D.daily(nk.split(":")[1], "IDX_I", "INDEX", frm, day)) if _d(c["t"]) < day] if nk else []
        except Exception:
            self.nifty = []
        self.hist, self.hist_day = hist, day
        with open(self.hist_path + ".tmp", "w") as f:
            json.dump({"hist": hist, "nifty": self.nifty, "day": day}, f)
        os.replace(self.hist_path + ".tmp", self.hist_path)

    def run_backtest(self):
        if self.job["running"]:
            return
        self.job.update(running=True, msg="Starting")
        try:
            self.refresh_history()
            res = choose(self.hist, self.nifty, dict(self.cfg(), min_score=None),
                         progress=lambda m: self.job.update(msg=m))
            res["ran_at"] = self.now().strftime("%d %b %Y %H:%M")
            res["stocks"] = len(self.hist)
            with self.lock:
                self.s["backtest"] = res
                self.log(("Backtest passed. " if res["passed"] else "Backtest: no edge. ") + res["why"],
                         "info" if res["passed"] else "warn")
                if not res["passed"] and self.s["mode"] == "live" and not self.s["positions"]:
                    self.s["mode"] = "paper"
                    self.log("Back to PAPER: the latest backtest shows no edge.", "warn")
                self.save()
        except Exception as e:
            traceback.print_exc()
            self.job["msg"] = f"Backtest failed: {e}"
        finally:
            self.job["running"] = False

    def today_candles(self, syms):
        uni = self.universe()
        keys = [uni[s] for s in syms if s in uni]
        out = {}
        for i in range(0, len(keys), 900):
            got = self.D.quote({"NSE_EQ": [k.split(":")[1] for k in keys[i:i + 900]]}, "quote")
            for (seg, sid), q in got.items():
                o = q.get("ohlc") or {}
                ltp = float(q.get("last_price") or 0)
                if not (ltp and o.get("open")):
                    continue
                sym = next((s for s, k in uni.items() if k == f"{seg}:{sid}"), None)
                if sym:
                    out[sym] = {"t": int(self.now().replace(hour=9, minute=15).timestamp()), "o": float(o["open"]),
                                "h": max(float(o.get("high") or ltp), ltp), "l": min(float(o.get("low") or ltp), ltp),
                                "c": ltp, "v": float(q.get("volume") or 0), "ltt": q.get("last_trade_time")}
        return out

    def ltp(self, key):
        q = self.QUOTES.get(key)
        if q and time.time() - q.get("t", 0) < 30 and q.get("ltp"):
            return float(q["ltp"])
        seg, sid = key.split(":")
        got = self.D.quote({seg: [sid]}, "ltp")
        v = got.get((seg, sid)) or {}
        return float(v.get("last_price") or 0) or None

    # -- the day
    def tick(self):
        """Called every ~20 s by the server."""
        t = self.now()
        day = t.strftime("%Y-%m-%d")
        hm = (t.hour, t.minute)
        done = self.s["done"]
        try:
            week = t.strftime("%Y-%W")
            if (t.weekday() == 5 or not self.s["backtest"]) and done.get("bt_week") != week and not self.job["running"]:
                done["bt_week"] = week                      # once a week (Saturday), and at the very start
                threading.Thread(target=self.run_backtest, daemon=True).start()
            if t.weekday() >= 5:
                return
            if hm >= (8, 30) and hm < (9, 10) and done.get("hist") != day:
                done["hist"] = day
                threading.Thread(target=self.refresh_history, daemon=True).start()
            if (9, 16) <= hm < (9, 45) and done.get("morning") != day:
                done["morning"] = day
                self.morning()
            if self.market_open(t):
                self.monitor()
            if (15, 10) <= hm < (15, 25) and done.get("signal") != day:
                done["signal"] = day
                self.signal()
            if hm >= (15, 31) and done.get("eod") != day:
                done["eod"] = day
                self.eod()
        except Exception as e:
            traceback.print_exc()
            self.log(f"Problem: {e}", "error")
        finally:
            self.save()

    def signal(self):
        """15:10: today's best 1-2 swing buys."""
        if not self.s["armed"] or self.s["halted"]:
            return
        c = self.cfg()
        if not self.hist:
            self.refresh_history()
        open_syms = {p["sym"] for p in self.s["positions"]} | {x["sym"] for x in self.s["setups"]}
        free = min(c["max_new_per_day"], c["max_positions"] - len(self.s["positions"]) - len(self.s["setups"]))
        if free <= 0:
            self.log("No new setups today: the bot already has its maximum.")
            return
        today = self.today_candles(list(self.hist))
        day = self.now().strftime("%Y-%m-%d")
        live_today = [x for x in today.values() if str(x.get("ltt") or "").startswith(self.now().strftime("%d/%m/%Y"))]
        if today and any(x.get("ltt") for x in today.values()) and not live_today:
            self.log("Market closed today (holiday): no setups.")
            return
        nifty = self.nifty[-WINDOW:]
        cands = []
        for s, d in self.hist.items():
            if s in open_syms or s not in today:
                continue
            r = swing.analyse(s, (d + [today[s]])[-WINDOW:], nifty, None, {"sides": "bull", "score_min": c["min_score"]})
            if r and r["side"] == "bull":
                cands.append(r)
        best = swing.rank(cands, free)
        if self.s["mode"] == "paper" and day not in self.s["paper_days"]:
            self.s["paper_days"].append(day)
        if not best:
            self.log(f"No setup scored {c['min_score']}+ today. No trade is a fine result.")
            return
        for r in best:
            p = r["plan"]
            it = self.INST.get(self.universe()[r["symbol"]])
            self.s["setups"].append({"sym": r["symbol"], "key": it["key"], "tick": it["tick"], "score": r["score"],
                                     "entry": p["entry"], "sl": p["sl"], "t1": p["t1"], "t2": p["t2"],
                                     "made": day, "days": 0, "why": r["why"], "order": None})
            self.log(f"Setup {r['symbol']} (score {r['score']}): buy above {p['entry']}, stop {p['sl']}, "
                     f"T1 {p['t1']}, T2 {p['t2']}")

    def morning(self):
        """9:16: buy orders for the valid setups; day stops placed again where needed."""
        for p in self.s["positions"]:
            if p.get("stop_kind") == "day" and self.s["mode"] == "live":
                self._place_stop(p)
        if not self.s["armed"] or self.s["halted"]:
            return
        c = self.cfg()
        keep = []
        exposure = sum(p["left"] * p["entry"] for p in self.s["positions"])
        for st in self.s["setups"]:
            st["days"] += 1
            if st["days"] > swing.TRIGGER_DAYS:
                self.log(f"{st['sym']}: entry didn't trigger in {swing.TRIGGER_DAYS} sessions; dropped.")
                continue
            if len(self.s["positions"]) >= c["max_positions"]:
                keep.append(st)
                continue
            px = self.ltp(st["key"])
            risk = st["entry"] - st["sl"]
            if px and px > st["entry"] + 0.5 * risk:
                self.log(f"{st['sym']}: opened at {px}, too far above the entry {st['entry']}; not chased today.")
                keep.append(st)
                continue
            if px and px <= st["sl"]:
                self.log(f"{st['sym']}: already below the stop; setup dropped.")
                continue
            qty = size(st["entry"], st["sl"], c, exposure)
            if not qty:
                self.log(f"{st['sym']}: size works out to 0 shares (risk or exposure limit); skipped.")
                continue
            st["qty"] = qty
            limit = tick_round(st["entry"] + 0.25 * risk, st["tick"])
            st["limit"] = limit
            if self.s["mode"] == "live":
                try:
                    r = self.D.place_order({"securityId": st["key"].split(":")[1], "exchangeSegment": "NSE_EQ",
                                            "transactionType": "BUY", "quantity": qty, "orderType": "STOP_LOSS",
                                            "productType": "MTF", "price": limit,
                                            "triggerPrice": tick_round(st["entry"], st["tick"]), "validity": "DAY",
                                            "correlationId": f"auto-{st['sym'][:10]}-{st['days']}"})
                    st["order"] = str(r.get("orderId"))
                    self.s["orders"].append(st["order"])
                    self.log(f"LIVE buy order {st['sym']} {qty} @ trigger {st['entry']} (limit {limit}), MTF. #{st['order']}")
                except Exception as e:
                    self.log(f"{st['sym']}: Dhan refused the buy order: {e}", "error")
                    keep.append(st)
                    continue
            else:
                self.log(f"Paper buy order {st['sym']} {qty} @ trigger {st['entry']} (limit {limit}).")
            exposure += qty * st["entry"]
            keep.append(st)
        self.s["setups"] = keep

    def monitor(self):
        c = self.cfg()
        # entries
        for st in list(self.s["setups"]):
            if not st.get("qty"):
                continue
            fill = None
            if self.s["mode"] == "live" and st.get("order"):
                try:
                    o = self.D.order(st["order"])
                except Exception:
                    continue
                if o.get("orderStatus") in ("TRADED",) or (o.get("filledQty") and o.get("orderStatus") in ("PART_TRADED",)):
                    fill = (float(o.get("averageTradedPrice") or st["entry"]), int(o.get("filledQty") or st["qty"]))
                elif o.get("orderStatus") in ("REJECTED", "CANCELLED", "EXPIRED"):
                    self.log(f"{st['sym']}: buy order {o.get('orderStatus').lower()} "
                             f"{('(' + o.get('omsErrorDescription') + ')') if o.get('omsErrorDescription') else ''}", "warn")
                    st["order"] = None
                    st["qty"] = 0
            elif self.s["mode"] == "paper":
                px = self.ltp(st["key"])
                if px and st["entry"] <= px <= st["limit"]:
                    fill = (px, st["qty"])
            if fill:
                self.s["setups"].remove(st)
                pos = {k: st[k] for k in ("sym", "key", "tick", "score", "entry", "sl", "t1", "t2", "why")}
                pos.update(entry=r2(fill[0]), plan_entry=st["entry"], qty=fill[1], left=fill[1], booked=0.0, half=False,
                           opened=self.now().strftime("%Y-%m-%d %H:%M"), sessions=0, risk=r2(fill[0] - st["sl"]),
                           stop_id=None, stop_kind=None, mode=self.s["mode"])
                if pos["mode"] == "live":                   # sells already in today's book aren't this position's
                    pos["seen"] = self._sell_ids(pos)
                self.s["positions"].append(pos)
                self.log(f"{'LIVE' if pos['mode'] == 'live' else 'Paper'} BOUGHT {pos['sym']} {pos['qty']} @ {pos['entry']}")
                if pos["mode"] == "live":
                    self._place_stop(pos)
        # exits
        for p in list(self.s["positions"]):
            px = self.ltp(p["key"])
            if not px:
                continue
            p["ltp"] = px
            if p["mode"] == "live" and self._stop_filled(p):
                continue
            if px <= p["sl"]:
                if p["mode"] == "paper":
                    self._exit(p, p["left"], "stop at cost" if p["half"] else "stop", px)
                elif px < p["sl"] * 0.985:                # gapped through the stop's limit: get out
                    self._exit(p, p["left"], "below the stop")
                continue
            if not p["half"] and px >= p["t1"]:
                half = p["left"] // 2
                if half:
                    self._exit(p, half, "target 1 (half)", px)
                p["half"] = True
                p["sl"] = p["entry"]
                self.log(f"{p['sym']}: T1 reached. Stop moved to cost {p['entry']}.")
                if p["mode"] == "live" and p["left"]:
                    self._place_stop(p)
            elif p["half"] and px >= p["t2"]:
                self._exit(p, p["left"], "target 2", px)
        # time stop and drawdown
        t = self.now()
        if (t.hour, t.minute) >= (15, 15):
            for p in list(self.s["positions"]):
                if p["sessions"] >= swing.HOLD_DAYS:
                    self._exit(p, p["left"], "10 sessions")
        self._check_drawdown(c)

    def eod(self):
        for p in self.s["positions"]:
            p["sessions"] += 1
        for st in self.s["setups"]:                      # day orders expire; a fresh one goes in tomorrow
            st["order"] = None
            st.pop("qty", None)

    # -- orders
    def _place_stop(self, p):
        """Stop-loss for the position at Dhan: a Forever (GTT) sell, or a day SL-M sell if GTT is refused."""
        self._cancel_stop(p)
        sid = p["key"].split(":")[1]
        trig = tick_round(p["sl"], p["tick"])
        try:
            r = self.D.place_forever({"orderFlag": "SINGLE", "transactionType": "SELL", "exchangeSegment": "NSE_EQ",
                                      "productType": "MTF", "orderType": "LIMIT", "validity": "DAY", "securityId": sid,
                                      "quantity": p["left"], "price": tick_round(trig * 0.99, p["tick"]),
                                      "triggerPrice": trig})
            p["stop_id"], p["stop_kind"] = str(r.get("orderId")), "gtt"
            self.s["orders"].append(p["stop_id"])
            self.log(f"{p['sym']}: stop-loss {trig} placed at Dhan (Forever order #{p['stop_id']}).")
            return
        except Exception as e:
            self.log(f"{p['sym']}: Forever stop refused ({e}); using a day stop-loss order.", "warn")
        try:
            r = self.D.place_order({"securityId": sid, "exchangeSegment": "NSE_EQ", "transactionType": "SELL",
                                    "quantity": p["left"], "orderType": "STOP_LOSS_MARKET", "productType": "MTF",
                                    "price": 0, "triggerPrice": trig, "validity": "DAY"})
            p["stop_id"], p["stop_kind"] = str(r.get("orderId")), "day"
            self.s["orders"].append(p["stop_id"])
            self.log(f"{p['sym']}: day stop-loss {trig} placed (#{p['stop_id']}).")
        except Exception as e:
            p["stop_id"], p["stop_kind"] = None, None
            self.log(f"{p['sym']}: COULD NOT PLACE A STOP-LOSS ({e}). The bot watches the price and sells at the stop.", "error")

    def _cancel_stop(self, p):
        if not p.get("stop_id"):
            return
        try:
            if p["stop_kind"] == "gtt":
                self.D.cancel_forever(p["stop_id"])
            else:
                self.D.cancel_order(p["stop_id"])
        except Exception as e:
            self.log(f"{p['sym']}: cancelling the stop #{p['stop_id']} said: {e}", "warn")
        p["stop_id"], p["stop_kind"] = None, None

    def _sell_ids(self, p):
        try:
            rows = self.D.trades_today()
        except Exception:
            return []
        sid = p["key"].split(":")[1]
        return [str(r.get("orderId")) for r in rows if str(r.get("securityId")) == sid and r.get("transactionType") == "SELL"]

    def _unknown_sells(self, p):
        """Sell fills today on this stock that the bot didn't send itself (its stop-loss triggering)."""
        try:
            rows = self.D.trades_today()
        except Exception:
            return 0, 0.0
        mine = set(self.s["orders"]) - {p.get("stop_id")}
        q = v = 0
        for r in rows:
            if str(r.get("securityId")) == p["key"].split(":")[1] and r.get("transactionType") == "SELL" \
                    and str(r.get("productType") or "MTF") == "MTF" and str(r.get("orderId")) not in mine and str(r.get("orderId")) not in p.setdefault("seen", []):
                q += int(r.get("tradedQuantity") or 0)
                v += int(r.get("tradedQuantity") or 0) * float(r.get("tradedPrice") or 0)
                p["seen"].append(str(r.get("orderId")))
        return q, (v / q if q else 0.0)

    def _stop_filled(self, p):
        """The stop-loss filled at Dhan, or the position was sold by hand: book it."""
        q, avg = self._unknown_sells(p)
        if not q:
            return False
        near = p["ltp"] <= p["sl"] * 1.02
        self._book(p, min(q, p["left"]), avg, ("stop at cost" if p["half"] else "stop") if near else "sold by hand")
        p["stop_id"] = None
        if p["left"] > 0:
            self._place_stop(p)
        return True

    def _exit(self, p, qty, why, px=None):
        if qty <= 0:
            return
        if p["mode"] == "live":
            self._cancel_stop(p)
            q, avg = self._unknown_sells(p)              # the stop may have filled just before the cancel
            if q:
                self._book(p, min(q, p["left"]), avg, "stop")
                qty = min(qty, p["left"])
                if qty <= 0:
                    return
            try:
                r = self.D.place_order({"securityId": p["key"].split(":")[1], "exchangeSegment": "NSE_EQ",
                                        "transactionType": "SELL", "quantity": qty, "orderType": "MARKET",
                                        "productType": "MTF", "price": 0, "triggerPrice": 0, "validity": "DAY"})
                oid = str(r.get("orderId"))
                self.s["orders"].append(oid)
                price = None
                for _ in range(10):
                    try:
                        o = self.D.order(oid)
                    except Exception:
                        o = {}
                    if o.get("orderStatus") == "TRADED":
                        price = float(o.get("averageTradedPrice") or 0) or None
                        break
                    if o.get("orderStatus") in ("REJECTED", "CANCELLED"):
                        raise RuntimeError(o.get("omsErrorDescription") or o.get("orderStatus"))
                    time.sleep(1)
                self._book(p, qty, price or px or p.get("ltp") or p["entry"], why)
            except Exception as e:
                self.log(f"{p['sym']}: SELL FAILED ({e}). Check the position in Dhan.", "error")
                if p["left"]:
                    self._place_stop(p)
                return
            if p["left"] > 0 and p in self.s["positions"]:
                self._place_stop(p)
        else:
            self._book(p, qty, px or p.get("ltp") or p["entry"], why)

    def _book(self, p, qty, price, why):
        c = self.cfg()
        p["booked"] += qty * (price - p["entry"])
        p["left"] -= qty
        self.log(f"{'LIVE' if p['mode'] == 'live' else 'Paper'} SOLD {p['sym']} {qty} @ {r2(price)} ({why})")
        if p["left"] <= 0:
            value = p["qty"] * p["entry"]
            funded = max(0.0, value - c["capital"] / c["max_positions"])
            cost = value * c["cost_pct"] / 100 + funded * c["mtf_interest_day_pct"] / 100 * max(1, p["sessions"])
            net = p["booked"] - cost
            self.s["closed"].insert(0, {"sym": p["sym"], "mode": p["mode"], "opened": p["opened"],
                                        "closed": self.now().strftime("%Y-%m-%d %H:%M"), "qty": p["qty"],
                                        "entry": p["entry"], "exit": r2(price), "why": why, "score": p["score"],
                                        "gross": r2(p["booked"]), "cost": r2(cost), "net": r2(net),
                                        "r": round(net / (p["risk"] * p["qty"]), 2) if p["risk"] * p["qty"] else 0})
            if p in self.s["positions"]:
                self.s["positions"].remove(p)

    def _check_drawdown(self, c):
        mode = self.s["mode"]
        run = peak = 0.0
        for t in reversed([x for x in self.s["closed"] if x["mode"] == mode]):
            run += t["net"]
            peak = max(peak, run)
        open_pl = sum(p["booked"] + p["left"] * ((p.get("ltp") or p["entry"]) - p["entry"]) for p in self.s["positions"])
        dd = (run + open_pl) - peak
        if dd <= -c["capital"] * c["max_drawdown_pct"] / 100 and not self.s["halted"]:
            self.s["halted"] = f"Drawdown {fmt_rs(dd)} reached the {c['max_drawdown_pct']}% limit"
            self.s["armed"] = False
            self.s["setups"] = []
            self.log(f"BOT STOPPED ITSELF: {self.s['halted']}. Open positions keep their stops; no new trades.", "error")

    # -- for the app
    def view(self):
        c = self.cfg()
        closed = [t for t in self.s["closed"] if t["mode"] == self.s["mode"]]
        tot = sum(t["net"] for t in closed)
        wins = sum(1 for t in closed if t["net"] > 0)
        bt = self.s["backtest"]
        return {"mode": self.s["mode"], "armed": self.s["armed"], "halted": self.s["halted"], "locked": self.locked(),
                "settings": c, "settings_raw": self.s["settings"], "setups": self.s["setups"], "positions": self.s["positions"],
                "closed": self.s["closed"][:60], "log": self.s["log"][:80],
                "summary": {"trades": len(closed), "net": r2(tot), "win_rate": round(100 * wins / len(closed), 1) if closed else 0},
                "paper_days": len(self.s["paper_days"]), "live_blockers": self.live_ready(), "backtest": bt,
                "job": self.job, "history": {"stocks": len(self.hist), "day": self.hist_day}}


def _rows(d):
    ks = ("timestamp", "open", "high", "low", "close", "volume")
    if not isinstance(d, dict) or not all(isinstance(d.get(k), list) for k in ks):
        return []
    return [{"t": int(t), "o": float(o), "h": float(h), "l": float(l), "c": float(c), "v": float(v)}
            for t, o, h, l, c, v in zip(*(d[k] for k in ks))]
