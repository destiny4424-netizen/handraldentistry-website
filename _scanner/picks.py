"""Top picks: run every scanned stock through a funnel and keep the best 5-6
trades with entry, stop-loss and targets.

    Scanned -> Setup (order block / HTF breakout) -> Sector agrees
            -> Good risk:reward -> OI confirms -> Top picks
"""
from analysis import _clamp, _day

SECTORS = {
    "Banks": "HDFCBANK ICICIBANK SBIN KOTAKBANK AXISBANK INDUSINDBK BANKBARODA PNB CANBK FEDERALBNK "
             "IDFCFIRSTB AUBANK BANDHANBNK RBLBANK YESBANK UNIONBANK BANKINDIA INDIANB",
    "Finance": "BAJFINANCE BAJAJFINSV CHOLAFIN SHRIRAMFIN MUTHOOTFIN MANAPPURAM LICHSGFIN PFC RECLTD IRFC "
               "POONAWALLA ABCAPITAL LTF IIFL SBICARD HDFCAMC SBILIFE HDFCLIFE ICICIGI ICICIPRULI LICI BSE "
               "CDSL MCX CAMS ANGELONE JIOFIN PAYTM POLICYBZR NUVAMA KFINTECH 360ONE HUDCO IREDA PNBHOUSING "
               "BAJAJHLDNG M&MFIN",
    "IT": "TCS INFY HCLTECH WIPRO TECHM LTIM MPHASIS PERSISTENT COFORGE OFSS KPITTECH TATAELXSI LTTS "
          "CYIENT TATATECH",
    "Auto": "MARUTI TATAMOTORS TMPV TMCV M&M BAJAJ-AUTO HEROMOTOCO EICHERMOT TVSMOTOR ASHOKLEY BHARATFORG "
            "MOTHERSON BOSCHLTD EXIDEIND TIINDIA SONACOMS UNOMINDA MRF APOLLOTYRE BALKRISIND",
    "Pharma": "SUNPHARMA DRREDDY CIPLA DIVISLAB LUPIN AUROPHARMA ZYDUSLIFE TORNTPHARM ALKEM BIOCON "
              "GLENMARK MANKIND LAURUSLABS APOLLOHOSP MAXHEALTH FORTIS SYNGENE PPLPHARMA GRANULES IPCALAB",
    "FMCG": "HINDUNILVR ITC NESTLEIND BRITANNIA DABUR MARICO GODREJCP COLPAL TATACONSUM VBL UNITDSPR "
            "PATANJALI",
    "Metals": "TATASTEEL JSWSTEEL HINDALCO VEDL SAIL JINDALSTEL NMDC NATIONALUM HINDZINC COALINDIA",
    "Oil & Gas": "RELIANCE ONGC BPCL IOC HINDPETRO GAIL PETRONET OIL IGL MGL",
    "Power": "NTPC POWERGRID TATAPOWER ADANIPOWER ADANIGREEN ADANIENSOL NHPC JSWENERGY TORNTPOWER CESC "
             "SUZLON INOXWIND IEX SJVN",
    "Capital Goods": "LT BEL HAL BHEL SIEMENS ABB CGPOWER CUMMINSIND POLYCAB KEI HAVELLS BDL MAZDOCK "
                     "SOLARINDS RVNL NBCC GMRAIRPORT ADANIPORTS CONCOR KAYNES DIXON AMBER PGEL BLUESTARCO "
                     "VOLTAS CROMPTON APLAPOLLO ASTRAL SUPREMEIND ADANIENT",
    "Cement & Chem": "ULTRACEMCO SHREECEM AMBUJACEM DALBHARAT ACC GRASIM PIDILITIND ASIANPAINT BERGEPAINT "
                     "UPL PIIND SRF DEEPAKNTR TATACHEM CHAMBLFERT COROMANDEL",
    "Realty": "DLF GODREJPROP OBEROIRLTY PRESTIGE LODHA PHOENIXLTD",
    "Consumer": "TITAN TRENT DMART KALYANKJIL PAGEIND JUBLFOOD NYKAA ETERNAL SWIGGY INDHOTEL IRCTC "
                "INDIGO NAUKRI",
    "Telecom": "BHARTIARTL IDEA INDUSTOWERS TATACOMM HFCL",
}
SECTOR_OF = {s: sec for sec, names in SECTORS.items() for s in names.split()}
STAGES = ["Scanned", "Strong setup", "Sector agrees", "Good R:R", "OI confirms", "Top picks"]


