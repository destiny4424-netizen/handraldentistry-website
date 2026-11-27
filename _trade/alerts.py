"""What to tell the phone, checked every 30 seconds.

  orders    an order filled, or was rejected (with Dhan's reason); a stop-loss filling shows up here too
  pnl       today's P&L crosses another loss step (every ₹5,000 by default), and the daily loss limit
  bot       the auto-trader buys, sells, stops itself or has a problem
  rules     an order waiting for confirmation is sent, expires or is dropped
  routine   9:00 AM the day's plan, 3:45 PM the journal (if not written yet), on weekdays

Everything sent is also kept in a list shown in the app (Account → Notifications).
"""
import json
import os
import threading
import traceback

KINDS = {"orders": "Orders filled or rejected", "pnl": "Loss steps and the daily loss limit",
         "bot": "Auto-trader trades and problems", "rules": "Orders waiting for confirmation",
         "routine": "9 AM plan and 3:45 PM journal reminders", "klb": "HTF-KLB signals (15m, all F&O stocks)"}
DEFAULTS = {"kinds": list(KINDS), "loss_step": 5000}


def rs(v):
    s = "−" if v < 0 else ""
    v = abs(v)
    return f"{s}₹{v:,.2f}"


class Alerts:
    def __init__(self, data_dir, push, now_fn):
        self.path = os.path.join(data_dir, "alerts.json")
        self.push, self.now = push, now_fn
        self.lock = threading.Lock()
        try:
            self.s = json.load(open(self.path))
        except (OSError, ValueError):
            self.s = {}
        for k, v in (("settings", {}), ("seen_orders", {}), ("history", []), ("day", {}), ("seen_bot", []),
                     ("seen_rules", [])):
            self.s.setdefault(k, v)
        self.primed = False

    def cfg(self):
        return dict(DEFAULTS, **self.s["settings"])

    def update_settings(self, b):
        c = {}
        if "kinds" in b:
            c["kinds"] = [k for k in b["kinds"] if k in KINDS]
        if b.get("loss_step") not in (None, ""):
            v = float(b["loss_step"])
            if not 500 <= v <= 1e7:
                return "The loss step must be between ₹500 and ₹1 crore."
            c["loss_step"] = v
        self.s["settings"].update(c)
        self.save()
        return ""

    def save(self):
        with self.lock:
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self.s, f)
            os.replace(tmp, self.path)

    def notify(self, kind, title, body, url="/"):
        if kind not in self.cfg()["kinds"] and kind != "test":
            return
        self.s["history"].insert(0, {"at": self.now().strftime("%d %b %H:%M"), "kind": kind, "title": title, "body": body})
        del self.s["history"][100:]
        self.push.send(title, body, tag=f"{kind}-{title}", url=url)

    # -- checks -----------------------------------------------------------------------------------
    def check_orders(self, orders, names):
        day = self.now().strftime("%Y-%m-%d")
        seen = self.s["seen_orders"]
        if seen.get("_day") != day:
            seen.clear()
            seen["_day"] = day
        for o in orders:
            oid = str(o.get("orderId") or "")
            st = o.get("orderStatus")
            if not oid or st not in ("TRADED", "REJECTED") or seen.get(oid) == st:
                continue
            first = not self.primed
            seen[oid] = st
            if first:                                    # orders from before the app started: no flood
                continue
            name = names.get(oid) or o.get("tradingSymbol") or "Order"
            side = o.get("transactionType", "")
            qty = int(o.get("filledQty") or o.get("quantity") or 0)
            if st == "TRADED":
                leg = o.get("legName") or ""
                what = "Stop-loss hit" if leg == "STOP_LOSS_LEG" else "Target hit" if leg == "TARGET_LEG" else "Filled"
                self.notify("orders", f"{what}: {side} {name}",
                            f"{qty:,} @ ₹{float(o.get('averageTradedPrice') or o.get('price') or 0):,.2f}", "/#orders")
            else:
                self.notify("orders", f"Rejected: {side} {name}",
                            (o.get("omsErrorDescription") or "Dhan rejected the order.")[:160], "/#orders")

    def check_pnl(self, pnl, loss_limit):
        day = self.now().strftime("%Y-%m-%d")
        d = self.s["day"]
        if d.get("day") != day:
            d.clear()
            d.update(day=day, step=0, limit=False)
        step = self.cfg()["loss_step"]
        n = int(-pnl // step) if pnl < 0 else 0
        if n > d["step"]:
            d["step"] = n
            self.notify("pnl", f"Today's P&L {rs(pnl)}",
                        f"Down more than {rs(n * step).lstrip('−')} today. Stick to your stops; no new trades to win it back.",
                        "/#positions")
        if loss_limit and pnl <= -loss_limit and not d["limit"]:
            d["limit"] = True
            self.notify("pnl", "Daily loss limit reached",
                        f"{rs(pnl)} today. Only orders that close a position are allowed until tomorrow.", "/#positions")

    def check_log(self, kind, entries, key, words):
        """New lines in the bot's or the rules' log (newest first)."""
        seen = self.s[key]
        fresh = [e for e in entries if f"{e.get('at')}|{e.get('msg')}" not in seen]
        for e in reversed(fresh):
            seen.append(f"{e.get('at')}|{e.get('msg')}")
            if self.primed and any(w in e.get("msg", "") for w in words):
                title = ("Auto-trader" if kind == "bot" else "Rules: " + str(e.get("sym") or "order"))
                self.notify(kind, title, e["msg"][:180], "/#auto" if kind == "bot" else "/#orders")
        del seen[:-300]

    def check_routine(self, journal_done):
        t = self.now()
        if t.weekday() >= 5:
            return
        day = t.strftime("%Y-%m-%d")
        d = self.s["day"]
        if (9, 0) <= (t.hour, t.minute) < (9, 15) and d.get("plan") != day:
            d["plan"] = day
            self.notify("routine", "Plan your day", "Open the plan card: levels, trades left, yesterday's note. "
                                                    "No level, no trade.", "/#watch")
        if (15, 45) <= (t.hour, t.minute) < (18, 0) and d.get("journal") != day and not journal_done:
            d["journal"] = day
            self.notify("routine", "Journal time", "Two minutes: did you follow your rules today?", "/#journal")

    def tick(self, orders=None, names=None, pnl=None, loss_limit=0, bot_log=None, rules_log=None, journal_done=True):
        try:
            if orders is not None:
                self.check_orders(orders, names or {})
            if pnl is not None and self.primed:
                self.check_pnl(pnl, loss_limit)
            if bot_log is not None:
                self.check_log("bot", bot_log, "seen_bot", ("BOUGHT", "SOLD", "STOPPED", "COULD NOT", "FAILED", "Problem"))
            if rules_log is not None:
                self.check_log("rules", rules_log, "seen_rules", ("Order sent", "not taken", "failed", "Not taken"))
            if self.primed:
                self.check_routine(journal_done)
            self.primed = True
        except Exception:
            traceback.print_exc()
        self.save()
