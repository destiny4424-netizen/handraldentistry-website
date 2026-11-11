"""WhatsApp alerts through CallMeBot (free, for messages to your own number).

Setup: save +34 694 29 84 96 in your phone, send it "I allow callmebot to send me messages" on WhatsApp,
and it replies with your API key. Put your number and the key in the scanner's Settings (they stay in the
settings file on the server). Messages go out one at a time from a background thread, so a slow or failed
send never holds up a scan.
"""
import os
import queue
import threading
import time
import urllib.parse
import urllib.request

URL = os.environ.get("OBS_WA_URL") or "https://api.callmebot.com/whatsapp.php"
GAP = 4                                                # seconds between messages (CallMeBot asks for spacing)
_Q = queue.Queue()
LOG = []                                               # last few sends: (time, ok, text or error)


def clean_phone(p):
    """'+91 98765 43210' -> '+919876543210' (None if it doesn't look like a phone number)."""
    p = "".join(ch for ch in str(p or "") if ch.isdigit() or ch == "+")
    if p and not p.startswith("+"):
        p = "+91" + p[-10:] if len(p) == 10 else "+" + p
    return p if 11 <= len(p) <= 16 and p[1:].isdigit() else None


def send_now(phone, key, text, timeout=20):
    """One message, right now. Returns (ok, detail)."""
    q = urllib.parse.urlencode({"phone": phone, "text": text, "apikey": key})
    try:
        with urllib.request.urlopen(urllib.request.Request(f"{URL}?{q}", headers={"User-Agent": "scanner-alerts"}),
                                    timeout=timeout) as r:
            body = r.read(2000).decode("utf-8", "replace")
    except Exception as e:
        return False, f"couldn't reach CallMeBot: {e}"
    low = body.lower()
    if "queued" in low or "sent" in low or "success" in low:
        return True, "sent"
    if "apikey" in low and ("invalid" in low or "wrong" in low or "not" in low):
        return False, "CallMeBot says the API key is wrong for this number"
    return False, "CallMeBot answered: " + " ".join(body.replace("<", " <").split())[:160]


def _worker(get_cfg):
    while True:
        text = _Q.get()
        cfg = get_cfg()
        if cfg:
            ok, detail = False, ""
            for attempt in range(3):
                ok, detail = send_now(cfg[0], cfg[1], text)
                if ok or "key is wrong" in detail:
                    break
                time.sleep(5 * (attempt + 1))
            LOG.append((time.time(), ok, text[:60] if ok else detail))
            del LOG[:-20]
        time.sleep(GAP)


def start(get_cfg):
    """get_cfg() -> (phone, key) when alerts are on, else None."""
    threading.Thread(target=_worker, args=(get_cfg,), daemon=True).start()


def send(text):
    _Q.put(text)


# ── Bank Nifty option trades → messages ───────────────────────────────────────────────────
def _n(v):
    return f"{v:,.0f}" if v is not None else "—"


def bnf_new(row, pick):
    """The 3 PM setup, locked in."""
    o, p = pick.get("option") or {}, pick["plan"]
    bull = pick["side"] == "bull"
    opt = (f"\nOption: BUY {o['label']} ~{o['entry']:.0f} · SL {o['sl']:.0f} · T1 {o['t1']:.0f} · T2 {o['t2']:.0f}"
           f"\n1 lot ({o.get('lot')}) risks ₹{o.get('risk_lot', 0):,}") if o else ""
    return (f"🏦 BANK NIFTY {'CALL' if bull else 'PUT'} setup confirmed ({p['basis'].split('· ')[-1]}, score {pick['score']:.0f})"
            f"\nEnter when BNF {'breaks above' if bull else 'breaks below'} {_n(p['entry'])} (next 2 sessions, after 9:30)"
            f"\nBNF SL {_n(p['sl'])} · T1 {_n(p['t1'])} (book half, SL to cost) · T2 {_n(p['t2'])}{opt}"
            + (f"\n⚠ {pick['caution']}" if pick.get("caution") else ""))


def stages(row):
    """Which stages a trade has reached: trig, t1, end."""
    return [k for k, v in (("trig", row.get("triggered_at")), ("t1", row.get("t1_at")), ("end", row.get("exit_at"))) if v]


def bnf_alerts(row):
    """Messages for the stages this Bank Nifty trade reached since the last alert (marks them sent)."""
    sent, out = row.setdefault("wa", []), []
    st = row.get("status") or ""
    opt = row.get("option") or ("CE" if row["side"] == "bull" else "PE")
    res = row.get("result_r")
    tail = f" · result {res:+.2f}R" if res is not None else ""
    for k in stages(row):
        if k in sent:
            continue
        sent.append(k)
        if k == "trig":
            out.append(f"🟢 BANK NIFTY entry triggered: BUY {opt}. BNF crossed {_n(row['entry'])}. "
                       f"SL {_n(row['sl'])} · T1 {_n(row['t1'])} · T2 {_n(row['t2'])}")
        elif k == "t1":
            out.append(f"🎯 BANK NIFTY {opt}: T1 {_n(row['t1'])} hit. Book half, move the stop to cost ({_n(row['entry'])}).")
        elif st.startswith("T2 hit"):
            out.append(f"✅ BANK NIFTY {opt}: T2 {_n(row['t2'])} hit. Exit the rest{tail}")
        elif st.startswith("Stopped at cost"):
            out.append(f"⚪ BANK NIFTY {opt}: back to cost after T1. Exit the rest{tail}")
        elif st.startswith("SL hit"):
            out.append(f"🔴 BANK NIFTY {opt}: stop {_n(row['sl'])} hit. Exit{tail}")
        elif st.startswith("Time stop"):
            out.append(f"⏱ BANK NIFTY {opt}: 6 sessions are up. Exit at market{tail}")
        elif st.startswith("Not triggered"):
            out.append(f"➖ BANK NIFTY {opt}: entry {_n(row['entry'])} didn't trigger in 2 sessions. Setup cancelled.")
    return out
