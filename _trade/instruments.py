"""Every instrument Dhan lists (stocks, indices, futures, options on NSE, BSE and MCX), read from
Dhan's scrip master, with search and option-chain lookups."""
import csv
import mcx
import os
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"

# (exchange, segment code) -> Dhan exchangeSegment
SEG = {("NSE", "E"): "NSE_EQ", ("BSE", "E"): "BSE_EQ", ("NSE", "D"): "NSE_FNO", ("BSE", "D"): "BSE_FNO",
       ("NSE", "C"): "NSE_CURRENCY", ("BSE", "C"): "BSE_CURRENCY", ("MCX", "M"): "MCX_COMM",
       ("NSE", "I"): "IDX_I", ("BSE", "I"): "IDX_I", ("MCX", "I"): "IDX_I"}
DERIV = {"FUTIDX", "OPTIDX", "FUTSTK", "OPTSTK", "FUTCOM", "OPTFUT", "FUTCUR", "OPTCUR"}
# Most-traded first when a search matches several kinds.
KIND_RANK = {"INDEX": 0, "EQUITY": 1, "FUTIDX": 2, "FUTSTK": 3, "OPTIDX": 4, "OPTSTK": 5,
             "FUTCOM": 6, "OPTFUT": 7, "FUTCUR": 8, "OPTCUR": 9}
POPULAR_INDICES = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX", "INDIAVIX", "NIFTYNXT50"]
INDEX_ALIASES = {"NIFTY 50": "NIFTY", "NIFTY50": "NIFTY", "NIFTY BANK": "BANKNIFTY", "NIFTYBANK": "BANKNIFTY",
                 "NIFTY FIN SERVICE": "FINNIFTY", "NIFTY FINANCIAL SERVICES": "FINNIFTY",
                 "NIFTY MID SELECT": "MIDCPNIFTY", "NIFTY MIDCAP SELECT": "MIDCPNIFTY", "INDIA VIX": "INDIAVIX",
                 "NIFTY NEXT 50": "NIFTYNXT50"}


def _tick(v):
    """Dhan writes tick sizes in paise (5.0 = ₹0.05) in the compact file; accept rupees too."""
    try:
        v = float(v or 0)
    except ValueError:
        return 0.05
    if v <= 0:
        return 0.05
    return round(v / 100, 4) if v >= 0.5 else v