def sector_of(symbol):
    return SECTOR_OF.get(symbol, "Others")


def oi_build(fut):
    """Open-interest build-up from today's futures candles vs the previous day's close."""
    if not fut or "oi" not in fut[-1]:
        return None
    today = _day(fut[-1]["t"])
    prev = [c for c in fut if _day(c["t"]) < today]
    if not prev or not prev[-1].get("oi"):
        return None
    p0, oi0, p1, oi1 = prev[-1]["c"], prev[-1]["oi"], fut[-1]["c"], fut[-1]["oi"]
    dp, doi = (p1 - p0) / p0 * 100, (oi1 - oi0) / oi0 * 100
    if dp > 0:
        kind = "LONG BUILD-UP" if doi > 0 else "SHORT COVERING"
    else:
        kind = "SHORT BUILD-UP" if doi > 0 else "LONG UNWINDING"
    return {"kind": kind, "oi_pct": round(doi, 2), "price_pct": round(dp, 2)}


def sector_table(pcts):
    """pcts: {symbol: % change today}. Average move of each sector, strongest first."""
    agg = {}
    for sym, p in pcts.items():
        a = agg.setdefault(sector_of(sym), [])
        a.append(p)
    rows = [{"name": name, "pct": round(sum(v) / len(v), 2), "n": len(v),
             "up": sum(1 for x in v if x > 0), "down": sum(1 for x in v if x <= 0)}
            for name, v in agg.items()]
    rows.sort(key=lambda r: -r["pct"])
    for i, r in enumerate(rows):
        r["rank"] = i + 1
    return rows


def trade_plan(side, price, atr, ob, bo, htf_levels, min_rr):
    """Entry, stop and targets from the order block and/or the broken HTF level.
    Returns None when there is no valid plan (stop hit, target already reached,
    or not enough room to the next level)."""
    entries = []
    if ob:
        if side == "bull":
            entries.append(("Order block", ob["top"], ob["bottom"] - 0.1 * atr, (ob["bottom"], ob["top"])))
        else:
            entries.append(("Order block", ob["bottom"], ob["top"] + 0.1 * atr, (ob["bottom"], ob["top"])))
    if bo:
        L = bo["level_price"]
        entries.append((f'{bo["level"]} breakout', L, bo["stop"], (min(L, bo["stop"]), max(L, bo["stop"]))))
    if not entries:
        return None
    # Use the entry nearest to the current price (the one most likely to fill).
    name, entry, sl, zone = min(entries, key=lambda e: abs(price - e[1]))
    sign = 1 if side == "bull" else -1
    risk = (entry - sl) * sign
    if risk <= 0.05 * atr:
        sl = entry - sign * 0.5 * atr                  # zone too thin: give the stop some room
        risk = 0.5 * atr
    if (price - sl) * sign <= 0:
        return None                                     # already through the stop

    levels = [l["price"] for l in htf_levels]
    if ob:
        levels += [v for v in (ob.get("resistance"), ob.get("support"), ob.get("liquidity")) if v]
    ahead = sorted({round(v, 2) for v in levels if (v - entry) * sign > 0.5 * risk}, key=lambda v: (v - entry) * sign)
    r1 = ahead[0] if ahead else None
    r2 = ahead[1] if len(ahead) > 1 else None
    t1 = r1 if r1 is not None else entry + sign * 2 * risk
    t2 = r2 if r2 is not None else entry + sign * 3 * risk
    if (t2 - t1) * sign <= 0:
        t2 = t1 + sign * risk
    rr = (t1 - entry) * sign / risk
    if rr < min_rr or (price - t1) * sign >= 0:
        return None
    if (price - entry) * sign > 1.0 * risk:
        return None                                     # already ran more than 1R: missed it
    status = "At entry" if (price - entry) * sign <= 0.3 * risk else "Wait for pullback"
    return {"basis": name, "entry": entry, "zone": zone, "sl": sl, "t1": t1, "t2": t2,
            "r1_level": r1 is not None, "rr": round(rr, 2), "rr2": round((t2 - entry) * sign / risk, 2),
            "risk_pct": round(risk / entry * 100, 2), "status": status}


