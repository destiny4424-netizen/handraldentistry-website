"""Quick option trade: one tap from "RELIANCE is going up" to a limit order on its 1-strike-OTM call,
then the stop-loss order placed by itself once the buy fills.

    plan:   the underlying's option chain -> the strike at the money (nearest the spot) -> one strike further
            out of the money in the trade's direction (call for up, put for down). The nearest expiry, unless it
            is today or tomorrow (then the next one: the last days' time decay is brutal). The limit price
            starts at the best ask (else the last price), one lot, a stop 30% under the price.
    order:  a plain LIMIT buy (no super order), through the app's normal checks.
    watch:  every few seconds the order's status at Dhan: when it fills, a SELL stop-loss order (SL, with a
            limit a little under the trigger so it can't fill at a silly price) is placed for the filled
            quantity. A partial fill gets a stop for what has filled, resized when the rest fills. A rejected
            or cancelled buy is dropped. Each step is kept in quick.json and sent as a phone notification.
"""
import json
import os
import threading
import time
from datetime import date

FILLED, PARTIAL = "TRADED", "PART_TRADED"
DEAD = ("REJECTED", "CANCELLED", "EXPIRED")


def tick_round(p, tick=0.05):
    tick = tick or 0.05
    return round(round(float(p) / tick) * tick, 2)


def pick_expiry(expiries, today=None):
    """The nearest expiry with at least 2 days to go."""
    today = today or date.today()
    ok = [e for e in sorted(expiries) if e >= today.isoformat()]
    for e in ok:
        if (date.fromisoformat(e) - today).days >= 2:
            return e
    return ok[-1] if ok else (sorted(expiries)[-1] if expiries else None)


def pick_strike(strikes, spot, direction):
    """(at-the-money strike, the strike one step out of the money in the trade's direction)."""
    s = sorted(strikes)
    if not s or not spot:
        return None, None
    atm = min(s, key=lambda k: abs(k - spot))
    i = s.index(atm)
    j = i + 1 if direction == "up" else i - 1
    return atm, (s[j] if 0 <= j < len(s) else None)


def default_stop(price, tick, pct=30):
    return max(tick or 0.05, tick_round(price * (1 - pct / 100), tick))


def sl_limit(trigger, tick):
    """The limit price of a sell stop-loss: 5% under the trigger (at least 2 ticks), never below one tick."""
    tick = tick or 0.05
    return max(tick, tick_round(min(trigger - 2 * tick, trigger * 0.95), tick))


def money(available, price, qty, stop):
    used = round(price * qty, 2)
    return {"available": round(available, 2) if available is not None else None, "used": used,
            "remaining": round(available - used, 2) if available is not None else None,
            "risk": round(max(0.0, price - stop) * qty, 2) if stop else None}


class Watcher:
    """Buys waiting to fill, and the stop-loss each one gets. place_fn(body) -> (code, reply) is the app's own
    order route; order_fn(id) / modify_fn(id, fields) / notify(title, body) talk to Dhan and the phone."""
    def __init__(self, folder, order_fn, place_fn, modify_fn, notify=lambda t, b: None, now=time.time):
        self.path = os.path.join(folder, "quick.json")
        self.order_fn, self.place_fn, self.modify_fn, self.notify, self.now = order_fn, place_fn, modify_fn, notify, now
        self.lock = threading.Lock()
        try:
            with open(self.path) as f:
                self.items = json.load(f)
        except (OSError, ValueError):
            self.items = []

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.items[-100:], f)
        os.replace(tmp, self.path)

    def add(self, order_id, key, name, qty, price, stop, product, tick):
        with self.lock:
            self.items.append({"order_id": str(order_id), "key": key, "name": name, "qty": int(qty), "price": price,
                               "stop": stop, "product": product, "tick": tick, "at": self.now(), "status": "waiting",
                               "filled": 0, "stop_id": None, "stop_qty": 0, "note": "Waiting for the buy to fill"})
            self.save()

    def open_items(self):
        return [x for x in self.items if x["status"] in ("waiting", "partial")]

    def view(self):
        return sorted(self.items, key=lambda x: -x["at"])[:20]

    def check(self):
        """One pass over the waiting buys (called every few seconds by the app)."""
        changed = False
        with self.lock:
            for x in self.open_items():
                try:
                    o = self.order_fn(x["order_id"]) or {}
                except Exception as e:                    # Dhan unreachable: try again next pass
                    x["note"] = f"Can't read the order from Dhan: {e}"
                    continue
                st = str(o.get("orderStatus") or "")
                filled = int(o.get("filledQty") or (x["qty"] if st == FILLED else 0))
                avg = float(o.get("averageTradedPrice") or 0) or x["price"]
                if st in DEAD and filled == 0:
                    x.update(status="dropped", note=f"The buy was {st.lower()} by Dhan: no stop needed"
                             + (f" ({o.get('omsErrorDescription')})" if o.get("omsErrorDescription") else ""))
                    self.notify(f"{x['name']}: buy {st.lower()}", x["note"])
                    changed = True
                    continue
                if filled <= x["stop_qty"]:
                    continue
                x["filled"], x["fill_price"] = filled, avg
                self._protect(x, filled)
                whole = st == FILLED or (st in DEAD and filled)
                if not x["stop_id"]:                       # the stop didn't go in: try again next pass (5 times)
                    x["tries"] = x.get("tries", 0) + 1
                    x["status"] = "no_stop" if x["tries"] >= 5 else ("waiting" if whole else "partial")
                else:
                    x["status"] = "done" if whole else "partial"
                changed = True
            if changed:
                self.save()

    def _protect(self, x, filled):
        """Place (or resize) the stop-loss for the filled quantity."""
        trig = tick_round(x["stop"], x["tick"])
        lim = sl_limit(trig, x["tick"])
        if x["stop_id"]:
            try:
                self.modify_fn(x["stop_id"], {"orderType": "STOP_LOSS", "quantity": filled, "price": lim,
                                              "triggerPrice": trig, "validity": "DAY"})
                x["stop_qty"] = filled
                x["note"] = f"Filled {filled} at ₹{x['fill_price']:,.2f} · stop-loss resized to {filled}"
            except Exception as e:
                x["note"] = f"Filled {filled}, but resizing the stop failed: {e}. Check the stop in Orders."
                self.notify(f"⚠ {x['name']}: check your stop", x["note"])
            return
        code, r = self.place_fn({"key": x["key"], "side": "SELL", "qty": filled, "product": x["product"],
                                 "type": "STOP_LOSS", "price": lim, "trigger": trig, "validity": "DAY", "repeat_ok": True})
        if code == 200 and r.get("ok"):
            row = (r.get("orders") or [{}])[0]
            x["stop_id"], x["stop_qty"] = str(row.get("orderId") or ""), filled
            x["note"] = f"Bought {filled} at ₹{x['fill_price']:,.2f} · stop-loss placed: trigger ₹{trig:,.2f}, limit ₹{lim:,.2f}"
            self.notify(f"✓ {x['name']}: stop-loss placed", x["note"])
        else:
            x["note"] = f"Bought {filled} at ₹{x['fill_price']:,.2f}, but the stop-loss was NOT placed: " \
                        f"{r.get('error') or code}. Retrying; if it keeps failing, place it yourself (Positions → stop)."
            if x.get("tries", 0) in (0, 4):              # tell the phone on the first failure and the last try
                self.notify(f"⚠ {x['name']}: NO stop-loss yet", x["note"])
