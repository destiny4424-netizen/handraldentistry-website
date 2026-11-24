"""MCX contract sizes. On MCX, Dhan counts the quantity in lots, and a contract's price is quoted for a smaller
unit than the lot (SILVER100: price per 10 g, lot 100 g). So a ₹1 price move is worth more than ₹1 per lot:
that is the multiplier here (₹ per lot for a ₹1 move).

Dhan's positions give closed MCX P&L with the multiplier applied, but the open (unrealized) P&L per unit:
a SILVER100 position came through 10 times smaller than Dhan's own app showed it. fix_positions() corrects that. Contracts not in the table can't be ordered from the app (the size isn't known).
"""

# underlying: (what one lot is, ₹ per lot for a ₹1 move in the quoted price)
SPEC = {
    "GOLD": ("1 kg (price per 10 g)", 100), "GOLDM": ("100 g (price per 10 g)", 10),
    "GOLDGUINEA": ("8 g (price per 8 g)", 1), "GOLDPETAL": ("1 g (price per 1 g)", 1),
    "GOLDTEN": ("10 g (price per 10 g)", 1),
    "SILVER": ("30 kg (price per kg)", 30), "SILVERM": ("5 kg (price per kg)", 5),
    "SILVERMIC": ("1 kg (price per kg)", 1), "SILVER100": ("100 g (price per 10 g)", 10),
    "CRUDEOIL": ("100 barrels (price per barrel)", 100), "CRUDEOILM": ("10 barrels (price per barrel)", 10),
    "NATURALGAS": ("1,250 mmBtu (price per mmBtu)", 1250), "NATGASMINI": ("250 mmBtu (price per mmBtu)", 250),
    "COPPER": ("2,500 kg (price per kg)", 2500), "ZINC": ("5,000 kg (price per kg)", 5000),
    "ZINCMINI": ("1,000 kg (price per kg)", 1000), "LEAD": ("5,000 kg (price per kg)", 5000),
    "LEADMINI": ("1,000 kg (price per kg)", 1000), "ALUMINIUM": ("5,000 kg (price per kg)", 5000),
    "ALUMINI": ("1,000 kg (price per kg)", 1000),
}


def underlying(sym):
    """'SILVER100-Dec2026-FUT' or 'SILVER100 DEC FUT' -> 'SILVER100'."""
    return str(sym or "").replace(" ", "-").split("-")[0].upper()


def multiplier(sym):
    s = SPEC.get(underlying(sym))
    return s[1] if s else None


def describe(sym):
    s = SPEC.get(underlying(sym))
    return s[0] if s else None


def fix_positions(pos, quotes=None):
    """Put the multiplier into the open P&L of MCX positions (Dhan's API sends it per unit).
    Checked against the live price where there is one, so it is never applied twice."""
    quotes = quotes or {}
    for p in pos:
        if p.get("exchangeSegment") != "MCX_COMM" or not int(p.get("netQty") or 0):
            continue
        m = multiplier(p.get("tradingSymbol")) or float(p.get("multiplier") or 0) or 1
        if m <= 1:
            continue
        net = int(p["netQty"])
        unreal = float(p.get("unrealizedProfit") or 0)
        cost = float(p.get("buyAvg") or 0) if net > 0 else float(p.get("sellAvg") or 0)
        ltp = float((quotes.get(f"MCX_COMM:{p.get('securityId')}") or {}).get("ltp") or 0)
        if ltp and cost:
            per_unit = net * (ltp - cost)
            if per_unit and abs(unreal - per_unit * m) < abs(unreal - per_unit):
                p["_mult"] = m                        # already includes it
                continue
        p["unrealizedProfit"] = round(unreal * m, 2)
        p["_mult"] = m
        p["_mcx_fixed"] = True
    return pos