class Instruments:
    def __init__(self, cache_dir, url=MASTER_URL):
        self.cache = os.path.join(cache_dir, "scrip-master.csv")
        self.url = url
        self.by_key = {}                               # "SEG:ID" -> instrument dict
        self.items = []                                # search list
        self.opts = {}                                 # underlying -> {expiry: {(strike, CE/PE): key}}
        self.futs = {}                                 # underlying -> [(expiry, key)]
        self.under = {}                                # underlying name -> key of its index / NSE stock
        self.loaded_at = 0
        self.error = ""
        self._lock = threading.Lock()

    def load(self, force=False):
        with self._lock:
            os.makedirs(os.path.dirname(self.cache), exist_ok=True)
            fresh = os.path.exists(self.cache) and time.time() - os.path.getmtime(self.cache) < 20 * 3600
            if force or not fresh:
                try:
                    with urllib.request.urlopen(self.url, timeout=90) as r:
                        tmp = self.cache + ".tmp"
                        with open(tmp, "wb") as f:
                            f.write(r.read())
                        os.replace(tmp, self.cache)
                except Exception as e:                 # keep yesterday's list if the download fails
                    self.error = f"Couldn't download Dhan's instrument list: {e}"
                    if not os.path.exists(self.cache):
                        return False
            self._parse()
            self.loaded_at = time.time()
            self.error = ""
            return True

    def _parse(self):
        today = datetime.now(IST).strftime("%Y-%m-%d")
        by_key, items, opts, futs, under, eq_nse = {}, [], {}, {}, {}, {}
        with open(self.cache, encoding="utf-8", errors="replace", newline="") as f:
            for row in csv.DictReader(f):
                exch, segc = row.get("SEM_EXM_EXCH_ID", ""), row.get("SEM_SEGMENT", "")
                seg = SEG.get((exch, segc))
                kind = (row.get("SEM_INSTRUMENT_NAME") or "").strip()
                sid = (row.get("SEM_SMST_SECURITY_ID") or "").strip()
                sym = (row.get("SEM_TRADING_SYMBOL") or "").strip()
                if not (seg and sid and sym):
                    continue
                if kind == "EQUITY":
                    series = (row.get("SEM_SERIES") or "").strip()
                    if exch == "NSE" and series not in ("EQ", "BE", "BZ", "SM", "ST"):
                        continue
                elif kind == "INDEX":
                    pass
                elif kind in DERIV:
                    pass
                else:
                    continue
                expiry = (row.get("SEM_EXPIRY_DATE") or "")[:10]
                if kind in DERIV and (not expiry or expiry < today):
                    continue
                try:
                    lot = int(float(row.get("SEM_LOT_UNITS") or 1)) or 1
                except ValueError:
                    lot = 1
                try:
                    strike = float(row.get("SEM_STRIKE_PRICE") or 0)
                except ValueError:
                    strike = 0.0
                opt = (row.get("SEM_OPTION_TYPE") or "").strip()
                opt = opt if opt in ("CE", "PE") else ""
                name = (row.get("SM_SYMBOL_NAME") or "").strip()
                custom = (row.get("SEM_CUSTOM_SYMBOL") or "").strip()
                if kind == "INDEX":
                    sym = INDEX_ALIASES.get(sym.upper(), sym.upper().replace(" ", "") if sym.upper().replace(" ", "") in POPULAR_INDICES else sym)
                ul = sym.split("-")[0] if kind in DERIV else sym
                key = f"{seg}:{sid}"
                mult = contract = None
                if seg == "MCX_COMM":                  # Dhan counts MCX quantity in lots (see mcx.py)
                    lot, mult, contract = 1, mcx.multiplier(ul), mcx.describe(ul)
                it = {"mult": mult, "contract": contract, "key": key, "seg": seg, "id": sid, "kind": kind, "sym": sym, "exch": exch,
                      "name": custom or name or sym, "co": name, "lot": lot, "tick": _tick(row.get("SEM_TICK_SIZE")),
                      "expiry": expiry if kind in DERIV else "", "strike": strike if opt else 0, "opt": opt, "ul": ul}
                by_key[key] = it
                if kind in ("OPTIDX", "OPTSTK", "OPTFUT", "OPTCUR"):
                    opts.setdefault(ul, {}).setdefault(expiry, {})[(strike, opt)] = key
                elif kind in ("FUTIDX", "FUTSTK", "FUTCOM", "FUTCUR"):
                    futs.setdefault(ul, []).append((expiry, key))
                elif kind == "INDEX":
                    under.setdefault(sym, key)
                elif kind == "EQUITY" and exch == "NSE":
                    eq_nse[sym] = key
                items.append(it)
        for s, k in eq_nse.items():
            under.setdefault(s, k)
        for it in items:
            it["_s"] = " ".join((it["sym"], it["name"], it["co"])).lower()
            it["_r"] = KIND_RANK.get(it["kind"], 9)
        for v in futs.values():
            v.sort()
        self.by_key, self.items, self.opts, self.futs, self.under = by_key, items, opts, futs, under

    def get(self, key):
        return self.by_key.get(key)

    def search(self, q, limit=40):
        """Match every word of the query. "nifty 24000 ce", "reliance fut", "sbin"."""
        words = [w for w in q.lower().replace(",", " ").split() if w]
        if not words:
            return []
        want_kind = None
        rest = []
        for w in words:
            if w in ("fut", "futs", "future", "futures"):
                want_kind = "FUT"
            elif w in ("ce", "call", "pe", "put"):
                rest.append("call" if w in ("ce", "call") else "put")
            else:
                rest.append(w)
        out = []
        first = rest[0] if rest else ""
        for it in self.items:
            s = it["_s"]
            if want_kind == "FUT" and not it["kind"].startswith("FUT"):
                continue
            ok = True
            for w in rest:
                if w == "call":
                    ok = it["opt"] == "CE"
                elif w == "put":
                    ok = it["opt"] == "PE"
                elif w.replace(".", "").isdigit() and it["opt"]:
                    ok = abs(it["strike"] - float(w)) < 1e-6 or w in s
                else:
                    ok = w in s
                if not ok:
                    break
            if not ok:
                continue
            sym = it["sym"].lower()
            exact = 0 if sym == first or it["ul"].lower() == first else (1 if sym.startswith(first) else 2)
            out.append((exact, it["_r"], it["expiry"] or "", it["strike"], it["exch"] != "NSE", it["sym"], it))
        out.sort(key=lambda t: t[:6])
        return [public(t[-1]) for t in out[:limit]]

    def chain_info(self, ul, now=None):
        """Expiries of one underlying's options that can still be traded: an expiry is gone after 15:30 on
        its day (Dhan's option chain then answers "Invalid Expiry Date"), even before the list is reloaded."""
        t = now or datetime.now(IST)
        today = t.strftime("%Y-%m-%d")
        closed = (t.hour, t.minute) >= (15, 30)
        ex = self.opts.get(ul) or {}
        live = [e for e in sorted(ex) if e > today or (e == today and not closed)]
        return live or sorted(ex)[-1:]

    def chain_keys(self, ul, expiry):
        return (self.opts.get(ul) or {}).get(expiry) or {}

    def underlying(self, ul):
        """(seg, id) to ask Dhan's option chain for: the index (IDX_I) or the NSE stock (NSE_EQ)."""
        k = self.under.get(ul)
        if not k:
            return None
        it = self.by_key[k]
        return it["seg"], it["id"]

    def option_underlyings(self):
        idx = [u for u in POPULAR_INDICES if u in self.opts]
        stocks = sorted(u for u in self.opts if u not in idx and self.under.get(u, "").startswith("NSE_EQ"))
        others = sorted(u for u in self.opts if u not in idx and u not in stocks)
        return idx + stocks + others


