// HTF Key Level Breakout — the owner's Pine Script indicator (v6, "HTF-KLB"), ported line by line to run on the
// desk's own Dhan candles. Same inputs, same defaults, same order of checks on each bar:
//   key levels   = the previous COMPLETED higher-timeframe candle's high / low (request.security [1], lookahead on)
//   break        = a decisive candle (body > dispMult × ATR) that opens at/below the level + buffer and closes above it
//                  (mirror for shorts), once per level; optional retest within N bars
//   entry        = that close; stop = entry ∓ stopATR × ATR; target = entry ± HTF ATR × tgtATRMult
//   filters      = stop not tighter than minStopATR × ATR; R:R inside [rrMin, rrMax]
//   management   = peak, optional trail behind the peak (R), stop / target / max bars; fees taken off in R
// ATR is Wilder's (ta.atr = rma of the true range), like TradingView.
(function (root) {
  const DEFAULTS = {
    htf: 'auto', useLong: true, useShort: true,
    bufATR: 0.1, dispMult: 0.8, useRetest: false, retestBars: 10,
    stopATR: 1.5, minStopATR: 1.0, tgtATRMult: 1.0, rrMin: 7.0, rrMax: 9.0, trailR: 0.0, maxHold: 60, cooldown: 3,
    feePct: 0.0472,
  };
  // The script's own pairing: 1h chart -> D, 15m -> 4h, 5m -> 1h (and 1m -> 15m, Day -> Week).
  const AUTO_HTF = {1: '15', 5: '60', 15: '240', 60: 'D', 1440: 'W'};
  const SECONDS = {'15': 900, '60': 3600, '240': 14400, D: 86400, W: 604800};
  const SESSION = 9 * 3600 + 15 * 60;                     // NSE: higher-timeframe intraday candles count from 09:15 IST

  function htfFor(tfMin, htf) { return !htf || htf === 'auto' ? (AUTO_HTF[tfMin] || 'D') : String(htf); }

  function bucket(t, htf) {
    const ist = t + 19800, day = Math.floor(ist / 86400);
    if (htf === 'W') return day - ((day + 3) % 7);          // Monday of that week (1 Jan 1970 was a Thursday)
    if (htf === 'D') return day;
    return day * 1000 + Math.floor((ist % 86400 - SESSION) / (Number(htf) * 60));
  }

  function rma(vals, n) {                                  // ta.rma: SMA seed, then Wilder smoothing
    const out = new Array(vals.length).fill(null);
    let sum = 0, prev = null;
    for (let i = 0; i < vals.length; i++) {
      if (prev === null) {
        sum += vals[i];
        if (i === n - 1) prev = sum / n;
        else continue;
      } else prev = (vals[i] + (n - 1) * prev) / n;
      out[i] = prev;
    }
    return out;
  }
  function atr(bars, n = 14) {
    const tr = bars.map((b, i) => i === 0 ? b.h - b.l : Math.max(b.h - b.l, Math.abs(b.h - bars[i - 1].c), Math.abs(b.l - bars[i - 1].c)));
    return rma(tr, n);
  }

  // bars: closed candles [{t (epoch s), o, h, l, c}], oldest first; tfMin: the chart's minutes (Day = 1440).
  function compute(bars, tfMin, opts) {
    const o = Object.assign({}, DEFAULTS, opts || {});
    const htf = htfFor(tfMin, o.htf);
    const htfOK = (SECONDS[htf] || 0) > tfMin * 60;
    // higher-timeframe candles built from the same bars, and each bar's previous completed one
    const H = [], hIdx = [];
    let last = null;
    for (const b of bars) {
      const k = bucket(b.t, htf);
      if (k !== last) { H.push({t: b.t, o: b.o, h: b.h, l: b.l, c: b.c}); last = k; }
      else { const x = H[H.length - 1]; x.h = Math.max(x.h, b.h); x.l = Math.min(x.l, b.l); x.c = b.c; }
      hIdx.push(H.length - 1);
    }
    const hAtr = atr(H), A = atr(bars);
    const htfHigh = [], htfLow = [], htfAtrS = [];
    for (let i = 0; i < bars.length; i++) {
      const p = hIdx[i] - 1;
      htfHigh.push(p >= 0 ? H[p].h : null); htfLow.push(p >= 0 ? H[p].l : null); htfAtrS.push(p >= 0 ? hAtr[p] : null);
    }

    let lastHigh = null, lastLow = null, longDone = false, shortDone = false;
    let tDir = 0, entryP = null, curStop = null, tgtP = null, riskP = null, peakP = null, entryBar = null, cooldownTo = 0;
    let cur = null, pendDir = 0, pendBar = null;
    const trades = [], st = {wins: 0, losses: 0, sumR: 0, sumFeeR: 0, sumRR: 0, skipped: 0, warmup: 0};
    st.htfBars = H.length;

    for (let i = 0; i < bars.length; i++) {
      const b = bars[i], a = A[i], hh = htfHigh[i], hl = htfLow[i], ha = htfAtrS[i];
      if (hh !== lastHigh) { longDone = false; lastHigh = hh; }
      if (hl !== lastLow) { shortDone = false; lastLow = hl; }
      const inTrade = tDir !== 0;
      let fired = false;
      const decisive = o.dispMult === 0 || (a !== null && Math.abs(b.c - b.o) > a * o.dispMult);
      const buf = (a || 0) * o.bufATR;

      let hitDir = 0;
      if (htfOK && !inTrade && i >= cooldownTo && decisive && a !== null) {
        if (o.useLong && hh !== null && !longDone && b.c > hh + buf && b.o <= hh + buf) hitDir = 1;
        if (o.useShort && hl !== null && !shortDone && hitDir === 0 && b.c < hl - buf && b.o >= hl - buf) hitDir = -1;
      }
      if (hitDir !== 0 && o.useRetest && !inTrade) { pendDir = hitDir; pendBar = i; hitDir = 0; }
      if (o.useRetest && pendDir !== 0 && !inTrade) {
        const lvl = pendDir === 1 ? hh : hl;
        const back = pendDir === 1 ? b.l <= lvl : b.h >= lvl;
        if (back) { hitDir = pendDir; pendDir = 0; } else if (i - pendBar > o.retestBars) pendDir = 0;
      }

      if (hitDir !== 0 && !inTrade) {
        const pEntry = b.c, pStop = pEntry - hitDir * a * o.stopATR, pRisk = Math.abs(pEntry - pStop);
        const pTgt = pEntry + hitDir * ((ha || 0) * o.tgtATRMult);
        const pRR = pRisk > 0 ? Math.abs(pTgt - pEntry) / pRisk : 0;
        const pFeeR = pRisk > 0 ? (2 * o.feePct / 100 * pEntry) / pRisk : 999;
        const tooTight = o.minStopATR > 0 && a > 0 && pRisk < a * o.minStopATR;
        const outOfBand = pRR < o.rrMin || (o.rrMax > 0 && pRR > o.rrMax);
        if (ha === null) st.warmup++;                  // not enough higher-timeframe candles yet for its ATR
        else if (pRisk <= 0 || tooTight || outOfBand) st.skipped++;
        else {
          tDir = hitDir; entryP = pEntry; curStop = pStop; tgtP = pTgt; riskP = pRisk; peakP = pEntry; entryBar = i;
          st.sumRR += pRR; fired = true;
          if (hitDir === 1) longDone = true; else shortDone = true;
          cur = {dir: hitDir, i, t: b.t, entry: pEntry, stop0: pStop, stop: pStop, tgt: pTgt, risk: pRisk, rr: pRR, feeR: pFeeR, level: hitDir === 1 ? hh : hl};
          trades.push(cur);
        }
      }

      if (inTrade && !fired) {
        peakP = tDir === 1 ? Math.max(peakP, b.h) : Math.min(peakP, b.l);
        const achieved = riskP > 0 ? (tDir === 1 ? peakP - entryP : entryP - peakP) / riskP : 0;
        if (o.trailR > 0 && achieved >= o.trailR) {
          const want = peakP - tDir * o.trailR * riskP;
          if (tDir === 1 ? want > curStop : want < curStop) curStop = want;
        }
        cur.stop = curStop;
        const hitStop = tDir === 1 ? b.l <= curStop : b.h >= curStop;
        const hitTgt = tDir === 1 ? b.h >= tgtP : b.l <= tgtP;
        const stale = i - entryBar > o.maxHold;
        if (hitStop || hitTgt || stale) {
          const exitP = hitStop ? curStop : hitTgt ? tgtP : b.c;
          const rawR = riskP > 0 ? (tDir === 1 ? exitP - entryP : entryP - exitP) / riskP : 0;
          const feeR = riskP > 0 ? (2 * o.feePct / 100 * entryP) / riskP : 0;
          const r = rawR - feeR;
          st.sumR += r; st.sumFeeR += feeR;
          if (r > 0) st.wins++; else st.losses++;
          Object.assign(cur, {exitI: i, exitT: b.t, exitP, r, status: hitStop ? (rawR > 0 ? 'TRAILED' : 'STOPPED') : hitTgt ? 'TARGET' : 'TIMED OUT'});
          tDir = 0; cooldownTo = i + o.cooldown; entryP = curStop = tgtP = riskP = null; cur = null;
        }
      }
    }
    return {htf, htfOK, htfHigh, htfLow, trades, open: cur, stats: st, opts: o};
  }

  const api = {compute, htfFor, DEFAULTS, AUTO_HTF, _atr: atr, _bucket: bucket};
  if (typeof module !== 'undefined' && module.exports) module.exports = api; else root.HTFKLB = api;
})(typeof window !== 'undefined' ? window : this);
