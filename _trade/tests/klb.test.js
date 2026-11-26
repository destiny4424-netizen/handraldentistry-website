// The HTF Key Level Breakout port (static/htfklb.js) on made-up candles. Run: node tests/klb.test.js
// (tests/test_klb.py runs it with the Python tests.)
const K = require('../static/htfklb.js');
let fails = 0;
const ok = (c, m) => { console.log((c ? '  ok   ' : '  FAIL ') + m); if (!c) fails++; };

const DAY0 = Date.UTC(2026, 10, 16) / 1000 - 19800;      // Mon 16 Nov 2026, 00:00 IST
// 5-minute candles 09:15–15:30 for `days` days, quiet around `base`; `events` [day, bar, {o,h,l,c}] override bars
function bars(days, base, events = []) {
  const out = [];
  for (let d = 0; d < days; d++) {
    for (let i = 0; i < 75; i++) {
      const t = DAY0 + d * 86400 + 9 * 3600 + 15 * 60 + i * 300;
      const w = (i % 2 ? 0.2 : -0.2);
      let b = {t, o: base - w, h: base + 0.5, l: base - 0.5, c: base + w};
      const ev = events.find(e => e[0] === d && e[1] === i);
      if (ev) b = Object.assign({t}, ev[2]);
      out.push(b);
    }
  }
  return out;
}

// ATR (Wilder): constant true range 1.0 -> ATR 1.0
const flat = bars(1, 100);
const a = K._atr(flat);
ok(a[12] === null && Math.abs(a[13] - 1.0) < 1e-9 && Math.abs(a[74] - 1.0) < 1e-9, 'ATR is Wilder\'s: first value at bar 14, flat range 1.0 -> 1.0');

// HTF for a 5-minute chart is 1 hour (09:15-10:15 …), as in the script's pairing
ok(K.htfFor(5, 'auto') === '60' && K.htfFor(60, 'auto') === 'D' && K.htfFor(15, 'auto') === '240', 'auto HTF: 5m->1h, 15m->4h, 1h->Day');
ok(K._bucket(DAY0 + 10 * 3600 + 14 * 60, '60') === K._bucket(DAY0 + 9 * 3600 + 15 * 60, '60') &&
   K._bucket(DAY0 + 10 * 3600 + 15 * 60, '60') !== K._bucket(DAY0 + 9 * 3600 + 15 * 60, '60'), '1h candles run 09:15–10:15, 10:15–11:15 …');

// Long: day 1, bar 30 breaks the previous hour's high (100.5) with a big body; later bars run to the target
const longBars = bars(4, 100, [[3, 30, {o: 100.4, h: 103.1, l: 100.3, c: 103}],
  ...Array.from({length: 10}, (_, k) => [3, 31 + k, {o: 103 + k * 0.4, h: 103.6 + k * 0.4, l: 102.8 + k * 0.4, c: 103.4 + k * 0.4}])]);
let r = K.compute(longBars, 5, {rrMin: 0, rrMax: 0});
let t = r.trades[0];
ok(r.htf === '60' && r.htfOK, 'chart 5m, key levels from 1h');
ok(r.trades.length === 1 && t.dir === 1 && t.entry === 103 && Math.abs(t.level - 100.5) < 1e-9, 'long on the close that breaks the previous 1h high (entry 103, level 100.5)');
ok(t.stop0 < t.entry && t.tgt > t.entry && Math.abs(t.entry - t.stop0 - 1.5 * K._atr(longBars)[t.i]) < 1e-9, 'stop = entry − 1.5 × ATR, target above');
ok(t.status === 'TARGET' && t.exitP === t.tgt && Math.abs(t.r - (t.rr - t.feeR)) < 1e-9, 'exits at the target; result = R:R minus the fee in R');

// the same: default R:R band 7–9 refuses it (R:R here is far below 7)
r = K.compute(longBars, 5, {});
ok(r.trades.length === 0 && r.stats.skipped === 1, 'default R:R 7–9: setup refused, counted as skipped');

// a short, mirrored, stopped out
const shortBars = bars(4, 100, [[3, 30, {o: 99.6, h: 99.7, l: 97.0, c: 97.1}], [3, 33, {o: 97.5, h: 101.5, l: 97.4, c: 101}]]);
r = K.compute(shortBars, 5, {rrMin: 0, rrMax: 0});
t = r.trades[0];
ok(t && t.dir === -1 && t.entry === 97.1 && t.status === 'STOPPED' && t.exitP === t.stop0 && t.r < -1, 'short below the previous 1h low, stopped out at its stop (−1R − fee)');

// once per level: a second break of the same level in the same hour doesn't fire again
const twice = bars(4, 100, [[3, 30, {o: 100.4, h: 103.1, l: 100.3, c: 103}], [3, 31, {o: 103, h: 103.1, l: 94, c: 95}],
  [3, 36, {o: 100.4, h: 103.1, l: 100.3, c: 103}]]);
r = K.compute(twice, 5, {rrMin: 0, rrMax: 0, cooldown: 0});
ok(r.trades.filter(x => x.dir === 1 && Math.abs(x.level - 100.5) < 1e-9).length === 1, 'a level fires once until the next HTF candle brings a new level');

// a stop tighter than 1 ATR is refused (stop 0.5 ATR)
r = K.compute(longBars, 5, {rrMin: 0, rrMax: 0, stopATR: 0.5});
ok(r.trades.length === 0 && r.stats.skipped === 1, 'stop tighter than 1 × ATR refused');

// HTF must be above the chart's timeframe
r = K.compute(longBars, 5, {htf: '15', rrMin: 0, rrMax: 0});
ok(r.htfOK === true, '15m HTF on a 5m chart is fine');
r = K.compute(longBars, 60, {htf: '60'});
ok(r.htfOK === false && r.trades.length === 0, 'HTF equal to the chart: no trades (the script warns)');

console.log(fails ? `${fails} check(s) failed` : 'All checks passed');
process.exit(fails ? 1 : 0);