def public(it):
    return {k: v for k, v in it.items() if not k.startswith("_")}


# ---- Zerodha (Kite Connect) -------------------------------------------------------------------------------------
KITE_MASTER_URL = "https://api.kite.trade/instruments"
KITE_SEG = {"NSE": "NSE_EQ", "BSE": "BSE_EQ", "NFO": "NSE_FNO", "BFO": "BSE_FNO", "CDS": "NSE_CURRENCY",
            "BCD": "BSE_CURRENCY", "MCX": "MCX_COMM"}
KITE_INDEX_UL = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50", "SENSEX", "BANKEX", "SENSEX50"}
EQ_SUFFIX = ("BE", "BZ", "SM", "ST")
MON = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


class KiteInstruments(Instruments):
    """The same lists from Zerodha's instrument file (api.kite.trade/instruments). The "security id" is Kite's
    instrument_token, so keys look like Dhan's ("NSE_EQ:738561") and the rest of the app doesn't notice. Each
    instrument also keeps Kite's own exchange and trading symbol ("kx", "ts") for orders and quotes."""

    def __init__(self, cache_dir, url=KITE_MASTER_URL):
        super().__init__(cache_dir, url)
        self.cache = os.path.join(cache_dir, "kite-instruments.csv")
        self.tok = {}                                  # token -> key
        self.by_ts = {}                                # (kite exchange, tradingsymbol) -> key

    def _parse(self):
        today = datetime.now(IST).strftime("%Y-%m-%d")
        by_key, items, opts, futs, under, eq_nse, tok, by_ts = {}, [], {}, {}, {}, {}, {}, {}
        with open(self.cache, encoding="utf-8", errors="replace", newline="") as f:
            for row in csv.DictReader(f):
                token = (row.get("instrument_token") or "").strip()
                ts = (row.get("tradingsymbol") or "").strip()
                kx = (row.get("exchange") or "").strip()
                segc = (row.get("segment") or "").strip()
                itype = (row.get("instrument_type") or "").strip()
                name = (row.get("name") or "").strip().strip('"')
                if not (token and ts and kx):
                    continue
                expiry = (row.get("expiry") or "")[:10]
                opt = itype if itype in ("CE", "PE") else ""
                if segc == "INDICES":
                    seg, kind = "IDX_I", "INDEX"
                elif kx in ("NSE", "BSE") and itype == "EQ":
                    base, _, suf = ts.partition("-")
                    if suf and suf not in EQ_SUFFIX:
                        continue                       # bonds, SGBs, rights and other non-equity series
                    seg, kind = KITE_SEG[kx], "EQUITY"
                elif kx in ("NFO", "BFO", "MCX", "CDS", "BCD") and itype in ("FUT", "CE", "PE"):
                    seg = KITE_SEG[kx]
                    fut = itype == "FUT"
                    if kx in ("NFO", "BFO"):
                        idx = name in KITE_INDEX_UL
                        kind = ("FUTIDX" if idx else "FUTSTK") if fut else ("OPTIDX" if idx else "OPTSTK")
                    elif kx == "MCX":
                        kind = "FUTCOM" if fut else "OPTFUT"
                    else:
                        kind = "FUTCUR" if fut else "OPTCUR"
                    if not expiry or expiry < today:
                        continue
                else:
                    continue
                try:
                    lot = int(float(row.get("lot_size") or 1)) or 1
                except ValueError:
                    lot = 1
                try:
                    strike = float(row.get("strike") or 0)
                except ValueError:
                    strike = 0.0
                try:
                    tick = float(row.get("tick_size") or 0.05) or 0.05
                except ValueError:
                    tick = 0.05
                if kind == "INDEX":
                    up = ts.upper()
                    sym = INDEX_ALIASES.get(up, up.replace(" ", "") if up.replace(" ", "") in POPULAR_INDICES else ts)
                    ul, disp, co = sym, ts, ts
                elif kind == "EQUITY":
                    sym, ul, co = ts, ts, name
                    disp = name.title() if name else ts
                else:
                    ul, sym, co = name, ts, name
                    try:
                        e = datetime.strptime(expiry, "%Y-%m-%d")
                        when = f"{e.day:02d} {MON[e.month - 1]}"
                    except ValueError:
                        when = expiry
                    if opt:
                        st = int(strike) if strike == int(strike) else strike
                        disp = f"{ul} {when} {st} {'CALL' if opt == 'CE' else 'PUT'}"
                    else:
                        disp = f"{ul} {when} FUT"
                key = f"{seg}:{token}"
                mult = contract = None
                if seg == "MCX_COMM":                  # quantity in lots, as on Dhan (see mcx.py)
                    lot, mult, contract = 1, mcx.multiplier(ul), mcx.describe(ul)
                exch = "BSE" if kx in ("BSE", "BFO", "BCD") else "MCX" if kx == "MCX" else "NSE"
                it = {"mult": mult, "contract": contract, "key": key, "seg": seg, "id": token, "kind": kind, "sym": sym,
                      "exch": exch, "name": disp, "co": co, "lot": lot, "tick": tick,
                      "expiry": expiry if kind in DERIV else "", "strike": strike if opt else 0, "opt": opt, "ul": ul,
                      "ts": ts, "kx": kx}
                by_key[key] = it
                tok[token] = key
                by_ts[(kx, ts)] = key
                if kind in ("OPTIDX", "OPTSTK", "OPTFUT", "OPTCUR"):
                    opts.setdefault(ul, {}).setdefault(expiry, {})[(strike, opt)] = key
                elif kind in ("FUTIDX", "FUTSTK", "FUTCOM", "FUTCUR"):
                    futs.setdefault(ul, []).append((expiry, key))
                elif kind == "INDEX":
                    under.setdefault(sym, key)
                elif kind == "EQUITY" and exch == "NSE":
                    eq_nse[sym] = key
                items.append(it)
        for s, k in eq_nse.items():
            under.setdefault(s, k)
        for it in items:
            it["_s"] = " ".join((it["sym"], it["name"], it["co"])).lower()
            it["_r"] = KIND_RANK.get(it["kind"], 9)
        for v in futs.values():
            v.sort()
        self.by_key, self.items, self.opts, self.futs, self.under, self.tok, self.by_ts = \
            by_key, items, opts, futs, under, tok, by_ts