def build(stocks, min_rr=1.5, n_picks=6, min_setup=55):
    """stocks: {symbol: {"res": order-block analysis, "bo": breakout setup or None,
    "levels": HTF levels, "oi": oi_build() result or None (filled in later)}}.
    Returns (candidates needing OI, finish function)."""
    pcts = {s: d["res"]["pct"] for s, d in stocks.items()}
    sectors = sector_table(pcts)
    sec = {r["name"]: r for r in sectors}
    half = max(1, len(sectors) // 2)
    market = sum(pcts.values()) / len(pcts) if pcts else 0

    flow, cands = {}, []
    for sym, d in stocks.items():
        res, bo = d["res"], d["bo"]
        options = []
        for side in ("bull", "bear"):
            ob = res.get(side)
            b = bo if bo and bo["side"] == side else None
            if ob or b:
                base = max(ob["score"] if ob else 0, b["score"] if b else 0)
                options.append((base + (10 if ob and b else 0), side, ob, b))
        if not options:
            flow[sym] = {"symbol": sym, "pct": res["pct"], "side": "bull" if res["pct"] >= 0 else "bear", "stage": 0}
            continue
        base, side, ob, b = max(options, key=lambda o: o[0])
        row = {"symbol": sym, "pct": res["pct"], "side": side, "stage": 0}
        flow[sym] = row
        if base < min_setup:                           # weak order block / breakout
            continue
        row["stage"] = 1
        s = sec[sector_of(sym)]
        if sector_of(sym) == "Others":
            agrees = (market > 0) == (side == "bull")
        else:
            agrees = (s["pct"] > 0 and s["rank"] <= half) if side == "bull" else (s["pct"] < 0 and s["rank"] > len(sectors) - half)
        if not agrees:
            continue
        row["stage"] = 2
        plan = trade_plan(side, res["price"], res["atr"], ob, b, d["levels"], min_rr)
        if not plan:
            continue
        row["stage"] = 3
        sources = (["Order block"] if ob else []) + ([f'{b["level"]} breakout'] if b else [])
        cands.append({"symbol": sym, "side": side, "base": base, "sector": sector_of(sym),
                      "sector_pct": s["pct"], "sector_rank": s["rank"], "price": res["price"],
                      "pct": res["pct"], "plan": plan, "sources": sources})
    cands.sort(key=lambda c: -c["base"])

    def finish(oi_by_symbol):
        strong, weak = [], []
        for c in cands:
            oi = oi_by_symbol.get(c["symbol"])
            c["oi"] = oi
            want = "LONG BUILD-UP" if c["side"] == "bull" else "SHORT BUILD-UP"
            soft = "SHORT COVERING" if c["side"] == "bull" else "LONG UNWINDING"
            oi_pts = 0
            if oi and oi["kind"] == want:
                oi_pts, bucket = 20, strong
            elif oi is None or oi["kind"] == soft:     # no OI data, or a weaker confirmation
                oi_pts, bucket = 8, weak
            else:
                continue                               # OI contradicts the trade
            sector_pts = 20 * (1 - (c["sector_rank"] - 1) / max(1, len(sectors) - 1))
            if c["side"] == "bear":
                sector_pts = 20 - sector_pts
            c["score"] = round(0.5 * c["base"] + sector_pts + oi_pts + 10 * _clamp((c["plan"]["rr"] - 1) / 2), 1)
            bucket.append(c)
            if bucket is strong:
                flow[c["symbol"]]["stage"] = 4
        strong.sort(key=lambda c: -c["score"])
        weak.sort(key=lambda c: -c["score"])
        picks = (strong + weak)[:n_picks]              # OI-confirmed first, then the rest if needed
        picks.sort(key=lambda c: -c["score"])
        for c in picks:
            flow[c["symbol"]]["stage"] = 5
        counts = [sum(1 for f in flow.values() if f["stage"] >= i) for i in range(len(STAGES))]
        return {"funnel": [{"name": n, "count": counts[i]} for i, n in enumerate(STAGES)],
                "flow": sorted(flow.values(), key=lambda f: -f["pct"]),
                "sectors": sectors, "picks": picks}

    return cands, finish
