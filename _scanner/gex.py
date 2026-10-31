"""NIFTY dealer gamma (GEX) and delta (DEX) exposure from the option chain.

Option sellers (dealers) hedge their gamma. The usual assumption: they are long the calls the public
sells and short the puts the public buys, so call gamma counts +, put gamma counts −.
    - Net GEX positive (and NIFTY above the gamma flip): dealers buy dips and sell rips -> the day tends
      to stay in a range and get pinned near big strikes.
    - Net GEX negative (or NIFTY below the flip): dealers chase the move -> bigger, trending moves.
    - Call wall / put wall: the strikes with the most call / put gamma, where moves often stall.
This is a well-known way of reading positioning, not a guarantee: the scanner records it with every trade
so the Learning journal can show whether it actually helps.
"""
import math


def _gamma(s, k, days, iv):
    """Black-Scholes gamma of one option."""
    t, v = max(days, 0.25) / 365, max(iv, 1.0) / 100
    d1 = (math.log(s / k) + (0.065 + v * v / 2) * t) / (v * math.sqrt(t))
    return math.exp(-d1 * d1 / 2) / math.sqrt(2 * math.pi) / (s * v * math.sqrt(t))


def analyse(chain, spot, lot=1, days=5):
    """chain: Dhan option chain data (data.oc). Returns GEX/DEX by strike, totals, flip and walls."""
    oc = (chain or {}).get("oc") or {}
    rows = []
    for k, row in oc.items():
        try:
            strike = float(k)
        except ValueError:
            continue
        ce, pe = (row or {}).get("ce") or {}, (row or {}).get("pe") or {}
        gc, gp = (ce.get("greeks") or {}).get("gamma") or 0, (pe.get("greeks") or {}).get("gamma") or 0
        dc, dp = (ce.get("greeks") or {}).get("delta") or 0, (pe.get("greeks") or {}).get("delta") or 0
        oic, oip = (ce.get("oi") or 0) * lot, (pe.get("oi") or 0) * lot
        unit = spot * spot * 0.01 / 1e7                # ₹ crore of hedging per 1% move
        rows.append({"strike": strike, "call_gex": gc * oic * unit, "put_gex": -gp * oip * unit,
                     "dex": (dc * oic + dp * oip) * spot / 1e7, "call_oi": oic, "put_oi": oip,
                     "iv_c": ce.get("implied_volatility") or 0, "iv_p": pe.get("implied_volatility") or 0})
    if not rows:
        return None
    rows.sort(key=lambda r: r["strike"])
    for r in rows:
        r["net"] = r["call_gex"] + r["put_gex"]
    near = [r for r in rows if abs(r["strike"] - spot) <= spot * 0.05]   # ±5% of spot
    total = sum(r["net"] for r in rows)
    # Gamma flip: the NIFTY level where total dealer gamma changes sign. Total gamma is recomputed at
    # hypothetical NIFTY levels (±5%) with each option's own implied volatility.
    ivs = [r["iv_c"] for r in rows if r["iv_c"]] + [r["iv_p"] for r in rows if r["iv_p"]]
    iv0 = sorted(ivs)[len(ivs) // 2] if ivs else 14.0

    def total_at(x):
        return sum(_gamma(x, r["strike"], days, r["iv_c"] or iv0) * r["call_oi"]
                   - _gamma(x, r["strike"], days, r["iv_p"] or iv0) * r["put_oi"] for r in rows)
    levels = [spot * (1 + i / 400) for i in range(-20, 21)]      # 0.25% steps
    prof = [(x, total_at(x)) for x in levels]
    flips = [a[0] + (b[0] - a[0]) * (-a[1]) / ((b[1] - a[1]) or 1) for a, b in zip(prof, prof[1:])
             if (a[1] < 0) != (b[1] < 0)]
    flip = round(min(flips, key=lambda f: abs(f - spot)), 2) if flips else None
    total_now = total_at(spot)
    up = [r for r in rows if r["strike"] >= spot] or rows       # resistance above, support below
    down = [r for r in rows if r["strike"] <= spot] or rows
    call_wall = max(up, key=lambda r: r["call_gex"])["strike"]
    put_wall = min(down, key=lambda r: r["put_gex"])["strike"]
    positive = total_now > 0
    regime = "pos" if positive else "neg"
    note = ("Positive gamma: dealers dampen moves. Expect a range and pinning near the big strikes; "
            "be choosy with breakouts and book profits sooner." if positive else
            "Negative gamma: dealers add to moves. Expect bigger, trending moves; breakouts work better, "
            "but use the stop.")
    return {"spot": round(spot, 2), "net_gex": round(total, 1), "dex": round(sum(r["dex"] for r in rows), 1),
            "flip": flip, "positive_above": bool(flips) and total_at(spot * 1.01) > 0, "call_wall": call_wall, "put_wall": put_wall, "regime": regime, "note": note,
            "strikes": [{k: (round(v, 2) if isinstance(v, float) else v) for k, v in r.items()
                         if k in ("strike", "call_gex", "put_gex", "net")} for r in near]}


def demo_chain(spot, days=5, iv=13.0, seed=1):
    """Made-up NIFTY option chain (Black-Scholes greeks, bell-shaped OI) for demo mode."""
    import random
    from options import bs
    rng = random.Random(seed)
    oc = {}
    atm = round(spot / 50) * 50
    for i in range(-20, 21):
        k = atm + 50 * i
        ce_ltp, ce_d, g = bs(spot, k, days, iv, "CE")
        pe_ltp, pe_d, _ = bs(spot, k, days, iv, "PE")
        base = 1e6 * math.exp(-((k - atm) / 600) ** 2)
        oc[f"{k:.6f}"] = {"ce": {"oi": int(base * rng.uniform(0.6, 1.6) * (1.6 if i > 0 else 1)), "implied_volatility": iv,
                                 "greeks": {"gamma": g, "delta": ce_d}, "last_price": ce_ltp},
                          "pe": {"oi": int(base * rng.uniform(0.6, 1.6) * (1.6 if i < 0 else 1)), "implied_volatility": iv,
                                 "greeks": {"gamma": g, "delta": pe_d}, "last_price": pe_ltp}}
    return {"oc": oc, "last_price": spot}
