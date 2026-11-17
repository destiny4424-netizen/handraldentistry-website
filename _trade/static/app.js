'use strict';
/* Meridian Trade: the app in the browser. Talks only to its own server (/api/...), which talks to Dhan. */

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const css = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();

const S = {status: null, tab: 'watch', wl: [], wlIdx: 0, info: {}, quotes: {}, editWl: false, sheetKey: null,
  orderFilter: 'open', insDays: 90, chainUl: 'NIFTY', chainExp: '', posKeys: [], lastOrders: [], ticket: null};

async function api(path, body, method) {
  const opt = {headers: {'X-Requested-With': 'meridian-trade'}};
  if (body !== undefined) { opt.method = method || 'POST'; opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
  const r = await fetch(path, opt);
  if (r.status === 401) { location.reload(); throw new Error('Signed out'); }
  let j = {};
  try { j = await r.json(); } catch (e) { j = {error: 'The server answered oddly (' + r.status + ')'}; }
  if (!r.ok) { const e = new Error(j.error || ('Error ' + r.status)); e.data = j; e.status = r.status; throw e; }
  return j;
}

// ---- formatting --------------------------------------------------------------------------------
function inr(v, dp) {
  if (v === null || v === undefined || v === '' || isNaN(v)) return '–';
  v = Number(v);
  const d = dp ?? (Math.abs(v) >= 1000 ? 0 : 2);
  return (v < 0 ? '−' : '') + '₹' + Math.abs(v).toLocaleString('en-IN', {minimumFractionDigits: d, maximumFractionDigits: d});
}
function inrShort(v) {
  if (v === null || v === undefined || isNaN(v)) return '–';
  const a = Math.abs(v), s = v < 0 ? '−' : '';
  if (a >= 1e7) return s + '₹' + (a / 1e7).toFixed(2) + ' Cr';
  if (a >= 1e5) return s + '₹' + (a / 1e5).toFixed(2) + ' L';
  return s + '₹' + a.toLocaleString('en-IN', {maximumFractionDigits: 0});
}
function px(v) {
  if (v === null || v === undefined || v === '' || isNaN(v)) return '–';
  return Number(v).toLocaleString('en-IN', {minimumFractionDigits: 2, maximumFractionDigits: 2});
}
function big(v) {
  if (v === null || v === undefined || isNaN(v)) return '–';
  v = Number(v);
  if (Math.abs(v) >= 1e7) return (v / 1e7).toFixed(2) + 'Cr';
  if (Math.abs(v) >= 1e5) return (v / 1e5).toFixed(2) + 'L';
  if (Math.abs(v) >= 1e3) return (v / 1e3).toFixed(1) + 'K';
  return String(Math.round(v));
}
const cls = v => (v > 0 ? 'up' : v < 0 ? 'down' : '');
const signed = (v, f = px) => (v > 0 ? '+' : '') + f(v);

function toast(msg, kind, ms = 3500) {
  const t = document.createElement('div');
  t.className = 'toast ' + (kind || '');
  t.textContent = msg;
  const box = $('#toasts');
  while (box.children.length >= 2) box.firstChild.remove();
  box.appendChild(t);
  setTimeout(() => t.remove(), ms);
}

function kindTag(it) {
  if (!it) return '';
  const k = it.kind;
  const t = k === 'EQUITY' ? it.exch : k === 'INDEX' ? 'INDEX' : k.startsWith('FUT') ? 'FUT' : it.opt;
  const exp = it.expiry ? ' ' + new Date(it.expiry + 'T00:00').toLocaleDateString('en-IN', {day: 'numeric', month: 'short'}) : '';
  return `<span class="tag">${esc(t)}${esc(exp)}</span>` + (k !== 'EQUITY' && k !== 'INDEX' && it.exch !== 'NSE' ? `<span class="tag">${esc(it.exch)}</span>` : '');
}
function shortName(it) {
  if (!it) return '';
  if (it.kind === 'EQUITY' || it.kind === 'INDEX') return it.sym;
  if (it.opt) return `${it.ul} ${Number(it.strike).toLocaleString('en-IN')} ${it.opt}`;
  return `${it.ul} FUT`;
}

// ---- status & header -----------------------------------------------------------------------------
async function loadStatus() {
  try {
    const s = await api('/api/status');
    S.status = s;
    $('#demo').classList.toggle('hidden', !s.demo);
    $('#conn').className = 'dot ' + (s.connected && !s.feed_error ? 'ok' : s.connected ? 'warn' : 'bad');
    $('#conn').title = s.connected ? `Dhan ${s.client}` + (s.feed_error ? ' · ' + s.feed_error : '') : 'Dhan not connected';
    const b = $('#banner');
    let msg = '';
    if (!s.connected) msg = 'Dhan isn\'t connected yet. Open Account → Dhan login and paste your Client ID and access token.';
    else if (s.token_note && /expired|failed/i.test(s.token_note)) msg = s.token_note;
    else if (s.feed_error) msg = s.feed_error;
    else if (s.inst_error) msg = s.inst_error;
    b.textContent = msg;
    b.classList.toggle('hidden', !msg);
    if (!S.wl.length) { S.wl = s.watchlists || []; renderWlTabs(); renderWl(); }
  } catch (e) { $('#conn').className = 'dot bad'; }
}
async function loadFunds() {
  try {
    const f = await api('/api/funds');
    $('#funds').textContent = inrShort(f.availabelBalance ?? f.availableBalance);
    S.funds = f;
  } catch (e) { $('#funds').textContent = ''; }
}

// ---- tabs ------------------------------------------------------------------------------------------
function showTab(t) {
  S.tab = t;
  $$('#tabs button').forEach(b => b.classList.toggle('on', b.dataset.tab === t));
  $$('.tab').forEach(s => s.classList.toggle('on', s.id === 'tab-' + t));
  if (t === 'orders') loadOrders();
  if (t === 'positions') loadPositions();
  if (t === 'chain') loadChain(true);
  if (t === 'insights') loadInsights();
  if (t === 'account') loadAccount();
  window.scrollTo(0, 0);
}
$('#tabs').onclick = e => { const b = e.target.closest('button'); if (b) showTab(b.dataset.tab); };

// ---- quotes ----------------------------------------------------------------------------------------
function visibleKeys() {
  const keys = new Set();
  if (S.tab === 'watch') (S.wl[S.wlIdx]?.keys || []).forEach(k => keys.add(k));
  if (S.tab === 'positions' || S.tab === 'account') S.posKeys.forEach(k => keys.add(k));
  if (S.sheetKey) keys.add(S.sheetKey);
  if (S.ticket?.key) keys.add(S.ticket.key);
  return [...keys];
}
let quoteBusy = false;
async function pollQuotes() {
  const keys = visibleKeys();
  if (!keys.length || quoteBusy || document.hidden) return;
  quoteBusy = true;
  try {
    const need = keys.filter(k => !S.info[k]);
    const r = await api('/api/quotes?keys=' + encodeURIComponent(keys.join(',')) + (need.length ? '&info=1' : ''));
    Object.assign(S.info, r.info || {});
    for (const [k, q] of Object.entries(r.quotes || {})) {
      const old = S.quotes[k];
      if (q) { q._dir = old && old.ltp !== q.ltp ? (q.ltp > old.ltp ? 'up' : 'down') : ''; S.quotes[k] = q; }
    }
    if (need.length && S.tab === 'watch') renderWl();
    updateQuotes();
  } catch (e) { /* next tick */ }
  quoteBusy = false;
}
function updateQuotes() {
  $$('[data-q]').forEach(el => {
    const q = S.quotes[el.dataset.q];
    if (!q) return;
    const f = el.dataset.f || 'ltp';
    if (f === 'ltp') {
      el.textContent = px(q.ltp);
      if (q._dir) { el.classList.remove('flash-up', 'flash-down'); void el.offsetWidth; el.classList.add('flash-' + q._dir); }
    } else if (f === 'chg') {
      el.textContent = `${signed(q.chg)} (${signed(q.pct, v => Number(v).toFixed(2))}%)`;
      el.className = 'chg ' + cls(q.chg);
    }
  });
  if (S.sheetKey) updateSheetQuote();
  if (S.ticket) updateTicketQuote();
}

// ---- watchlists ----------------------------------------------------------------------------------
function renderWlTabs() {
  const el = $('#wlTabs');
  el.innerHTML = S.wl.map((w, i) => `<button data-i="${i}" class="${i === S.wlIdx ? 'on' : ''}">${esc(w.name)}</button>`).join('') +
    (S.editWl ? '<button data-new="1">+ New list</button>' : '');
  el.onclick = e => {
    const b = e.target.closest('button'); if (!b) return;
    if (b.dataset.new) {
      const n = prompt('Name of the new watchlist'); if (!n) return;
      S.wl.push({name: n.slice(0, 30), keys: []}); S.wlIdx = S.wl.length - 1; saveWl();
    } else if (S.editWl && +b.dataset.i === S.wlIdx) {
      const n = prompt('Rename this list (empty to delete it)', S.wl[S.wlIdx].name);
      if (n === null) return;
      if (!n.trim()) { if (S.wl.length > 1 && confirm('Delete this watchlist?')) { S.wl.splice(S.wlIdx, 1); S.wlIdx = 0; } }
      else S.wl[S.wlIdx].name = n.slice(0, 30);
      saveWl();
    } else { S.wlIdx = +b.dataset.i; }
    renderWlTabs(); renderWl(); pollQuotes();
  };
}
function renderWl() {
  const w = S.wl[S.wlIdx];
  const el = $('#wl');
  const emptyList = !w || !w.keys.length;
  $('#wlEmpty').classList.toggle('hidden', !emptyList);
  if (emptyList) { el.innerHTML = ''; return; }
  el.innerHTML = w.keys.map(k => {
    const it = S.info[k], q = S.quotes[k];
    return `<div class="item" data-key="${esc(k)}">
      <div class="grow"><div class="name">${esc(shortName(it) || k)} ${kindTag(it)}</div>
      <div class="sub">${esc(it ? (it.kind === 'EQUITY' ? it.co || it.name : it.kind === 'INDEX' ? (it.exch + ' index') : it.name) : '')}</div></div>
      <div class="right"><div class="ltp" data-q="${esc(k)}">${q ? px(q.ltp) : '…'}</div>
      <div class="chg ${q ? cls(q.chg) : ''}" data-q="${esc(k)}" data-f="chg">${q ? `${signed(q.chg)} (${signed(q.pct, v => Number(v).toFixed(2))}%)` : ''}</div></div>
      ${S.editWl ? `<button class="x" data-rm="${esc(k)}" title="Remove">✕</button>` : ''}</div>`;
  }).join('');
}
$('#wl').onclick = wlClick;
function wlClick(e) {
  const rm = e.target.closest('[data-rm]');
  if (rm) { e.stopPropagation(); const w = S.wl[S.wlIdx]; w.keys = w.keys.filter(k => k !== rm.dataset.rm); saveWl(); renderWl(); return; }
  const it = e.target.closest('.item'); if (it) openInstrument(it.dataset.key);
}
$('#wlEdit').onclick = () => { S.editWl = !S.editWl; $('#wlEdit').textContent = S.editWl ? 'Done' : 'Edit'; renderWlTabs(); renderWl(); };
async function saveWl() {
  try { await api('/api/watchlists', {watchlists: S.wl}); } catch (e) { toast(e.message, 'bad'); }
}
function inWl(k) { return (S.wl[S.wlIdx]?.keys || []).includes(k); }
function toggleWl(k) {
  const w = S.wl[S.wlIdx]; if (!w) return;
  if (w.keys.includes(k)) w.keys = w.keys.filter(x => x !== k); else w.keys.push(k);
  saveWl(); renderWl(); pollQuotes();
}

// ---- search -------------------------------------------------------------------------------------------
let searchT = 0;
$('#q').oninput = () => { clearTimeout(searchT); searchT = setTimeout(doSearch, 180); };
$('#q').onkeydown = e => { if (e.key === 'Escape') { $('#q').value = ''; doSearch(); } };
async function doSearch() {
  const q = $('#q').value.trim();
  const el = $('#results');
  if (q.length < 2) { el.classList.add('hidden'); el.innerHTML = ''; return; }
  const r = await api('/api/search?q=' + encodeURIComponent(q));
  r.results.forEach(it => { S.info[it.key] = it; });
  el.innerHTML = r.results.length ? r.results.map(it => `<div class="res" data-key="${esc(it.key)}">
      <div class="grow"><div class="name">${esc(shortName(it))} ${kindTag(it)}</div><div class="sub">${esc(it.name)}${it.lot > 1 ? ' · lot ' + it.lot : ''}</div></div>
      <button data-add="${esc(it.key)}" class="${inWl(it.key) ? 'in' : ''}" title="Add to watchlist">${inWl(it.key) ? '✓' : '＋'}</button></div>`).join('')
    : '<div class="res muted">Nothing found. Try the NSE symbol, or e.g. "banknifty 58000 pe".</div>';
  el.classList.remove('hidden');
}
$('#results').onclick = e => {
  const add = e.target.closest('[data-add]');
  if (add) { toggleWl(add.dataset.add); add.textContent = inWl(add.dataset.add) ? '✓' : '＋'; add.classList.toggle('in', inWl(add.dataset.add)); return; }
  const r = e.target.closest('.res[data-key]'); if (r) openInstrument(r.dataset.key);
};

// ---- sheet --------------------------------------------------------------------------------------------
function openSheet(html) {
  $('#sheetBody').innerHTML = html;
  $('#sheet').classList.remove('hidden');
  document.body.style.overflow = 'hidden';
}
function closeSheet() {
  $('#sheet').classList.add('hidden');
  document.body.style.overflow = '';
  S.sheetKey = null; S.ticket = null;
}
$('#sheet').addEventListener('click', e => { if (e.target.classList.contains('sheet-bg') || e.target.closest('.sh-close')) closeSheet(); });
document.addEventListener('keydown', e => { if (e.key === 'Escape' && !$('#sheet').classList.contains('hidden')) closeSheet(); });

async function ensureInfo(key) {
  if (S.info[key]) return S.info[key];
  const it = await api('/api/instrument?key=' + encodeURIComponent(key));
  if (it && it.key) S.info[key] = it;
  return S.info[key];
}

async function openInstrument(key) {
  const it = await ensureInfo(key);
  if (!it) return toast('Unknown instrument', 'bad');
  S.ticket = null; S.sheetKey = key;
  const tradable = it.kind !== 'INDEX';
  const hasChain = it.kind === 'INDEX' || it.kind === 'EQUITY' || it.opt || it.kind.startsWith('FUT');
  openSheet(`<div class="sh-head"><div><h2>${esc(shortName(it))} ${kindTag(it)}</h2><div class="muted small">${esc(it.name)}${it.lot > 1 ? ' · lot size ' + it.lot : ''}</div></div>
    <button class="sh-close" aria-label="Close">✕</button></div>
    <div><span class="bigprice" id="shLtp">–</span> <span id="shChg" class="chg"></span></div>
    <div class="stats" id="shStats"></div>
    <div class="chips tf" id="tf">${['1', '5', '15', '60', 'D'].map(t => `<button data-tf="${t}" class="${t === '5' ? 'on' : ''}">${t === 'D' ? 'Day' : t + 'm'}</button>`).join('')}</div>
    <canvas class="chart" id="chart"></canvas>
    <div class="section-title">Market depth</div><div class="depth" id="depth"><div class="muted small">Waiting for prices…</div></div>
    <div class="ord-actions" style="margin-top:12px">
      <button id="shWl">${inWl(key) ? '✓ In watchlist' : '＋ Watchlist'}</button>
      ${hasChain ? `<button id="shChain">Option chain</button>` : ''}
    </div>
    ${tradable ? `<div class="bs"><button class="b-buy" id="shBuy">BUY</button><button class="b-sell" id="shSell">SELL</button></div>`
      : '<div class="muted small" style="margin-top:12px">An index can\'t be bought itself: open its option chain, or search for its futures.</div>'}`);
  updateSheetQuote();
  $('#shWl').onclick = () => { toggleWl(key); $('#shWl').textContent = inWl(key) ? '✓ In watchlist' : '＋ Watchlist'; };
  if ($('#shChain')) $('#shChain').onclick = () => { S.chainUl = it.ul || it.sym; S.chainExp = it.opt ? it.expiry : ''; closeSheet(); showTab('chain'); };
  if (tradable) { $('#shBuy').onclick = () => openTicket(key, 'BUY'); $('#shSell').onclick = () => openTicket(key, 'SELL'); }
  $('#tf').onclick = e => { const b = e.target.closest('button'); if (!b) return; $$('#tf button').forEach(x => x.classList.toggle('on', x === b)); drawChart(key, b.dataset.tf); };
  drawChart(key, '5');
  pollQuotes();
}
function updateSheetQuote() {
  const q = S.quotes[S.sheetKey]; if (!q || !$('#shLtp')) return;
  $('#shLtp').textContent = px(q.ltp);
  $('#shChg').textContent = `${signed(q.chg)} (${signed(q.pct, v => Number(v).toFixed(2))}%)`;
  $('#shChg').className = 'chg ' + cls(q.chg);
  $('#shStats').innerHTML = [['Open', q.open], ['High', q.high], ['Low', q.low], ['Prev close', q.prev]]
    .map(([l, v]) => `<div>${l}<b>${px(v)}</b></div>`).join('') +
    (q.vol ? `<div>Volume<b>${big(q.vol)}</b></div>` : '') + (q.oi ? `<div>OI<b>${big(q.oi)}</b></div>` : '') +
    (q.avg ? `<div>Avg price<b>${px(q.avg)}</b></div>` : '') + (q.uc ? `<div>Circuit<b class="small">${px(q.lc)}–${px(q.uc)}</b></div>` : '');
  if (q.bids && q.bids.length) {
    const tb = (rows, c) => `<table>${rows.map(r => `<tr><td class="${c}">${px(r[0])}</td><td>${big(r[1])}</td></tr>`).join('')}</table>`;
    $('#depth').innerHTML = `<div><div class="muted small">Bids</div>${tb(q.bids, 'up')}</div><div><div class="muted small">Offers</div>${tb(q.asks, 'down')}</div>`;
  }
}

// ---- chart --------------------------------------------------------------------------------------------
async function drawChart(key, tf) {
  const cv = $('#chart'); if (!cv) return;
  const ctx = cv.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const W = cv.clientWidth, H = cv.clientHeight;
  cv.width = W * dpr; cv.height = H * dpr; ctx.scale(dpr, dpr);
  ctx.fillStyle = css('--muted'); ctx.font = '12px system-ui'; ctx.fillText('Loading chart…', 12, 20);
  let r;
  try { r = await api(`/api/chart?key=${encodeURIComponent(key)}&tf=${tf}`); } catch (e) { r = {error: e.message}; }
  if (S.sheetKey !== key || !$('#chart')) return;
  ctx.clearRect(0, 0, W, H);
  if (r.error || !r.rows || !r.rows.length) { ctx.fillStyle = css('--muted'); ctx.fillText(r.error || 'No candles for this period.', 12, 20); return; }
  const rows = r.rows.slice(-Math.max(30, Math.floor(W / 6)));
  const hi = Math.max(...rows.map(x => x[2])), lo = Math.min(...rows.map(x => x[3]));
  const pad = 8, right = 56, top = 10, bot = 18;
  const y = v => top + (hi - v) / ((hi - lo) || 1) * (H - top - bot);
  const cw = (W - pad - right) / rows.length;
  ctx.strokeStyle = css('--line'); ctx.lineWidth = 1; ctx.fillStyle = css('--muted'); ctx.font = '10px system-ui';
  for (let i = 0; i <= 4; i++) {
    const v = lo + (hi - lo) * i / 4, yy = y(v);
    ctx.beginPath(); ctx.moveTo(pad, yy); ctx.lineTo(W - right, yy); ctx.stroke();
    ctx.fillText(px(v), W - right + 4, yy + 3);
  }
  const up = css('--up'), dn = css('--down');
  rows.forEach((c, i) => {
    const x = pad + i * cw + cw / 2;
    const col = c[4] >= c[1] ? up : dn;
    ctx.strokeStyle = col; ctx.fillStyle = col;
    ctx.beginPath(); ctx.moveTo(x, y(c[2])); ctx.lineTo(x, y(c[3])); ctx.stroke();
    const b1 = y(Math.max(c[1], c[4])), b2 = y(Math.min(c[1], c[4]));
    ctx.fillRect(x - Math.max(1, cw * 0.35), b1, Math.max(2, cw * 0.7), Math.max(1, b2 - b1));
  });
  const last = rows[rows.length - 1][4];
  ctx.setLineDash([3, 3]); ctx.strokeStyle = css('--buy');
  ctx.beginPath(); ctx.moveTo(pad, y(last)); ctx.lineTo(W - right, y(last)); ctx.stroke(); ctx.setLineDash([]);
  const t0 = new Date(rows[0][0] * 1000), t1 = new Date(rows[rows.length - 1][0] * 1000);
  const f = d => tf === 'D' ? d.toLocaleDateString('en-IN', {day: 'numeric', month: 'short'}) : d.toLocaleString('en-IN', {day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit'});
  ctx.fillStyle = css('--muted'); ctx.fillText(f(t0), pad, H - 4); ctx.textAlign = 'right'; ctx.fillText(f(t1), W - right, H - 4); ctx.textAlign = 'left';
}

// ---- order ticket ---------------------------------------------------------------------------------------
async function openTicket(key, side, preset = {}) {
  const it = await ensureInfo(key);
  if (!it) return;
  S.sheetKey = null;
  const eq = it.seg === 'NSE_EQ' || it.seg === 'BSE_EQ';
  const lot = eq ? 1 : it.lot;
  const q = S.quotes[key];
  S.ticket = {key, it, side, eq, lot, product: preset.product || 'INTRADAY', type: preset.type || 'LIMIT',
    lots: preset.qty ? Math.max(1, Math.round(preset.qty / lot)) : 1, price: preset.price ?? (q ? q.ltp : ''), trigger: '',
    superOn: false, target: '', sl: '', trail: '', validity: 'DAY', amo: false, slice: false, exit: !!preset.exit, priceTouched: !!preset.price};
  renderTicket();
  pollQuotes();
}
function renderTicket() {
  const t = S.ticket, it = t.it;
  const products = t.eq ? [['INTRADAY', 'Intraday'], ['CNC', 'Delivery'], ['MTF', 'MTF']] : [['INTRADAY', 'Intraday'], ['MARGIN', 'Carry (NRML)']];
  const types = [['LIMIT', 'Limit'], ['MARKET', 'Market'], ['STOP_LOSS', 'SL'], ['STOP_LOSS_MARKET', 'SL-M']];
  openSheet(`<div class="ticket">
    <div class="sh-head"><div><h2>${esc(shortName(it))} ${kindTag(it)}</h2>
      <div class="muted small"><span id="tkLtp">${px(S.quotes[t.key]?.ltp)}</span> · ${esc(it.name)}</div></div>
      <button class="sh-close" aria-label="Close">✕</button></div>
    ${t.exit ? '<div class="okbox">Closing your position. Check the quantity and price, then review.</div>' : ''}
    <div class="side"><button data-side="BUY" class="buy ${t.side === 'BUY' ? 'on' : ''}">BUY</button><button data-side="SELL" class="sell ${t.side === 'SELL' ? 'on' : ''}">SELL</button></div>
    <div class="seg" id="tkProd">${products.map(([v, l]) => `<button data-v="${v}" class="${t.product === v ? 'on' : ''}">${l}</button>`).join('')}</div>
    <div class="seg" id="tkType">${types.map(([v, l]) => `<button data-v="${v}" class="${t.type === v ? 'on' : ''}">${l}</button>`).join('')}</div>
    <div class="fields">
      <div class="field"><label>${t.lot > 1 ? 'Lots' : 'Quantity'}</label>
        <div class="stepper"><button data-step="-1">−</button><input id="tkLots" inputmode="numeric" value="${t.lots}"><button data-step="1">＋</button></div>
        <div class="hint" id="tkQtyHint"></div></div>
      <div class="field" id="fPrice"><label>Price</label><input id="tkPrice" inputmode="decimal" value="${t.price === '' ? '' : px(t.price).replace(/,/g, '')}">
        <div class="hint">Tick ₹${it.tick}</div></div>
      <div class="field" id="fTrig"><label>Trigger price</label><input id="tkTrig" inputmode="decimal" value="${t.trigger}"></div>
    </div>
    <label class="toggle"><input type="checkbox" id="tkSuper" ${t.superOn ? 'checked' : ''}> Add target &amp; stop-loss (Super order)</label>
    <div id="superBox" class="fields ${t.superOn ? '' : 'hidden'}">
      <div class="field"><label>Target price</label><input id="tkTgt" inputmode="decimal" value="${t.target}"></div>
      <div class="field"><label>Stop-loss price</label><input id="tkSl" inputmode="decimal" value="${t.sl}"></div>
      <div class="field"><label>Trailing jump (₹, optional)</label><input id="tkTrail" inputmode="decimal" value="${t.trail}"></div>
    </div>
    <details class="box"><summary class="muted">More options</summary>
      <label class="toggle"><input type="checkbox" id="tkIoc" ${t.validity === 'IOC' ? 'checked' : ''}> IOC (fill now or cancel)</label>
      <label class="toggle"><input type="checkbox" id="tkAmo" ${t.amo ? 'checked' : ''}> After-market order (AMO)</label>
      ${!t.eq ? `<label class="toggle"><input type="checkbox" id="tkSlice" ${t.slice ? 'checked' : ''}> Split into exchange-size pieces (for very large quantities)</label>` : ''}
    </details>
    <div id="tkMsg"></div>
    <button class="go ${t.side === 'BUY' ? 'b-buy' : 'b-sell'}" id="tkReview" style="width:100%;margin-top:6px">Review ${t.side === 'BUY' ? 'buy' : 'sell'} order</button>
  </div>`);
  syncTicketFields();
  const body = $('#sheetBody');
  body.querySelector('.side').onclick = e => { const b = e.target.closest('button'); if (!b) return; readTicket(); t.side = b.dataset.side; renderTicket(); };
  $('#tkProd').onclick = e => { const b = e.target.closest('button'); if (!b) return; readTicket(); t.product = b.dataset.v; renderTicket(); };
  $('#tkType').onclick = e => { const b = e.target.closest('button'); if (!b) return; readTicket(); t.type = b.dataset.v; if (t.type !== 'LIMIT' && t.type !== 'MARKET') t.superOn = false; renderTicket(); };
  body.querySelectorAll('[data-step]').forEach(b => b.onclick = () => { readTicket(); t.lots = Math.max(1, (parseInt(t.lots) || 1) + +b.dataset.step); $('#tkLots').value = t.lots; syncTicketFields(); });
  $('#tkSuper').onchange = () => { readTicket(); t.superOn = $('#tkSuper').checked; if (t.superOn && !['LIMIT', 'MARKET'].includes(t.type)) t.type = 'LIMIT'; renderTicket(); };
  ['tkLots', 'tkPrice', 'tkTrig', 'tkTgt', 'tkSl'].forEach(id => { const el = $('#' + id); if (el) el.oninput = () => { if (id === 'tkPrice') t.priceTouched = true; readTicket(); syncTicketFields(); }; });
  $('#tkReview').onclick = reviewOrder;
}
function readTicket() {
  const t = S.ticket; if (!t || !$('#tkLots')) return;
  t.lots = parseInt($('#tkLots').value) || 0;
  t.price = $('#tkPrice').value; t.trigger = $('#tkTrig').value;
  t.target = $('#tkTgt')?.value || ''; t.sl = $('#tkSl')?.value || ''; t.trail = $('#tkTrail')?.value || '';
  t.validity = $('#tkIoc')?.checked ? 'IOC' : 'DAY'; t.amo = !!$('#tkAmo')?.checked; t.slice = !!$('#tkSlice')?.checked;
}
function syncTicketFields() {
  const t = S.ticket;
  $('#fPrice').classList.toggle('hidden', t.type === 'MARKET' || t.type === 'STOP_LOSS_MARKET');
  $('#fTrig').classList.toggle('hidden', !(t.type === 'STOP_LOSS' || t.type === 'STOP_LOSS_MARKET'));
  const qty = (parseInt(t.lots) || 0) * t.lot;
  const p = parseFloat(t.price) || S.quotes[t.key]?.ltp || 0;
  $('#tkQtyHint').textContent = (t.lot > 1 ? `${qty.toLocaleString('en-IN')} units · ` : '') + (p ? `≈ ${inrShort(qty * p)}` : '');
}
function updateTicketQuote() {
  const t = S.ticket, q = S.quotes[t.key]; if (!q || !$('#tkLtp')) return;
  $('#tkLtp').textContent = px(q.ltp);
  if (!t.priceTouched && $('#tkPrice') && !$('#tkPrice').value) { $('#tkPrice').value = Number(q.ltp).toFixed(2); t.price = q.ltp; syncTicketFields(); }
}
function ticketBody(extra = {}) {
  const t = S.ticket;
  return Object.assign({key: t.key, side: t.side, qty: (parseInt(t.lots) || 0) * t.lot, product: t.product, type: t.type,
    price: parseFloat(t.price) || 0, trigger: parseFloat(t.trigger) || 0, validity: t.validity, amo: t.amo, slice: t.slice,
    super: t.superOn ? {target: parseFloat(t.target) || 0, sl: parseFloat(t.sl) || 0, trail: parseFloat(t.trail) || 0} : null}, extra);
}
async function reviewOrder() {
  readTicket();
  const t = S.ticket;
  const btn = $('#tkReview'); btn.disabled = true; btn.textContent = 'Checking…';
  let r;
  try { r = await api('/api/order/preview', ticketBody()); } catch (e) { btn.disabled = false; btn.textContent = 'Review order'; return toast(e.message, 'bad'); }
  btn.disabled = false; btn.textContent = `Review ${t.side === 'BUY' ? 'buy' : 'sell'} order`;
  if (r.problems && r.problems.length) { $('#tkMsg').innerHTML = r.problems.map(p => `<div class="badbox">${esc(p)}</div>`).join(''); return; }
  showConfirm(r);
}
function showConfirm(r) {
  const t = S.ticket, it = t.it;
  const qty = (parseInt(t.lots) || 0) * t.lot;
  const typeName = {LIMIT: 'Limit', MARKET: 'Market', STOP_LOSS: 'Stop-loss limit', STOP_LOSS_MARKET: 'Stop-loss market'}[t.type];
  const prodName = {INTRADAY: 'Intraday', CNC: 'Delivery', MTF: 'MTF', MARGIN: 'Carry forward (NRML)'}[t.product];
  const priceTxt = t.type === 'MARKET' ? 'Market price' : t.type === 'STOP_LOSS_MARKET' ? `Market, when ₹${px(t.trigger)} trades` :
    t.type === 'STOP_LOSS' ? `₹${px(t.price)} once ₹${px(t.trigger)} trades` : `₹${px(t.price)}`;
  const m = r.margin || {};
  const live = !S.status?.demo;
  openSheet(`<div class="sh-head"><div><div class="confirm-title"><span class="pill ${t.side === 'BUY' ? 'buy' : 'sell'}">${t.side}</span> ${esc(shortName(it))}</div>
      <div class="muted small">${esc(it.name)} ${kindTag(it)}</div></div><button class="sh-close" aria-label="Close">✕</button></div>
    <div class="box">
      <div class="kv"><span>Quantity</span><b>${qty.toLocaleString('en-IN')}${t.lot > 1 ? ` (${t.lots} lot${t.lots > 1 ? 's' : ''} × ${t.lot})` : ''}</b></div>
      <div class="kv"><span>Price</span><b>${priceTxt}</b></div>
      <div class="kv"><span>Order</span><b>${typeName} · ${prodName}${t.validity === 'IOC' ? ' · IOC' : ''}${t.amo ? ' · AMO' : ''}</b></div>
      <div class="kv"><span>Order value</span><b>${inr(r.value)}</b></div>
      ${t.superOn ? `<div class="kv"><span>Target / Stop-loss</span><b>${t.target ? '₹' + px(t.target) : '–'} / ${t.sl ? '₹' + px(t.sl) : '–'}${t.trail ? ' · trail ₹' + t.trail : ''}</b></div>` : ''}
      ${r.risk !== undefined ? `<div class="kv"><span>Money at risk to stop</span><b class="down">${inr(r.risk)}</b></div>` : ''}
      ${r.reward !== undefined ? `<div class="kv"><span>Profit at target</span><b class="up">${inr(r.reward)}${r.risk ? ` · ${(r.reward / r.risk).toFixed(1)}R` : ''}</b></div>` : ''}
    </div>
    <div class="box">
      <div class="kv"><span>Margin needed</span><b>${m.totalMargin !== undefined ? inr(m.totalMargin) : '–'}</b></div>
      <div class="kv"><span>Available</span><b>${m.availableBalance !== undefined ? inr(m.availableBalance) : '–'}</b></div>
      <div class="kv"><span>Charges (estimate)</span><b>${inr(r.charges, 2)}</b></div>
      ${r.day_pnl !== undefined ? `<div class="kv"><span>Today's P&amp;L so far</span><b class="${cls(r.day_pnl)}">${inr(r.day_pnl)}</b></div>` : ''}
      ${r.margin_error ? `<div class="muted small">Margin check: ${esc(r.margin_error)}</div>` : ''}
    </div>
    ${(r.warnings || []).map(w => `<div class="warnbox">⚠ ${esc(w)}</div>`).join('')}
    ${r.needs_big_ok ? `<label class="toggle"><input type="checkbox" id="bigOk"> I'm sure: this order is above my order-size limit</label>` : ''}
    ${live ? '<div class="muted small">This sends a real order to your Dhan account.</div>' : '<div class="muted small">Demo: this goes to a pretend account.</div>'}
    <div class="bs"><button class="ghost" id="cfBack" style="color:var(--text)">Edit</button>
      <button class="go ${t.side === 'BUY' ? 'b-buy' : 'b-sell'}" id="cfGo" ${r.needs_big_ok ? 'disabled' : ''}>Place ${t.side} order</button></div>
    <div id="cfMsg"></div>`);
  if ($('#bigOk')) $('#bigOk').onchange = () => { $('#cfGo').disabled = !$('#bigOk').checked; };
  $('#cfBack').onclick = () => renderTicket();
  $('#cfGo').onclick = placeOrder;
}
async function placeOrder() {
  const go = $('#cfGo'); go.disabled = true; go.textContent = 'Sending…';
  const cid = 'mt' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
  try {
    const r = await api('/api/order', ticketBody({big_ok: !!$('#bigOk')?.checked, cid}));
    const o = (r.orders || [])[0] || {};
    closeSheet();
    toast(`Order sent: ${r.sym} · ${o.orderStatus || 'sent'}${o.orderId ? ' · #' + o.orderId : ''}`, 'ok', 6000);
    setTimeout(() => { loadOrders(); loadPositions(); loadFunds(); }, 600);
    if (S.tab !== 'orders' && S.tab !== 'positions') showTab('orders');
  } catch (e) {
    go.disabled = false; go.textContent = 'Try again';
    $('#cfMsg').innerHTML = `<div class="badbox">${esc(e.message)}</div>`;
  }
}

// ---- orders ---------------------------------------------------------------------------------------------
const OPEN = ['PENDING', 'TRANSIT', 'TRIGGERED', 'PART_TRADED', 'PARTIALLY_FILLED', 'CONFIRM'];
$('#orderFilter').onclick = e => { const b = e.target.closest('button'); if (!b) return; S.orderFilter = b.dataset.f; $$('#orderFilter button').forEach(x => x.classList.toggle('on', x === b)); renderOrders(); };
async function loadOrders() {
  try { const r = await api('/api/orders'); S.lastOrders = r.orders; S.superOrders = r.super || []; renderOrders(); }
  catch (e) { $('#orders').innerHTML = `<div class="item muted">${esc(e.message)}</div>`; }
}
function statusPill(s) {
  const c = s === 'TRADED' ? 'ok' : s === 'REJECTED' || s === 'CANCELLED' || s === 'EXPIRED' ? 'bad' : 'warn';
  return `<span class="pill ${c}">${esc(String(s || '').replace('_', ' '))}</span>`;
}
function renderOrders() {
  const all = S.lastOrders || [];
  const sup = (S.superOrders || []).map(o => Object.assign({}, o, {_super: true}));
  const list = [...sup, ...all].filter(o => S.orderFilter === 'all' || (S.orderFilter === 'open' ? OPEN.includes(o.orderStatus) || (o._super && (o.legDetails || []).some(l => OPEN.includes(l.orderStatus))) : o.orderStatus === 'TRADED'));
  const nOpen = all.filter(o => OPEN.includes(o.orderStatus)).length;
  $('#ordersBadge').textContent = nOpen; $('#ordersBadge').classList.toggle('hidden', !nOpen);
  const el = $('#orders');
  if (!list.length) { el.innerHTML = `<div class="item muted">${S.orderFilter === 'open' ? 'No open orders.' : 'No orders today.'}</div>`; return; }
  el.innerHTML = list.map(o => {
    const open = OPEN.includes(o.orderStatus);
    const t = (o.updateTime || o.createTime || '').slice(11, 19);
    const priceTxt = o.orderType === 'MARKET' ? 'MKT' : o.orderType === 'STOP_LOSS_MARKET' ? `SL-M ${px(o.triggerPrice)}` : o.orderType === 'STOP_LOSS' ? `${px(o.price)} (trg ${px(o.triggerPrice)})` : px(o.price);
    const legs = o._super ? (o.legDetails || []).map(l => `<div class="sub">${esc(l.legName.replace('_LEG', '').replace('_', ' '))}: ${px(l.price)} ${statusPill(l.orderStatus)}
      ${OPEN.includes(l.orderStatus) ? `<button class="ghost sm" data-leg="${esc(l.legName)}" data-oid="${esc(o.orderId)}" data-lp="${l.price}">Change</button>` : ''}</div>`).join('') : '';
    return `<div class="item" style="cursor:default;align-items:flex-start"><div class="grow">
      <div class="name"><span class="pill ${o.transactionType === 'BUY' ? 'buy' : 'sell'}">${o.transactionType}</span> ${esc(o._name || o.tradingSymbol)} ${o._super ? '<span class="tag">SUPER</span>' : ''}</div>
      <div class="sub">${(o.filledQty || 0).toLocaleString('en-IN')}/${(o.quantity || 0).toLocaleString('en-IN')} · ${esc(priceTxt)} · ${esc(o.productType)} · ${t}
        ${o.averageTradedPrice ? ' · avg ' + px(o.averageTradedPrice) : ''}</div>
      ${o.omsErrorDescription && o.orderStatus === 'REJECTED' ? `<div class="sub down">${esc(o.omsErrorDescription)}</div>` : ''}
      ${legs}
      ${open || (o._super && OPEN.includes(o.orderStatus)) ? `<div class="ord-actions">${o._super ? '' : `<button data-mod="${esc(o.orderId)}">Modify</button>`}<button class="danger" data-cancel="${esc(o.orderId)}" data-super="${o._super ? 1 : ''}">Cancel</button></div>` : ''}
    </div><div class="right">${statusPill(o.orderStatus)}</div></div>`;
  }).join('');
}
$('#orders').onclick = async e => {
  const c = e.target.closest('[data-cancel]');
  if (c) {
    if (!confirm('Cancel this order?')) return;
    try { await api('/api/order/cancel', {order_id: c.dataset.cancel, super: !!c.dataset.super}); toast('Cancel sent', 'ok'); } catch (er) { toast(er.message, 'bad'); }
    return setTimeout(loadOrders, 500);
  }
  const m = e.target.closest('[data-mod]');
  if (m) return openModify(S.lastOrders.find(o => o.orderId === m.dataset.mod));
  const l = e.target.closest('[data-leg]');
  if (l) {
    const v = prompt(`New ${l.dataset.leg === 'TARGET_LEG' ? 'target' : 'stop-loss'} price`, l.dataset.lp);
    if (!v) return;
    const o = S.superOrders.find(x => x.orderId === l.dataset.oid);
    try { await api('/api/order/modify', {order_id: l.dataset.oid, super_leg: l.dataset.leg, price: parseFloat(v), tick: o?._tick}); toast('Change sent', 'ok'); } catch (er) { toast(er.message, 'bad'); }
    setTimeout(loadOrders, 500);
  }
};
function openModify(o) {
  if (!o) return;
  const lot = o._lot > 1 && !String(o.exchangeSegment).endsWith('_EQ') ? o._lot : 1;
  openSheet(`<div class="sh-head"><div><h2>Modify order</h2><div class="muted small">${esc(o.transactionType)} ${esc(o._name)} · #${esc(o.orderId)}</div></div><button class="sh-close">✕</button></div>
    <div class="seg" id="mdType">${[['LIMIT', 'Limit'], ['MARKET', 'Market'], ['STOP_LOSS', 'SL'], ['STOP_LOSS_MARKET', 'SL-M']].map(([v, l]) => `<button data-v="${v}" class="${o.orderType === v ? 'on' : ''}">${l}</button>`).join('')}</div>
    <div class="fields"><div class="field"><label>Quantity${lot > 1 ? ` (lot ${lot})` : ''}</label><input id="mdQty" inputmode="numeric" value="${o.quantity}"></div>
    <div class="field"><label>Price</label><input id="mdPrice" inputmode="decimal" value="${o.price || ''}"></div>
    <div class="field"><label>Trigger</label><input id="mdTrig" inputmode="decimal" value="${o.triggerPrice || ''}"></div></div>
    <div id="mdMsg"></div><button class="primary" id="mdGo" style="width:100%;margin-top:12px">Send change</button>`);
  let type = o.orderType;
  $('#mdType').onclick = e => { const b = e.target.closest('button'); if (!b) return; type = b.dataset.v; $$('#mdType button').forEach(x => x.classList.toggle('on', x === b)); };
  $('#mdGo').onclick = async () => {
    const qty = parseInt($('#mdQty').value);
    if (!qty || qty % lot) return $('#mdMsg').innerHTML = `<div class="badbox">Quantity must be a multiple of ${lot}.</div>`;
    try {
      await api('/api/order/modify', {order_id: o.orderId, type, qty, price: parseFloat($('#mdPrice').value) || 0, trigger: parseFloat($('#mdTrig').value) || 0,
        validity: o.validity, leg: o.legName || '', tick: o._tick});
      closeSheet(); toast('Change sent', 'ok'); setTimeout(loadOrders, 500);
    } catch (e) { $('#mdMsg').innerHTML = `<div class="badbox">${esc(e.message)}</div>`; }
  };
}

// ---- positions --------------------------------------------------------------------------------------------
async function loadPositions() {
  let r;
  try { r = await api('/api/positions'); } catch (e) { $('#positions').innerHTML = `<div class="item muted">${esc(e.message)}</div>`; return; }
  S.positions = r.positions;
  S.posKeys = r.positions.filter(p => +p.netQty).map(p => p._key);
  const open = r.positions.filter(p => +p.netQty), closed = r.positions.filter(p => !+p.netQty);
  let live = 0;
  r.positions.forEach(p => { live += (+p.realizedProfit || 0) + (+(p._unreal ?? p.unrealizedProfit) || 0); });
  const lim = +r.limits.daily_loss_limit || 0;
  $('#pnlCard').innerHTML = `<div class="muted small">Today's P&amp;L</div><div class="big ${cls(live)}">${inr(live)}</div>
    <div class="muted small">${open.length} open · ${closed.length} closed today</div>
    ${lim ? `<div class="meter"><i style="width:${Math.min(100, Math.max(0, -live / lim * 100))}%"></i></div>
      <div class="muted small">Daily loss limit ${inr(lim)}: ${live <= -lim ? '<b class="down">reached. Only closing orders allowed.</b>' : inr(lim + Math.min(0, live)) + ' left'}</div>` : ''}`;
  const row = p => {
    const net = +p.netQty, unreal = +(p._unreal ?? p.unrealizedProfit) || 0, pl = (+p.realizedProfit || 0) + unreal;
    const avg = net > 0 ? p.buyAvg : net < 0 ? p.sellAvg : null;
    return `<div class="item" style="cursor:default;align-items:flex-start"><div class="grow">
      <div class="name">${esc(p._name || p.tradingSymbol)} <span class="tag">${esc(p.productType)}</span></div>
      <div class="sub">${net ? `${net > 0 ? 'Long' : 'Short'} ${Math.abs(net).toLocaleString('en-IN')} @ ${px(avg)} · LTP <span data-q="${esc(p._key)}">${px(p._ltp)}</span>` :
        `Closed · bought ${(+p.buyQty).toLocaleString('en-IN')} @ ${px(p.buyAvg)}, sold @ ${px(p.sellAvg)}`}</div>
      ${net ? `<div class="ord-actions"><button data-exit="${esc(p._key)}" data-net="${net}" data-prod="${esc(p.productType)}">Exit</button>
        <button data-add="${esc(p._key)}" data-net="${net}" data-prod="${esc(p.productType)}">Add</button>
        <button data-conv="${esc(p._key)}">Convert</button></div>` : ''}
    </div><div class="right"><div class="ltp ${cls(pl)}">${inr(pl, 2)}</div>${net && p.realizedProfit ? `<div class="small muted">booked ${inr(p.realizedProfit)}</div>` : ''}</div></div>`;
  };
  $('#positions').innerHTML = r.positions.length ? [...open, ...closed].map(row).join('') : '<div class="item muted">No positions today.</div>';
  $('#exitAll').classList.toggle('hidden', !open.length);
}
$('#positions').onclick = async e => {
  const x = e.target.closest('[data-exit],[data-add]');
  if (x) {
    const net = +x.dataset.net, key = x.dataset.exit || x.dataset.add;
    const exit = !!x.dataset.exit;
    await ensureInfo(key);
    return openTicket(key, (net > 0) === exit ? 'SELL' : 'BUY', {qty: exit ? Math.abs(net) : undefined, product: x.dataset.prod, type: 'LIMIT', exit});
  }
  const c = e.target.closest('[data-conv]');
  if (c) {
    const p = S.positions.find(p => p._key === c.dataset.conv);
    const eq = String(p.exchangeSegment).endsWith('_EQ');
    const to = p.productType === 'INTRADAY' ? (eq ? 'CNC' : 'MARGIN') : 'INTRADAY';
    if (!confirm(`Convert ${Math.abs(p.netQty)} ${p._name} from ${p.productType} to ${to}?`)) return;
    try {
      await api('/api/position/convert', {fromProductType: p.productType, toProductType: to, exchangeSegment: p.exchangeSegment,
        positionType: +p.netQty > 0 ? 'LONG' : 'SHORT', securityId: String(p.securityId), convertQty: Math.abs(+p.netQty)});
      toast('Converted', 'ok'); loadPositions();
    } catch (er) { toast(er.message, 'bad'); }
  }
};
$('#exitAll').onclick = async () => {
  const v = prompt('This closes EVERY open position at market price and cancels open orders.\nType EXIT ALL to confirm.');
  if (v !== 'EXIT ALL') return;
  try { await api('/api/exit-all', {confirm: 'EXIT ALL'}); toast('Exit all sent', 'ok'); } catch (e) { toast(e.message, 'bad'); }
  setTimeout(() => { loadPositions(); loadOrders(); }, 800);
};

// ---- option chain -------------------------------------------------------------------------------------------
let chainList = null;
async function loadChain(first) {
  if (!chainList) {
    try { chainList = (await api('/api/underlyings')).list; } catch (e) { chainList = []; }
    $('#ul').innerHTML = chainList.map(u => `<option>${esc(u)}</option>`).join('');
    $('#ul').onchange = () => { S.chainUl = $('#ul').value; S.chainExp = ''; loadChain(true); };
  }
  if (!chainList.includes(S.chainUl) && chainList.length) S.chainUl = chainList[0];
  $('#ul').value = S.chainUl;
  let r;
  try { r = await api(`/api/chain?ul=${encodeURIComponent(S.chainUl)}&expiry=${encodeURIComponent(S.chainExp)}`); }
  catch (e) { $('#chainInfo').textContent = e.message; return; }
  if (r.error && !r.rows) { $('#chainInfo').textContent = r.error; $('#chain').innerHTML = ''; return; }
  S.chainExp = r.expiry;
  $('#expiries').innerHTML = (r.expiries || []).map(x => `<button data-e="${x}" class="${x === r.expiry ? 'on' : ''}">${new Date(x + 'T00:00').toLocaleDateString('en-IN', {day: 'numeric', month: 'short', year: x.slice(0, 4) !== String(new Date().getFullYear()) ? '2-digit' : undefined})}</button>`).join('');
  $('#expiries').onclick = e => { const b = e.target.closest('button'); if (b) { S.chainExp = b.dataset.e; loadChain(true); } };
  const days = Math.max(0, Math.round((new Date(r.expiry + 'T15:30') - new Date()) / 864e5));
  $('#chainInfo').innerHTML = `<span>Spot <b>${px(r.spot)}</b></span><span>PCR <b>${r.pcr ?? '–'}</b></span><span>Lot <b>${r.lot}</b></span><span>${days} day${days === 1 ? '' : 's'} to expiry</span>` +
    (r.ukey ? `<button class="ghost sm" data-k="${esc(r.ukey)}">Chart</button>` : '') + (r.futures?.[0] ? `<button class="ghost sm" data-k="${esc(r.futures[0])}">Futures</button>` : '') +
    (r.error ? `<span class="down">${esc(r.error)}</span>` : '');
  $('#chainInfo').onclick = e => { const b = e.target.closest('[data-k]'); if (b) openInstrument(b.dataset.k); };
  const rows = r.rows, spot = r.spot;
  let atm = null;
  if (spot) atm = rows.reduce((a, x) => (a === null || Math.abs(x.strike - spot) < Math.abs(a - spot) ? x.strike : a), null);
  const maxOi = Math.max(1, ...rows.map(x => Math.max(x.CE?.oi || 0, x.PE?.oi || 0)));
  const cell = (o, side) => {
    if (!o) return `<td class="${side}"></td><td class="${side} wide"></td><td class="${side} wide"></td><td class="${side}"></td>`;
    const bar = `<span class="bar" style="width:${Math.round((o.oi || 0) / maxOi * 40)}px;color:${side === 'ce' ? 'var(--down)' : 'var(--up)'}"></span>`;
    const chg = o.ltp && o.prev ? (o.ltp - o.prev) / o.prev * 100 : null;
    const oiCell = `<td class="${side}">${side === 'ce' ? bar : ''}${big(o.oi)}${side === 'pe' ? bar : ''}<div class="sm ${cls(o.chg_oi)}">${o.chg_oi === null ? '' : signed(o.chg_oi, big)}</div></td>`;
    const ivCell = `<td class="${side} wide sm">${o.iv ? Number(o.iv).toFixed(1) : '–'}</td>`;
    const dCell = `<td class="${side} wide sm">${o.delta !== null && o.delta !== undefined ? Number(o.delta).toFixed(2) : '–'}</td>`;
    const ltp = `<td class="${side} p" data-k="${esc(o.key)}">${px(o.ltp)}<div class="sm ${cls(chg)}">${chg === null ? '' : signed(chg, v => v.toFixed(1)) + '%'}</div></td>`;
    return side === 'ce' ? oiCell + ivCell + dCell + ltp : ltp + dCell + ivCell + oiCell;
  };
  $('#chain').innerHTML = `<thead><tr><th>OI (chg)</th><th class="wide">IV</th><th class="wide">Δ</th><th>CALL LTP</th><th>Strike</th><th>PUT LTP</th><th class="wide">Δ</th><th class="wide">IV</th><th>OI (chg)</th></tr></thead><tbody>` +
    rows.map(x => `<tr class="${spot && x.strike < spot ? 'itm-ce' : ''} ${spot && x.strike > spot ? 'itm-pe' : ''} ${x.strike === atm ? 'atm' : ''}">${cell(x.CE, 'ce')}<td class="k">${Number(x.strike).toLocaleString('en-IN')}</td>${cell(x.PE, 'pe')}</tr>`).join('') + '</tbody>';
  if (first && atm !== null) {
    const tr = $('#chain tr.atm');
    if (tr) { const w = $('.chain-wrap'); w.scrollTop = tr.offsetTop - w.clientHeight / 2 + 20; }
  }
}
$('#chain').onclick = e => { const c = e.target.closest('td.p[data-k]'); if (c) openInstrument(c.dataset.k); };

// ---- insights ------------------------------------------------------------------------------------------------
$('#insDays').onclick = e => { const b = e.target.closest('button'); if (!b) return; S.insDays = +b.dataset.d; $$('#insDays button').forEach(x => x.classList.toggle('on', x === b)); loadInsights(); };
$('#syncBtn').onclick = async () => { await api('/api/insights/sync', {}); toast('Importing your trades from Dhan…'); setTimeout(loadInsights, 2500); };
async function loadInsights() {
  let r;
  try { r = await api('/api/insights?days=' + S.insDays); } catch (e) { $('#insights').innerHTML = `<div class="badbox">${esc(e.message)}</div>`; return; }
  $('#syncNote').textContent = r.sync ? r.sync : r.fills ? `${r.fills.toLocaleString('en-IN')} fills from ${r.first} to ${r.last}` + (r.sync_at ? ` · updated ${r.sync_at}` : '') +
    (r.estimated ? ' · today\'s charges are estimates until Dhan\'s statement has them' : '') : '';
  if (r.sync) setTimeout(() => S.tab === 'insights' && loadInsights(), 3000);
  const s = r.summary;
  if (!s.trades) {
    $('#insights').innerHTML = `<div class="empty">No finished trades ${S.insDays ? 'in this period' : 'yet'}. ${r.fills ? '' : 'Tap “Import trades” to read your history from Dhan.'}</div>`;
    return;
  }
  const kpi = (l, v, sub, c) => `<div class="kpi"><div class="l">${l}</div><div class="v ${c || ''}">${v}</div>${sub ? `<div class="s">${sub}</div>` : ''}</div>`;
  const pf = s.profit_factor === null ? '–' : s.profit_factor >= 99 ? '∞' : s.profit_factor;
  const bad = r.habits.filter(h => !h.good), good = r.habits.filter(h => h.good);
  const today = r.today;
  $('#insights').innerHTML = `
    <div class="kpis">
      ${kpi('Net P&amp;L (after charges)', inrShort(s.net), `${s.trades} trades · gross ${inrShort(s.gross)}`, cls(s.net))}
      ${kpi('Win rate', s.win_rate + '%', `${s.wins} won · ${s.losses} lost`)}
      ${kpi('Average win / loss', `${inrShort(s.avg_win)} / ${inrShort(-s.avg_loss)}`, `per trade: ${inrShort(s.expectancy)}`)}
      ${kpi('Profit factor', pf, 'money won ÷ money lost')}
      ${kpi('Charges &amp; taxes', inrShort(s.charges), s.gross > 0 ? Math.round(s.charges / s.gross * 100) + '% of gross profit' : '')}
      ${kpi('Worst drawdown', inrShort(r.max_drawdown), 'from a peak, closing basis', 'down')}
    </div>
    <div class="card"><h3>Your P&amp;L over time</h3><canvas class="curve" id="curve"></canvas></div>
    ${today.summary.trades ? `<div class="card"><h3>Today's report card</h3>
      <div class="kv"><span>Trades</span><b>${today.summary.trades} (${today.summary.wins} won)</b></div>
      <div class="kv"><span>Net P&amp;L</span><b class="${cls(today.summary.net)}">${inr(today.summary.net)}</b></div>
      <div class="kv"><span>Charges</span><b>${inr(today.summary.charges)}</b></div>
      ${today.notes.length ? today.notes.map(n => `<div class="warnbox">${esc(n)}</div>`).join('') : '<div class="okbox">No rule breaks today.</div>'}</div>` : ''}
    ${bad.length ? `<div class="card"><h3>What's costing you money</h3>${bad.map(h => `<div class="habit"><div class="t"><span>${esc(h.title)}</span>${h.impact ? `<span class="down">${inrShort(h.impact)}</span>` : ''}</div><p>${esc(h.text)}</p></div>`).join('')}</div>` : ''}
    ${good.length ? `<div class="card"><h3>What works for you</h3>${good.map(h => `<div class="habit good"><div class="t"><span>${esc(h.title)}</span><span class="up">${inrShort(h.impact)}</span></div><p>${esc(h.text)}</p></div>`).join('')}</div>` : ''}
    <div class="grid2">
      ${barCard('By what you trade', r.by_category)}
      ${barCard('By time you entered', r.by_hour)}
      ${barCard('By day of the week', r.by_weekday)}
      ${barCard('By how long you held', r.by_hold)}
      ${barCard('Long vs short', r.by_side)}
      ${barCard('Best and worst symbols', r.by_symbol)}
    </div>
    <div class="card"><h3>Recent trades</h3><div class="list" style="border:0">${r.recent.slice(0, 30).map(t => `<div class="item" style="cursor:default;padding:8px 0">
      <div class="grow"><div class="name">${esc(t.sym)} <span class="tag">${esc(t.side === 'LONG' ? 'LONG' : 'SHORT')}</span>${t.avg_down ? '<span class="tag">AVG DOWN</span>' : ''}</div>
      <div class="sub">${esc(t.entry.slice(0, 16))} → ${esc(t.exit.slice(11, 16))} · ${t.qty.toLocaleString('en-IN')} qty · ${esc(t.cat)}</div></div>
      <div class="right"><div class="${cls(t.net)}">${inr(t.net)}</div><div class="small muted">charges ${inr(t.charges)}${t.est ? '*' : ''}</div></div></div>`).join('')}</div></div>`;
  drawCurve(r.curve);
}
function barCard(title, rows) {
  if (!rows || !rows.length) return '';
  const m = Math.max(1, ...rows.map(r => Math.abs(r.net)));
  return `<div class="card"><h3>${title}</h3><table class="bars">${rows.map(r => `<tr><td class="l">${esc(r.label)}<div class="small muted">${r.trades} · ${r.win_rate}% won</div></td>
    <td class="bar"><div class="wrap"><div class="half neg">${r.net < 0 ? `<div class="b" style="width:${Math.abs(r.net) / m * 100}%;background:var(--down)"></div>` : ''}</div>
    <div class="half">${r.net > 0 ? `<div class="b" style="width:${r.net / m * 100}%;background:var(--up)"></div>` : ''}</div></div></td>
    <td class="r ${cls(r.net)}">${inrShort(r.net)}</td></tr>`).join('')}</table></div>`;
}
function drawCurve(curve) {
  const cv = $('#curve'); if (!cv || !curve.length) return;
  const ctx = cv.getContext('2d'), dpr = window.devicePixelRatio || 1, W = cv.clientWidth, H = cv.clientHeight;
  cv.width = W * dpr; cv.height = H * dpr; ctx.scale(dpr, dpr);
  const vals = curve.map(c => c.total), hi = Math.max(0, ...vals), lo = Math.min(0, ...vals);
  const L = 60, R = 8, T = 10, B = 20;
  const x = i => L + (curve.length === 1 ? (W - L - R) / 2 : i / (curve.length - 1) * (W - L - R));
  const y = v => T + (hi - v) / ((hi - lo) || 1) * (H - T - B);
  ctx.font = '10px system-ui'; ctx.fillStyle = css('--muted'); ctx.strokeStyle = css('--line');
  [hi, 0, lo].forEach(v => { ctx.beginPath(); ctx.moveTo(L, y(v)); ctx.lineTo(W - R, y(v)); ctx.stroke(); ctx.fillText(inrShort(v), 4, y(v) + 3); });
  const bw = Math.max(1, (W - L - R) / curve.length * 0.6);
  const day = curve.map(c => c.day), dm = Math.max(1, ...day.map(Math.abs));
  curve.forEach((c, i) => { ctx.fillStyle = c.day >= 0 ? css('--upbg') : css('--downbg'); const h = Math.abs(c.day) / dm * (H - T - B) * 0.35; ctx.fillRect(x(i) - bw / 2, c.day >= 0 ? y(0) - h : y(0), bw, h); });
  ctx.lineWidth = 2; ctx.strokeStyle = vals[vals.length - 1] >= 0 ? css('--up') : css('--down');
  ctx.beginPath(); curve.forEach((c, i) => (i ? ctx.lineTo(x(i), y(c.total)) : ctx.moveTo(x(i), y(c.total)))); ctx.stroke();
  ctx.fillStyle = css('--muted'); ctx.fillText(curve[0].date, L, H - 4); ctx.textAlign = 'right'; ctx.fillText(curve[curve.length - 1].date, W - R, H - 4); ctx.textAlign = 'left';
}

// ---- account --------------------------------------------------------------------------------------------------
async function loadAccount() {
  const s = S.status || await api('/api/status');
  const el = $('#account');
  el.innerHTML = '<div class="muted">Loading…</div>';
  const [f, h, k, a] = await Promise.all([api('/api/funds').catch(e => ({_e: e.message})), api('/api/holdings').catch(e => ({_e: e.message, holdings: []})),
    api('/api/killswitch').catch(() => ({})), api('/api/audit').catch(() => ({log: []}))]);
  const kill = /ACTIVATE$/i.test(String(k.killSwitchStatus || '')) && !/DEACTIVATE/i.test(String(k.killSwitchStatus || ''));
  S.posKeys = (h.holdings || []).map(x => x._key).filter(Boolean);
  const lim = s.limits;
  let hv = 0, hc = 0;
  (h.holdings || []).forEach(x => { hc += x.totalQty * x.avgCostPrice; hv += x.totalQty * (x._ltp || x.avgCostPrice); });
  el.innerHTML = `
    <div class="section-title">Funds</div>
    ${f._e ? `<div class="badbox">${esc(f._e)}</div>` : `<div class="box">
      <div class="kv"><span>Available to trade</span><b>${inr(f.availabelBalance ?? f.availableBalance)}</b></div>
      <div class="kv"><span>Used margin</span><b>${inr(f.utilizedAmount)}</b></div>
      <div class="kv"><span>Opening balance</span><b>${inr(f.sodLimit)}</b></div>
      <div class="kv"><span>Collateral</span><b>${inr(f.collateralAmount)}</b></div>
      <div class="kv"><span>Withdrawable</span><b>${inr(f.withdrawableBalance)}</b></div></div>`}
    <div class="section-title">Holdings ${h.holdings?.length ? `<span class="muted">· ${inrShort(hv)} (${signed(hv - hc, inrShort)})</span>` : ''}</div>
    <div class="list">${(h.holdings || []).map(x => {
      const pl = x._ltp ? (x._ltp - x.avgCostPrice) * x.totalQty : null;
      return `<div class="item" data-key="${esc(x._key || '')}"><div class="grow"><div class="name">${esc(x.tradingSymbol)}</div>
      <div class="sub">${x.totalQty} @ ${px(x.avgCostPrice)} · LTP <span data-q="${esc(x._key || '')}">${px(x._ltp)}</span></div></div>
      <div class="right"><div class="${cls(pl)}">${pl === null ? '–' : inr(pl)}</div></div></div>`;
    }).join('') || '<div class="item muted">' + esc(h._e || 'No holdings.') + '</div>'}</div>
    <div class="section-title">Safety</div>
    <div class="box">
      <div class="formrow">
        <div class="field"><label>Daily loss limit (₹, 0 = off)</label><input id="lmLoss" inputmode="numeric" value="${lim.daily_loss_limit || 0}">
          <div class="hint">Past it, only orders that close a position go through.</div></div>
        <div class="field"><label>Largest single order (₹)</label><input id="lmMax" inputmode="numeric" value="${lim.max_order_value || 0}">
          <div class="hint">Bigger orders need an extra tick to send.</div></div>
        <div class="field"><label>Warn from this many lots</label><input id="lmLots" inputmode="numeric" value="${lim.lots_warn || 0}"></div>
        <div class="field"><label>Your max trades a day (insights)</label><input id="lmTrades" inputmode="numeric" value="${lim.max_trades_day || 5}"></div>
      </div>
      <button class="ghost" id="lmSave" style="margin-top:10px">Save limits</button>
    </div>
    <div class="box"><div class="kv"><span>Dhan kill switch</span><b class="${kill ? 'down' : 'up'}">${kill ? 'ON: trading stopped for today' : 'Off'}</b></div>
      <div class="muted small">Stops ALL trading on your Dhan account for the rest of the day (Dhan's own kill switch). Close positions first.</div>
      <button class="ghost ${kill ? '' : 'danger'}" id="killBtn" style="margin-top:8px">${kill ? 'Turn kill switch off' : 'Stop trading for today'}</button></div>
    <div class="section-title">Dhan login</div>
    <div class="box">
      <div class="kv"><span>Status</span><b>${s.connected ? `Connected · ${esc(s.client)}` : 'Not connected'}</b></div>
      ${s.token_expires ? `<div class="kv"><span>Token valid until</span><b>${esc(s.token_expires)}</b></div>` : ''}
      ${s.connected ? '<div class="muted small">Renewed automatically before it expires, so you paste it only once.</div>' : ''}
      ${s.token_note ? `<div class="muted small">${esc(s.token_note)}</div>` : ''}
      <div class="formrow" style="margin-top:10px"><div class="field"><label>Client ID</label><input id="dhId" autocomplete="off"></div>
      <div class="field"><label>Access token</label><input id="dhTok" type="password" autocomplete="off" placeholder="from web.dhan.co"></div></div>
      <button class="ghost" id="dhSave" style="margin-top:10px">Save &amp; test</button>
      <div class="muted small" style="margin-top:10px">Get them on web.dhan.co → My Profile → <b>Access DhanHQ APIs</b>. For orders, Dhan needs this server's IP set as your <b>static IP</b> there${s.ip ? `: <code>${esc(s.ip)}</code>` : ''}.</div>
    </div>
    <div class="section-title">Order log (this app)</div>
    <div class="box small" style="max-height:260px;overflow:auto">${(a.log || []).slice(0, 40).map(x => `<div class="kv"><span>${esc(x.at)} ${esc(x.event.replace('_', ' '))}</span><span>${esc(x.sym || x.order_id || '')}${x.error ? ' · ' + esc(x.error).slice(0, 80) : ''}</span></div>`).join('') || '<span class="muted">Nothing yet.</span>'}</div>
    <div class="section-title">App</div>
    <div class="box"><div class="kv"><span>Version</span><span>${esc(s.version)}</span></div>
      <div class="kv"><span>Instruments</span><span>${(s.instruments || 0).toLocaleString('en-IN')}</span></div>
      <div class="ord-actions"><button id="themeBtn">Light / dark</button><button id="logout" class="danger">Sign out</button></div></div>`;
  $('#lmSave').onclick = async () => {
    try { const r = await api('/api/settings/limits', {daily_loss_limit: +$('#lmLoss').value || 0, max_order_value: +$('#lmMax').value || 0, lots_warn: +$('#lmLots').value || 0, max_trades_day: +$('#lmTrades').value || 5});
      S.status.limits = r.limits; toast('Limits saved', 'ok'); } catch (e) { toast(e.message, 'bad'); }
  };
  $('#killBtn').onclick = async () => {
    if (!kill && prompt('Stop all trading on your Dhan account for the rest of today?\nType STOP to confirm.') !== 'STOP') return;
    try { await api('/api/killswitch', {on: !kill, confirm: 'STOP'}); toast(kill ? 'Kill switch off' : 'Trading stopped for today', 'ok'); loadAccount(); } catch (e) { toast(e.message, 'bad'); }
  };
  $('#dhSave').onclick = async () => {
    try { await api('/api/settings/dhan', {client_id: $('#dhId').value, token: $('#dhTok').value}); toast('Dhan connected', 'ok'); await loadStatus(); loadAccount(); } catch (e) { toast(e.message, 'bad', 8000); }
  };
  $('#logout').onclick = async () => { await api('/api/logout', {}); location.reload(); };
  $('#themeBtn').onclick = () => {
    const cur = document.documentElement.dataset.theme || (matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark');
    const next = cur === 'light' ? 'dark' : 'light';
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem('mt-theme', next); } catch (e) { /* private mode */ }
  };
  el.querySelectorAll('.item[data-key]').forEach(i => { if (i.dataset.key) i.onclick = () => openInstrument(i.dataset.key); });
}

// ---- start --------------------------------------------------------------------------------------------------
try { const th = localStorage.getItem('mt-theme'); if (th) document.documentElement.dataset.theme = th; } catch (e) { /* ignore */ }
(async function start() {
  await loadStatus();
  renderWlTabs(); renderWl();
  loadFunds(); loadOrders();
  setInterval(pollQuotes, 1000);
  setInterval(loadStatus, 15000);
  setInterval(loadFunds, 20000);
  setInterval(() => { if (document.hidden) return; if (S.tab === 'orders') loadOrders(); if (S.tab === 'positions') loadPositions(); }, 2500);
  setInterval(() => { if (!document.hidden && S.tab === 'chain') loadChain(false); }, 3500);
  setInterval(() => { if (!document.hidden && S.tab !== 'orders') loadOrders(); }, 10000);
  pollQuotes();
})();
