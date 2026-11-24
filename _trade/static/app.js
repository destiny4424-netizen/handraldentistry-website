'use strict';
/* Handral Trading Desk: the app in the browser. Talks only to its own server (/api/...), which talks to Dhan. */

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const css = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();

function pref(k, d) { try { const v = localStorage.getItem('mt-' + k); return v === null ? d : v === '1'; } catch (e) { return d; } }
function setPref(k, v) { try { localStorage.setItem('mt-' + k, v ? '1' : '0'); } catch (e) { /* private mode */ } }
const S = {liq: {}, wlGood: pref('wlGood', true), mkGood: pref('mkGood', true), status: null, tab: 'watch', wl: [], wlIdx: 0, info: {}, quotes: {}, editWl: false, sheetKey: null,
  orderFilter: 'open', insDays: 90, chainUl: 'NIFTY', chainExp: '', posKeys: [], lastOrders: [], ticket: null};

async function api(path, body, method) {
  if (lockShown) throw new Error('Locked');             // nothing is asked of the server until the PIN is entered
  const opt = {headers: {'X-Requested-With': 'meridian-trade'}};
  const pt = pinToken();
  if (pt) opt.headers['X-Pin-Token'] = pt;
  if (body !== undefined) { opt.method = method || 'POST'; opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
  const r = await fetch(path, opt);
  if (r.status === 401) { location.reload(); throw new Error('Signed out'); }
  if (r.status === 423) { showLock(); throw new Error('Locked'); }
  let j = {};
  try { j = await r.json(); } catch (e) { j = {error: 'The server answered oddly (' + r.status + ')'}; }
  if (!r.ok) { const e = new Error(j.error || ('Error ' + r.status)); e.data = j; e.status = r.status; throw e; }
  return j;
}

// ---- PIN lock --------------------------------------------------------------------------------------------
const LOCK_AFTER_MIN = 5;                                   // background longer than this: ask for the PIN again
function pinToken() {
  try { const t = JSON.parse(sessionStorage.getItem('mt-pin') || 'null'); return t && t.exp * 1000 > Date.now() ? t.token : ''; }
  catch (e) { return ''; }
}
function savePinToken(token, exp) { try { sessionStorage.setItem('mt-pin', JSON.stringify({token, exp})); } catch (e) { /* private mode */ } }
function dropPinToken() { try { sessionStorage.removeItem('mt-pin'); } catch (e) { /* ignore */ } }
let lockShown = false;
function showLock() {
  if (lockShown) return;
  lockShown = true;
  const el = document.createElement('div');
  el.id = 'lock';
  el.innerHTML = `<div class="lock-box"><div class="brand big"><span class="logo">◆</span> Handral <b>Trading Desk</b></div>
    <p class="muted">Enter your PIN</p>
    <input id="pinIn" type="password" inputmode="numeric" pattern="[0-9]*" maxlength="8" autocomplete="off">
    <button class="primary" id="pinGo">Unlock</button><p id="pinErr" class="error"></p>
    <button class="ghost sm" id="pinForgot">Forgot PIN?</button></div>`;
  document.body.appendChild(el);
  const go = async () => {
    try {
      const r = await fetch('/api/pin/unlock', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Requested-With': 'meridian-trade'},
        body: JSON.stringify({pin: $('#pinIn').value})});
      const j2 = await r.json();
      if (!r.ok) { $('#pinErr').textContent = j2.error || 'Wrong PIN'; $('#pinIn').value = ''; return; }
      savePinToken(j2.token, j2.exp);
      el.remove(); lockShown = false;
      refreshAll();
    } catch (e) { $('#pinErr').textContent = e.message; }
  };
  $('#pinGo').onclick = go;
  $('#pinIn').onkeydown = e => { if (e.key === 'Enter') go(); };
  $('#pinForgot').onclick = async () => {
    const pw = prompt('Enter the app password (the one you sign in with). It clears the PIN; set a new one under Account.');
    if (!pw) return;
    const r = await fetch('/api/pin/reset', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Requested-With': 'meridian-trade'},
      body: JSON.stringify({password: pw})});
    if (r.ok) { el.remove(); lockShown = false; refreshAll(); toast('PIN cleared. Set a new one under Account.', 'ok', 6000); }
    else $('#pinErr').textContent = (await r.json()).error || 'Wrong password';
  };
  setTimeout(() => $('#pinIn')?.focus(), 50);
}
let hiddenAt = 0;
document.addEventListener('visibilitychange', () => {
  if (document.hidden) { hiddenAt = Date.now(); return; }
  if (hiddenAt && Date.now() - hiddenAt > LOCK_AFTER_MIN * 60000 && S.pinSet) { dropPinToken(); showLock(); }
});
function refreshAll() {
  loadStatus(); loadFunds(); loadOrders(); loadPlan();
  if (S.tab === 'positions') loadPositions();
  if (S.tab === 'account') loadAccount();
  if (S.tab === 'auto') loadAuto();
  if (S.tab === 'insights') loadInsights();
}

// ---- phone notifications ---------------------------------------------------------------------------------
function isStandalone() { return window.navigator.standalone === true || matchMedia('(display-mode: standalone)').matches; }
function isIOS() { return /iPhone|iPad|iPod/.test(navigator.userAgent); }
async function swReg() {
  if (!('serviceWorker' in navigator)) return null;
  try { return await navigator.serviceWorker.register('/sw.js'); } catch (e) { return null; }
}
function keyBytes(b64) {
  const s = atob(b64.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - b64.length % 4) % 4));
  return Uint8Array.from(s, c => c.charCodeAt(0));
}
async function enablePush() {
  const info = S.pushInfo || await api('/api/push');
  if (!info.available) return toast('Notifications aren\'t available on the server yet (it installs what it needs within a few minutes).', 'bad', 7000);
  if (isIOS() && !isStandalone()) return toast('On iPhone: Share → Add to Home Screen, open the app from that icon, then turn notifications on.', 'bad', 9000);
  if (!('Notification' in window) || !('PushManager' in window)) return toast('This browser can\'t receive notifications.', 'bad');
  const perm = await Notification.requestPermission();
  if (perm !== 'granted') return toast('Notifications are blocked. Allow them in Settings → Notifications → Handral Desk.', 'bad', 8000);
  const reg = await swReg();
  if (!reg) return toast('Couldn\'t start the notification service.', 'bad');
  await navigator.serviceWorker.ready;
  let sub = await reg.pushManager.getSubscription();
  if (!sub) sub = await reg.pushManager.subscribe({userVisibleOnly: true, applicationServerKey: keyBytes(info.key)});
  await api('/api/push/subscribe', {subscription: sub.toJSON(), device: (isIOS() ? 'iPhone' : navigator.platform || 'This device')});
  toast('Notifications on. A welcome alert is on its way.', 'ok', 6000);
  loadAccount();
}
async function disablePush() {
  const reg = await swReg();
  const sub = reg && await reg.pushManager.getSubscription();
  if (sub) { await api('/api/push/unsubscribe', {endpoint: sub.endpoint}); await sub.unsubscribe(); }
  toast('Notifications off on this device', 'ok');
  loadAccount();
}
async function thisDeviceSubscribed() {
  try { const reg = await swReg(); const sub = reg && await reg.pushManager.getSubscription(); return sub ? sub.endpoint : ''; }
  catch (e) { return ''; }
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
  if (t === 'watch') loadPlan();
  if (t === 'positions') loadPositions();
  if (t === 'chain') loadChain(true);
  if (t === 'insights') loadInsights();
  if (t === 'account') loadAccount();
  if (t === 'auto') loadAuto();
  if (t === 'market') loadMarket();
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
    const r = await api('/api/quotes?keys=' + encodeURIComponent(keys.join(',')) + (need.length ? '&info=1' : '') + (S.tab === 'watch' ? '&liq=1' : ''));
    Object.assign(S.info, r.info || {});
    if (r.liq && Object.keys(r.liq).length) {
      const changed = Object.keys(r.liq).some(k => (S.liq[k] || {}).good !== r.liq[k].good);
      Object.assign(S.liq, r.liq);
      if (changed && S.tab === 'watch') renderWl();
    }
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
  $('#wlFilter').classList.toggle('on', S.wlGood);
  $('#wlFilter').textContent = (S.wlGood ? '✓ ' : '') + 'F&O · good volume';
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
  if (emptyList) { el.innerHTML = ''; $('#wlHidden').innerHTML = ''; return; }
  const shown = S.wlGood ? w.keys.filter(k => !S.liq[k] || S.liq[k].good) : w.keys;
  const hidden = w.keys.length - shown.length;
  $('#wlHidden').innerHTML = hidden ? `${hidden} hidden: not F&amp;O, or low volume today. <button class="ghost sm" id="wlShowAll">Show all</button>` : '';
  if ($('#wlShowAll')) $('#wlShowAll').onclick = () => { S.wlGood = false; setPref('wlGood', false); renderWlTabs(); renderWl(); };
  el.innerHTML = shown.map(k => {
    const it = S.info[k], q = S.quotes[k], lq = S.liq[k] || {};
    const lqTags = lq.exempt ? '' : (lq.fno ? '<span class="tag fno">F&amp;O</span>' : '') +
      (lq.rvol ? `<span class="tag">${lq.rvol}× vol</span>` : '') + (lq.good === false ? '<span class="tag low">low vol</span>' : '');
    return `<div class="item" data-key="${esc(k)}">
      <div class="grow"><div class="name">${esc(shortName(it) || k)} ${kindTag(it)}${lqTags}</div>
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
$('#wlFilter').onclick = () => { S.wlGood = !S.wlGood; setPref('wlGood', S.wlGood); renderWlTabs(); renderWl(); };
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
      <div class="grow"><div class="name">${esc(shortName(it))} ${kindTag(it)}${it.fno ? '<span class="tag fno">F&amp;O</span>' : ''}</div><div class="sub">${esc(it.name)}${it.lot > 1 ? ' · lot ' + it.lot : ''}</div></div>
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
  if (!preset.exit) loadTicketRules(S.ticket);
}
async function loadTicketRules(t) {
  let lv;
  try { lv = await api('/api/rules/levels?key=' + encodeURIComponent(t.key)); } catch (e) { return; }
  if (S.ticket !== t || !lv.cfg || !lv.cfg.on) return;
  readTicket();
  t.rules = lv;
  t.superOn = true;
  if (!['LIMIT', 'MARKET'].includes(t.type)) t.type = 'LIMIT';
  t.confirm = lv.cfg.confirm_default;
  const near = (lv.levels || []).filter(x => x.near);
  t.level = near.length ? near[0].id : '';
  renderTicket();
}
function rulesBox(t) {
  const lv = t.rules, c = lv.cfg;
  if (lv.error) return `<div class="box rules"><b>Rules mode</b><div class="badbox">${esc(lv.error)}</div></div>`;
  const near = (lv.levels || []).filter(x => x.near);
  const maxRisk = c.capital * c.risk_pct / 100;
  return `<div class="box rules"><div class="rtitle">Rules mode</div>
    <div class="rstep"><b>① Level</b> <span class="muted small">on ${esc(lv.under)}${lv.on_underlying ? ' (the underlying)' : ''} · ${px(lv.ltp)} · near = within ${px(lv.tol)}</span></div>
    <div class="chips lvl" id="tkLevels">${(lv.levels || []).slice(0, 8).map(x => `<button data-lv="${esc(x.id)}" class="${x.id === t.level ? 'on' : ''}" ${x.near ? '' : 'disabled'} title="${esc(x.detail || x.name)}">
      ${esc(x.name)} <b>${px(x.price)}</b> <span class="small">${x.dist_pct > 0 ? '+' : ''}${x.dist_pct}%</span></button>`).join('')}</div>
    ${near.length ? '' : '<div class="badbox">No level near the price: no trade. Wait for price to come to one of these.</div>'}
    <label class="toggle"><input type="checkbox" id="tkConfirm" ${t.confirm ? 'checked' : ''}> <span><b>② Wait for the reaction:</b> send only after a 15-minute candle touches the level and closes back beyond it</span></label>
    <div class="rstep"><b>③ Risk first:</b> <span id="tkRisk" class="small">set your stop-loss below; the size is worked out from it (max ₹${Math.round(maxRisk).toLocaleString('en-IN')} at risk)</span></div>
  </div>`;
}
function renderTicket() {
  const t = S.ticket, it = t.it;
  const products = t.eq ? [['INTRADAY', 'Intraday'], ['CNC', 'Delivery'], ['MTF', 'MTF']] : [['INTRADAY', 'Intraday'], ['MARGIN', 'Carry (NRML)']];
  const types = t.rules ? [['LIMIT', 'Limit'], ['MARKET', 'Market']] : [['LIMIT', 'Limit'], ['MARKET', 'Market'], ['STOP_LOSS', 'SL'], ['STOP_LOSS_MARKET', 'SL-M']];
  openSheet(`<div class="ticket">
    <div class="sh-head"><div><h2>${esc(shortName(it))} ${kindTag(it)}</h2>
      <div class="muted small"><span id="tkLtp">${px(S.quotes[t.key]?.ltp)}</span> · ${esc(it.name)}</div></div>
      <button class="sh-close" aria-label="Close">✕</button></div>
    ${t.exit ? '<div class="okbox">Closing your position. Check the quantity and price, then review.</div>' : ''}
    ${t.rules ? rulesBox(t) : ''}
    <div class="side"><button data-side="BUY" class="buy ${t.side === 'BUY' ? 'on' : ''}">BUY</button><button data-side="SELL" class="sell ${t.side === 'SELL' ? 'on' : ''}">SELL</button></div>
    <div class="seg" id="tkProd">${products.map(([v, l]) => `<button data-v="${v}" class="${t.product === v ? 'on' : ''}">${l}</button>`).join('')}</div>
    <div class="seg" id="tkType">${types.map(([v, l]) => `<button data-v="${v}" class="${t.type === v ? 'on' : ''}">${l}</button>`).join('')}</div>
    <div class="fields">
      <div class="field"><label>${t.lot > 1 || it.seg === 'MCX_COMM' ? 'Lots' : 'Quantity'}</label>
        <div class="stepper"><button data-step="-1">−</button><input id="tkLots" inputmode="numeric" value="${t.lots}"><button data-step="1">＋</button></div>
        <div class="hint" id="tkQtyHint"></div></div>
      <div class="field" id="fPrice"><label>Price</label><input id="tkPrice" inputmode="decimal" value="${t.price === '' ? '' : px(t.price).replace(/,/g, '')}">
        <div class="hint">Tick ₹${it.tick}</div></div>
      <div class="field" id="fTrig"><label>Trigger price</label><input id="tkTrig" inputmode="decimal" value="${t.trigger}"></div>
    </div>
    <label class="toggle ${t.rules ? 'hidden' : ''}"><input type="checkbox" id="tkSuper" ${t.superOn ? 'checked' : ''}> Add target &amp; stop-loss (Super order)</label>
    <div id="superBox" class="fields ${t.superOn ? '' : 'hidden'}">
      <div class="field"><label>Stop-loss price${t.rules ? ' <b class="down">required</b>' : ''}</label><input id="tkSl" inputmode="decimal" value="${t.sl}"></div>
      <div class="field"><label>Target price${t.rules ? ' (optional)' : ''}</label><input id="tkTgt" inputmode="decimal" value="${t.target}"></div>
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
  body.querySelectorAll('[data-step]').forEach(b => b.onclick = () => { readTicket(); t.lotsTouched = true; t.lots = Math.max(1, (parseInt(t.lots) || 1) + +b.dataset.step); $('#tkLots').value = t.lots; syncTicketFields(); });
  if ($('#tkLevels')) $('#tkLevels').onclick = e => { const b = e.target.closest('button[data-lv]'); if (!b || b.disabled) return; readTicket(); t.level = b.dataset.lv; renderTicket(); };
  if ($('#tkConfirm')) $('#tkConfirm').onchange = () => { t.confirm = $('#tkConfirm').checked; };
  $('#tkSuper').onchange = () => { readTicket(); t.superOn = $('#tkSuper').checked; if (t.superOn && !['LIMIT', 'MARKET'].includes(t.type)) t.type = 'LIMIT'; renderTicket(); };
  ['tkLots', 'tkPrice', 'tkTrig', 'tkTgt', 'tkSl'].forEach(id => { const el = $('#' + id); if (el) el.oninput = () => { if (id === 'tkPrice') t.priceTouched = true; if (id === 'tkLots') t.lotsTouched = true; readTicket(); syncTicketFields(); }; });
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
  const p = parseFloat(t.price) || S.quotes[t.key]?.ltp || 0;
  if (t.rules && t.rules.cfg && $('#tkRisk')) {
    const c = t.rules.cfg, sl = parseFloat(t.sl) || 0, entry = t.type === 'MARKET' ? (S.quotes[t.key]?.ltp || p) : p;
    const maxRisk = c.capital * c.risk_pct / 100;
    if (sl && entry && Math.abs(entry - sl) > 0) {
      const mult = t.it.mult || 1;
      const maxLots = Math.floor(Math.floor(maxRisk / (Math.abs(entry - sl) * mult)) / t.lot);
      t.maxLots = maxLots;
      if (!t.lotsTouched || (parseInt(t.lots) || 0) > maxLots) { t.lots = Math.max(0, maxLots); $('#tkLots').value = t.lots; }
      const risk = (parseInt(t.lots) || 0) * t.lot * Math.abs(entry - sl) * mult;
      $('#tkRisk').innerHTML = maxLots < 1 ? `<span class="down">Even ${t.lot > 1 ? '1 lot' : '1 share'} risks more than ₹${Math.round(maxRisk).toLocaleString('en-IN')} with this stop. Use a closer stop or skip.</span>` :
        `risk <b>${inr(risk)}</b> of ₹${Math.round(maxRisk).toLocaleString('en-IN')} allowed · at most ${(maxLots * t.lot).toLocaleString('en-IN')} ${t.lot > 1 ? 'units' : 'shares'}`;
    } else {
      $('#tkRisk').textContent = `set your stop-loss below; the size is worked out from it (max ₹${Math.round(maxRisk).toLocaleString('en-IN')} at risk)`;
    }
  }
  const qty = (parseInt(t.lots) || 0) * t.lot;
  const m = t.it.seg === 'MCX_COMM' ? (t.it.mult || 0) : 1;
  $('#tkQtyHint').textContent = t.it.seg === 'MCX_COMM'
    ? (m ? `${qty} lot${qty === 1 ? '' : 's'} of ${t.it.contract} · ₹${m.toLocaleString('en-IN')} per ₹1 move · ≈ ${inrShort(qty * p * m)}` : 'Contract size unknown: use Dhan\'s app')
    : (t.lot > 1 ? `${qty.toLocaleString('en-IN')} units · ` : '') + (p ? `≈ ${inrShort(qty * p)}` : '');
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
    super: t.superOn ? {target: parseFloat(t.target) || 0, sl: parseFloat(t.sl) || 0, trail: parseFloat(t.trail) || 0} : null,
    level: t.rules ? t.level : undefined, confirm: !!(t.rules && t.confirm)}, extra);
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
      <div class="kv"><span>Quantity</span><b>${t.it.seg === 'MCX_COMM' ? `${qty} lot${qty === 1 ? '' : 's'} (each ${esc(t.it.contract || '?')})` :
        qty.toLocaleString('en-IN') + (t.lot > 1 ? ` (${t.lots} lot${t.lots > 1 ? 's' : ''} × ${t.lot})` : '')}</b></div>
      <div class="kv"><span>Price</span><b>${priceTxt}</b></div>
      <div class="kv"><span>Order</span><b>${typeName} · ${prodName}${t.validity === 'IOC' ? ' · IOC' : ''}${t.amo ? ' · AMO' : ''}</b></div>
      <div class="kv"><span>Order value</span><b>${inr(r.value)}</b></div>
      ${t.superOn ? `<div class="kv"><span>Target / Stop-loss</span><b>${t.target ? '₹' + px(t.target) : '–'} / ${t.sl ? '₹' + px(t.sl) : '–'}${t.trail ? ' · trail ₹' + t.trail : ''}</b></div>` : ''}
      ${r.risk !== undefined ? `<div class="kv"><span>Money at risk to stop</span><b class="down">${inr(r.risk)}</b></div>` : ''}
      ${r.reward !== undefined ? `<div class="kv"><span>Profit at target</span><b class="up">${inr(r.reward)}${r.risk ? ` · ${(r.reward / r.risk).toFixed(1)}R` : ''}</b></div>` : ''}
    </div>
    ${r.rules && r.rules.rules && r.rules.level ? `<div class="okbox">✓ Level: ${esc(r.rules.level.name)} ${px(r.rules.level.price)} · ✓ Stop-loss set · ✓ Size within ₹${Math.round(r.rules.risk_allowed).toLocaleString('en-IN')} risk
      ${t.confirm ? '<br>② It waits at the server for a 15-minute candle to react at the level, then sends at market. Expires at 3 PM.' : ''}</div>` : ''}
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
      <button class="go ${t.side === 'BUY' ? 'b-buy' : 'b-sell'}" id="cfGo" ${r.needs_big_ok ? 'disabled' : ''}>${r.rules && r.rules.rules && t.confirm && r.opening ? 'Wait for confirmation' : `Place ${t.side} order`}</button></div>
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
    if (r.armed) { closeSheet(); toast(r.message, 'ok', 8000); showTab('orders'); return; }
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
async function loadArmed() {
  let r;
  try { r = await api('/api/rules'); } catch (e) { return; }
  const wait = r.armed.filter(a => a.status === 'waiting'), done = r.armed.filter(a => a.status !== 'waiting');
  $('#armed').innerHTML = wait.length || done.length ? `<div class="section-title">Waiting for confirmation (rule ②)</div><div class="list">` +
    wait.map(a => `<div class="item" style="cursor:default;align-items:flex-start"><div class="grow">
      <div class="name"><span class="pill ${a.side === 'BUY' ? 'buy' : 'sell'}">${a.side}</span> ${esc(a.name)}</div>
      <div class="sub">${a.qty.toLocaleString('en-IN')} · stop ${px(a.stop)} · at ${esc(a.level.name)} ${px(a.level.price)} on ${esc(a.under)} · since ${esc(a.at)}</div>
      ${a.note ? `<div class="sub">${esc(a.note)}</div>` : ''}
      <div class="ord-actions"><button class="danger" data-unarm="${esc(a.id)}">Cancel</button></div></div><div class="right"><span class="pill warn">WAITING</span></div></div>`).join('') +
    done.slice(-5).map(a => `<div class="item" style="cursor:default"><div class="grow"><div class="name">${esc(a.name)} <span class="tag">${esc(a.status.toUpperCase())}</span></div>
      <div class="sub">${esc(a.note || '')}</div></div></div>`).join('') + '</div>' : '';
}
$('#armed').onclick = async e => {
  const b = e.target.closest('[data-unarm]'); if (!b) return;
  try { await api('/api/rules/cancel', {id: b.dataset.unarm}); toast('Cancelled', 'ok'); } catch (er) { toast(er.message, 'bad'); }
  loadArmed();
};
async function loadOrders() {
  if (S.tab === 'orders') loadArmed();
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
  const live = +r.pnl || 0;                                 // Dhan's own figure (booked + open)
  const lim = +r.limits.daily_loss_limit || 0;
  $('#pnlCard').innerHTML = `<div class="muted small">Today's P&amp;L</div><div class="big ${cls(live)}">${inr(live, 2)}</div>
    <div class="muted small">${open.length} open · ${closed.length} closed today</div>
    ${lim ? `<div class="meter"><i style="width:${Math.min(100, Math.max(0, -live / lim * 100))}%"></i></div>
      <div class="muted small">Daily loss limit ${inr(lim, 2)}: ${live <= -lim ? '<b class="down">reached. Only closing orders allowed.</b>' : inr(lim + Math.min(0, live), 2) + ' left'}</div>` : ''}`;
  const row = p => {
    const net = +p.netQty, unreal = +p.unrealizedProfit || 0, pl = (+p.realizedProfit || 0) + unreal;
    const avg = net > 0 ? p.buyAvg : net < 0 ? p.sellAvg : null;
    return `<div class="item" style="cursor:default;align-items:flex-start"><div class="grow">
      <div class="name">${esc(p._name || p.tradingSymbol)} <span class="tag">${esc(p.productType)}</span></div>
      <div class="sub">${net ? `${net > 0 ? 'Long' : 'Short'} ${Math.abs(net).toLocaleString('en-IN')} @ ₹${px(avg)} · LTP ₹<span data-q="${esc(p._key)}">${px(p._ltp)}</span>` :
        `Closed · bought ${(+p.buyQty).toLocaleString('en-IN')} @ ₹${px(p.buyAvg)}, sold @ ₹${px(p.sellAvg)}`}</div>
      ${net ? `<div class="ord-actions"><button data-exit="${esc(p._key)}" data-net="${net}" data-prod="${esc(p.productType)}">Exit</button>
        <button data-add="${esc(p._key)}" data-net="${net}" data-prod="${esc(p.productType)}">Add</button>
        <button data-conv="${esc(p._key)}">Convert</button></div>` : ''}
    </div><div class="right"><div class="ltp ${cls(pl)}">${inr(pl, 2)}</div>${net && p.realizedProfit ? `<div class="small muted">booked ${inr(p.realizedProfit, 2)}</div>` : ''}</div></div>`;
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
function rulesSettings() {
  const r = S.rulesCfg || {}, c = r.cfg || {}, dis = r.locked ? 'disabled' : '';
  return `<label class="toggle"><input type="checkbox" id="ruOn" ${c.on ? 'checked' : ''} ${dis}> <span><b>Rules mode on</b>: every order that opens a position needs a level, a stop-loss and a size within your risk</span></label>
    <div class="formrow"><div class="field"><label>Capital (₹)</label><input id="ruCap" inputmode="numeric" value="${c.capital ?? ''}" ${dis}></div>
      <div class="field"><label>Risk per trade (% of capital)</label><input id="ruRisk" inputmode="decimal" value="${c.risk_pct ?? ''}" ${dis}></div>
      <div class="field"><label>"Near a level" = within this × daily ATR</label><input id="ruNear" inputmode="decimal" value="${c.near_atr ?? ''}" ${dis}></div></div>
    <label class="toggle"><input type="checkbox" id="ruConfirm" ${c.confirm_default ? 'checked' : ''} ${dis}> <span>Wait for the 15-minute reaction by default</span></label>
    <div class="muted small">Trades a day: set under Safety (“Your max trades a day”). ${r.locked ? '<b>Locked while the market is open.</b>' : ''}</div>
    <button class="ghost" id="ruSave" style="margin-top:8px" ${dis}>Save rules</button>`;
}
function bindRulesSettings() {
  if (!$('#ruSave')) return;
  $('#ruSave').onclick = async () => {
    try { const r = await api('/api/rules/settings', {on: $('#ruOn').checked, capital: +$('#ruCap').value, risk_pct: +$('#ruRisk').value,
      near_atr: +$('#ruNear').value, confirm_default: $('#ruConfirm').checked}); S.rulesCfg.cfg = r.cfg; toast('Rules saved', 'ok'); }
    catch (e) { toast(e.message, 'bad'); }
  };
}
async function renderPush() {
  const box = $('#pushBox'); if (!box) return;
  let info;
  try { info = await api('/api/push'); } catch (e) { box.innerHTML = `<div class="badbox">${esc(e.message)}</div>`; return; }
  S.pushInfo = info;
  const mine = await thisDeviceSubscribed();
  const here = info.phones.some(p => p.endpoint === mine);
  const kinds = info.settings.kinds;
  const iosHint = isIOS() && !isStandalone() ? '<div class="warnbox">On iPhone, notifications work only in the home-screen app: Share → Add to Home Screen, then open it from that icon and turn them on there.</div>' : '';
  box.innerHTML = `${!info.available ? '<div class="warnbox">Not available on the server yet: it installs what it needs on its next update (within a few minutes).</div>' : ''}
    ${iosHint}
    <div class="kv"><span>This device</span><b class="${here ? 'up' : ''}">${here ? 'On' : 'Off'}</b></div>
    <div class="kv"><span>Phones getting alerts</span><b>${info.phones.length}</b></div>
    <div class="ord-actions">${here ? '<button id="pushOff">Turn off here</button><button id="pushTest">Send a test</button>' :
      `<button id="pushOn" class="primary" style="padding:6px 14px" ${info.available ? '' : 'disabled'}>Turn on notifications</button>${info.phones.length ? '<button id="pushTest">Send a test</button>' : ''}`}</div>
    <div class="section-title" style="margin-top:12px">Tell me about</div>
    ${Object.entries(info.kinds).map(([k, l]) => `<label class="toggle"><input type="checkbox" data-kind="${k}" ${kinds.includes(k) ? 'checked' : ''}> ${esc(l)}</label>`).join('')}
    <div class="field"><label>Loss alert every (₹)</label><input id="lossStep" inputmode="numeric" value="${info.settings.loss_step}"></div>
    <button class="ghost" id="alSave" style="margin-top:8px">Save</button>
    ${info.history.length ? `<details style="margin-top:10px"><summary class="muted small">Last alerts</summary>${info.history.slice(0, 15).map(h =>
      `<div class="kv small"><span>${esc(h.at)}</span><span style="text-align:right;max-width:72%"><b>${esc(h.title)}</b><br>${esc(h.body)}</span></div>`).join('')}</details>` : ''}
    ${info.log.length ? `<details><summary class="muted small">Delivery log</summary>${info.log.map(l => `<div class="small ${l[1] ? '' : 'down'}">${esc(l[0])} · ${esc(l[2])}</div>`).join('')}</details>` : ''}`;
  if ($('#pushOn')) $('#pushOn').onclick = () => enablePush().catch(e => toast(e.message, 'bad', 7000));
  if ($('#pushOff')) $('#pushOff').onclick = () => disablePush().catch(e => toast(e.message, 'bad'));
  if ($('#pushTest')) $('#pushTest').onclick = async () => { try { await api('/api/push/test', {}); toast('Test sent', 'ok'); setTimeout(renderPush, 3000); } catch (e) { toast(e.message, 'bad'); } };
  $('#alSave').onclick = async () => {
    try { await api('/api/alerts/settings', {kinds: $$('#pushBox [data-kind]').filter(x => x.checked).map(x => x.dataset.kind), loss_step: +$('#lossStep').value});
      toast('Saved', 'ok'); } catch (e) { toast(e.message, 'bad'); }
  };
}
async function loadAccount() {
  try { S.rulesCfg = await api('/api/rules'); } catch (e) { S.rulesCfg = {}; }
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
    <div class="section-title">Phone notifications</div>
    <div class="box" id="pushBox"><span class="muted small">Loading…</span></div>
    <div class="section-title">App lock (PIN)</div>
    <div class="box" id="pinBox">${S.pinSet ? `<div class="kv"><span>PIN</span><b class="up">On</b></div>
      <div class="muted small">Asked when the app opens, and again after ${LOCK_AFTER_MIN} minutes in the background.</div>
      <div class="ord-actions"><button id="pinChange">Change PIN</button><button id="pinOff" class="danger">Turn off</button></div>` :
      `<div class="muted small">A 4–8 digit PIN, asked when the app opens and after ${LOCK_AFTER_MIN} minutes in the background, so nobody holding your phone can trade.</div>
      <div class="ord-actions"><button id="pinOn">Set a PIN</button></div>`}</div>
    <div class="section-title">Funds</div>
    ${f._e ? `<div class="badbox">${esc(f._e)}</div>` : `<div class="box">
      <div class="kv"><span>Available to trade</span><b>${inr(f.availabelBalance ?? f.availableBalance)}</b></div>
      <div class="kv"><span>Used margin</span><b>${inr(f.utilizedAmount)}</b></div>
      <div class="kv"><span>Opening balance</span><b>${inr(f.sodLimit)}</b></div>
      <div class="kv"><span>Collateral</span><b>${inr(f.collateralAmount)}</b></div>
      <div class="kv"><span>Withdrawable</span><b>${inr(f.withdrawableBalance)}</b></div></div>`}
    <div class="section-title">Holdings ${h.holdings?.length ? `<span class="muted">· ${inr(hv, 2)} (${hv - hc >= 0 ? '+' : ''}${inr(hv - hc, 2)})</span>` : ''}</div>
    <div class="list">${(h.holdings || []).map(x => {
      const pl = x._ltp ? (x._ltp - x.avgCostPrice) * x.totalQty : null;
      return `<div class="item" data-key="${esc(x._key || '')}"><div class="grow"><div class="name">${esc(x.tradingSymbol)}</div>
      <div class="sub">${x.totalQty} @ ₹${px(x.avgCostPrice)} · LTP ₹<span data-q="${esc(x._key || '')}">${px(x._ltp)}</span></div></div>
      <div class="right"><div class="${cls(pl)}">${pl === null ? '–' : inr(pl, 2)}</div></div></div>`;
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
    <div class="section-title">Rules mode (your manual trades)</div>
    <div class="box">${rulesSettings()}</div>
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
  bindRulesSettings();
  renderPush();
  const setPin = async () => {
    const p1 = prompt('New PIN (4–8 digits)'); if (!p1) return;
    const p2 = prompt('Type the PIN again'); if (p1 !== p2) return toast('The two PINs were different', 'bad');
    try { const r = await api('/api/pin/set', {pin: p1}); savePinToken(r.token, r.exp); S.pinSet = true; toast('PIN set', 'ok'); loadAccount(); }
    catch (e) { toast(e.message, 'bad'); }
  };
  if ($('#pinOn')) $('#pinOn').onclick = setPin;
  if ($('#pinChange')) $('#pinChange').onclick = setPin;
  if ($('#pinOff')) $('#pinOff').onclick = async () => {
    if (!confirm('Turn the PIN off?')) return;
    try { await api('/api/pin/clear', {}); S.pinSet = false; dropPinToken(); toast('PIN off', 'ok'); loadAccount(); } catch (e) { toast(e.message, 'bad'); }
  };
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

// ---- routine: today's plan and the journal (rule ④) -------------------------------------------------------
async function loadPlan() {
  let p;
  try { p = await api('/api/rules/plan'); } catch (e) { return; }
  S.plan = p;
  const now = new Date(), after = now.getHours() * 60 + now.getMinutes() >= 15 * 60 + 30;
  const open = !!S.planOpen;
  const bits = [];
  if (p.fix_today) bits.push(`<div class="warnbox">Yesterday you wrote: “${esc(p.fix_today)}”</div>`);
  bits.push(`<div class="kv"><span>Trades left today</span><b>${p.trades_left} of ${p.max_trades}</b></div>`);
  if (p.loss_left !== null) bits.push(`<div class="kv"><span>Loss you can still take</span><b class="${p.loss_left <= 0 ? 'down' : ''}">${inr(Math.max(0, p.loss_left))}</b></div>`);
  bits.push(`<div class="kv"><span>Today's P&amp;L</span><b class="${cls(p.day_pnl)}">${inr(p.day_pnl)}</b></div>`);
  bits.push(`<div class="kv"><span>Rules mode</span><b>${p.rules.on ? `On · ${p.rules.risk_pct}% risk = ${inr(p.rules.capital * p.rules.risk_pct / 100)}` : '<span class="down">Off</span>'}</b></div>`);
  if (p.bot_setups.length || p.bot_positions.length) bits.push(`<div class="kv"><span>Auto-trader</span><b>${p.bot_setups.length} setup(s) waiting · ${p.bot_positions.length} open</b></div>`);
  if (p.waiting.length) bits.push(`<div class="kv"><span>Waiting for confirmation</span><b>${p.waiting.map(a => esc(a.name)).join(', ')}</b></div>`);
  bits.push(p.near_levels.length ? `<div class="small muted" style="margin-top:6px">At a level now (your watchlist):</div>` +
      p.near_levels.map(n => `<div class="kv plan-lv" data-key="${esc(n.key)}"><span>${esc(n.sym)}</span><b>${esc(n.level)} ${px(n.price)} <span class="small muted">${n.dist_pct > 0 ? '+' : ''}${n.dist_pct}%</span></b></div>`).join('')
    : '<div class="small muted" style="margin-top:6px">None of your watchlist stocks is at a level. No level, no trade: waiting is fine.</div>');
  const jBtn = after || p.journal_done ? `<button class="${p.journal_done ? 'ghost sm' : 'primary'}" id="jrnBtn" style="${p.journal_done ? '' : 'padding:6px 12px'}">${p.journal_done ? '✓ Journal done' : "Write today's journal"}</button>` : '';
  $('#planCard').innerHTML = `<div class="card plan" style="margin:0 0 12px">
    <div style="display:flex;justify-content:space-between;align-items:center;gap:8px">
      <h3 style="margin:0;cursor:pointer" id="planToggle">${after ? 'After the close' : "Today's plan"} <span class="muted small">${open ? '▾' : '▸'}</span></h3>
      <div style="display:flex;gap:8px;align-items:center">${p.streak ? `<span class="pill ok">${p.streak}-day journal streak</span>` : ''}${jBtn}</div></div>
    ${open ? bits.join('') : `<div class="small muted" style="margin-top:4px">${p.trades_left} trades left · ${p.near_levels.length} watchlist stock(s) at a level${p.fix_today ? " · yesterday's note inside" : ''}</div>`}
  </div>`;
  $('#planToggle').onclick = () => { S.planOpen = !open; loadPlan(); };
  if ($('#jrnBtn')) $('#jrnBtn').onclick = () => openJournal();
  $$('#planCard .plan-lv').forEach(x => x.onclick = () => openInstrument(x.dataset.key));
}
async function openJournal(day) {
  let v;
  try { v = await api('/api/rules/journal' + (day ? '?day=' + day : '')); } catch (e) { return toast(e.message, 'bad'); }
  const e = v.entry || {}, s = v.summary;
  openSheet(`<div class="sh-head"><div><h2>Journal · ${esc(v.day)}</h2><div class="muted small">Two minutes. Same questions every day.</div></div><button class="sh-close">✕</button></div>
    <div class="box"><div class="kv"><span>Trades</span><b>${s.trades} (${s.wins} won)</b></div>
      <div class="kv"><span>Net P&amp;L after charges</span><b class="${cls(s.net)}">${inr(s.net)}</b></div>
      <div class="kv"><span>Charges</span><b>${inr(s.charges)}</b></div></div>
    ${v.trades.map(t => `<div class="box small"><div class="kv"><b>${esc(t.sym)}</b><b class="${cls(t.net)}">${inr(t.net)}</b></div>
      <div class="muted">${esc(t.entry.slice(11, 16))} → ${esc(t.exit.slice(11, 16))} · ${esc(t.side === 'LONG' ? 'long' : 'short')} ${t.qty}</div>
      <div>${t.in_app ? `✓ Level: ${esc(t.level || '–')} · ✓ Stop ${px(t.stop)} ${t.confirmed ? '· ✓ Waited for confirmation' : '· entered without waiting'}` : '<span class="down">Placed outside this app: no rules check</span>'}
      ${t.avg_down ? ' · <span class="down">added to a loser</span>' : ''}</div></div>`).join('') || '<div class="muted small">No finished trades today.</div>'}
    ${[...v.notes, ...v.auto_notes].map(n => `<div class="warnbox">${esc(n)}</div>`).join('')}
    <div class="section-title">Did you follow your rules?</div>
    <div class="seg" id="jFollow">${[['yes', 'Yes'], ['partly', 'Partly'], ['no', 'No']].map(([k, l]) => `<button data-v="${k}" class="${e.followed === k ? 'on' : ''}">${l}</button>`).join('')}</div>
    <div class="field"><label>What went well?</label><input id="jGood" value="${esc(e.good || '')}" placeholder="e.g. waited for the reaction at PDL"></div>
    <div class="field" style="margin-top:8px"><label>One thing to fix tomorrow (shown in tomorrow's plan)</label><input id="jFix" value="${esc(e.fix || '')}" placeholder="e.g. no trades before 9:30"></div>
    <div class="field" style="margin-top:8px"><label>How did you feel while trading? (1 calm – 5 stressed)</label>
      <div class="seg" id="jMood">${[1, 2, 3, 4, 5].map(m => `<button data-v="${m}" class="${e.mood === m ? 'on' : ''}">${m}</button>`).join('')}</div></div>
    <button class="primary" id="jSave" style="width:100%;margin-top:12px">Save journal</button>`);
  let followed = e.followed || '', mood = e.mood || 0;
  $('#jFollow').onclick = ev => { const b = ev.target.closest('button'); if (!b) return; followed = b.dataset.v; $$('#jFollow button').forEach(x => x.classList.toggle('on', x === b)); };
  $('#jMood').onclick = ev => { const b = ev.target.closest('button'); if (!b) return; mood = +b.dataset.v; $$('#jMood button').forEach(x => x.classList.toggle('on', x === b)); };
  $('#jSave').onclick = async () => {
    try { await api('/api/rules/journal', {day: v.day, followed, good: $('#jGood').value, fix: $('#jFix').value, mood}); closeSheet(); toast('Journal saved', 'ok'); loadPlan(); }
    catch (er) { toast(er.message, 'bad'); }
  };
}

// ---- market: gainers, losers, breakouts, volume, 52-week, OI, sectors -------------------------------------
S.mk = 'gainers';
$('#mkGood').onclick = () => { S.mkGood = !S.mkGood; setPref('mkGood', S.mkGood); renderMarket(); };
$('#mkTabs').onclick = e => { const b = e.target.closest('button'); if (!b) return; S.mk = b.dataset.m; $$('#mkTabs button').forEach(x => x.classList.toggle('on', x === b)); renderMarket(); };
async function loadMarket() {
  try { S.market = await api('/api/market'); } catch (e) { $('#mkList').innerHTML = `<div class="item muted">${esc(e.message)}</div>`; return; }
  renderMarket();
}
function pctSpan(v) { return v === null || v === undefined ? '–' : `<span class="${cls(v)}">${v > 0 ? '+' : ''}${Number(v).toFixed(2)}%</span>`; }
function renderMarket() {
  const m = S.market; if (!m) return;
  if (m.error) { $('#mkList').innerHTML = `<div class="item muted">${esc(m.error)}</div>`; return; }
  $('#mkIdx').innerHTML = m.indices.map(i => `<div class="mk-card" data-key="${esc(i.key)}"><div class="small muted">${esc(i.name)}</div>
    <b>${px(i.ltp)}</b><div class="small">${pctSpan(i.pct)}</div></div>`).join('');
  const b = m.breadth, tot = b.up + b.down + b.flat || 1;
  $('#mkBreadth').innerHTML = `<div class="mk-breadth"><div class="small"><span class="up">▲ ${b.up} up</span> · <span class="down">▼ ${b.down} down</span> · ${b.flat} flat <span class="muted">(${m.count} F&amp;O stocks · ${esc(m.at)})</span></div>
    <div class="mk-bar"><i class="u" style="width:${100 * b.up / tot}%"></i><i class="d" style="width:${100 * b.down / tot}%"></i></div></div>`;
  const row = (r, right, sub) => `<div class="item" data-key="${esc(r.key)}"><div class="grow"><div class="name">${esc(r.sym)} <span class="tag">${esc(r.sector)}</span></div>
      <div class="sub">${sub || ''}</div></div><div class="right"><div class="ltp">₹${px(r.ltp)}</div><div class="chg">${right}</div></div></div>`;
  let html = '', note = '';
  const k = S.mk;
  const L = name => (S.mkGood ? m[name] : (m[name + '_all'] || m[name])) || [];
  const lq = m.liq || {};
  $('#mkGood').classList.toggle('on', S.mkGood);
  $('#mkGood').textContent = (S.mkGood ? '✓ ' : '') + 'F&O · good volume only';
  $('#mkRule').innerHTML = S.mkGood ? `Good volume: ₹${lq.min_turnover_cr} Cr+ and ${lq.min_avg_lakh} lakh+ shares a day (20-day average), and today ≥ ${lq.min_rvol}× usual so far. ${m.good_count} of ${m.count} pass. <a href="#" id="mkRuleEdit">Change</a>` : '';
  if ($('#mkRuleEdit')) $('#mkRuleEdit').onclick = async e => {
    e.preventDefault();
    const a = prompt('Minimum average traded value a day (₹ crore)', lq.min_turnover_cr); if (a === null) return;
    const b = prompt('Minimum average shares a day (lakh)', lq.min_avg_lakh); if (b === null) return;
    const c = prompt('Today at least this × usual volume so far', lq.min_rvol); if (c === null) return;
    try { await api('/api/market/liquidity', {min_turnover_cr: +a, min_avg_lakh: +b, min_rvol: +c}); toast('Saved', 'ok'); loadMarket(); } catch (er) { toast(er.message, 'bad'); }
  };
  if (k === 'gainers' || k === 'losers') {
    html = L(k).map(r => row(r, pctSpan(r.pct), `${r.chg > 0 ? '+' : ''}₹${px(r.chg)}${r.rvol ? ` · volume ${r.rvol}× usual` : ''}${r.turnover_cr ? ` · ₹${r.turnover_cr.toLocaleString('en-IN')} Cr/day` : ''}`)).join('');
  } else if (k === 'volume') {
    html = L('volume').map(r => row(r, `<b>${r.rvol}×</b> volume`, `${pctSpan(r.pct)} · ${big(r.vol)} traded`)).join('');
    note = 'Today\'s volume against the 20-day average, adjusted for how much of the day has gone. 2× or more.';
    if (!m.daily_ready) note = 'Waiting for the daily candles (the auto-trader downloads them; a few minutes after the first start).';
  } else if (k === 'highs' || k === 'lows') {
    const hi = k === 'highs';
    html = L(k).map(r => row(r, pctSpan(r.pct), (hi ? r.new_high : r.new_low) ? `<b class="${hi ? 'up' : 'down'}">New 52-week ${hi ? 'high' : 'low'} today</b>` :
      `${Math.abs(hi ? r.from_high_pct : r.from_low_pct).toFixed(2)}% from the 52-week ${hi ? 'high' : 'low'} ₹${px(hi ? r.hi52 : r.lo52)}`)).join('');
    note = `New 52-week ${hi ? 'highs' : 'lows'} today, and stocks within 2% of one.`;
    if (!m.daily_ready) note = 'Waiting for the daily candles (a few minutes after the first start).';
  } else if (k === 'breakouts') {
    html = L('breakouts').map(x => `<div class="item" data-key="${esc(x.key)}"><div class="grow">
      <div class="name">${esc(x.sym)} <span class="pill ${x.side === 'up' ? 'ok' : 'bad'}">${x.side === 'up' ? '▲ BREAKOUT' : '▼ BREAKDOWN'}</span></div>
      <div class="sub">${esc(x.at)} candle closed ${x.side === 'up' ? 'above' : 'below'} ${esc(x.level_name)} ₹${px(x.level)} · ${x.vol_x}× the day's average volume</div></div>
      <div class="right"><div class="ltp">₹${px(x.close)}</div><div class="chg ${x.side === 'up' ? 'up' : 'down'}">${x.beyond_pct > 0 ? '+' : ''}${x.beyond_pct}% beyond</div></div></div>`).join('');
    note = `The first 15-minute candle (9:15–9:30) is the range; a later 15-minute close above its high (or below its low) is a breakout. Also closes through yesterday's high or low. Newest first.${m.breakouts_at ? ' Checked at ' + m.breakouts_at + '.' : ''}${m.job ? ' ' + m.job + '…' : ''}`;
  } else if (k === 'buildup') {
    const order = ['Long build-up', 'Short build-up', 'Short covering', 'Long unwinding'];
    const what = {'Long build-up': 'price ↑ OI ↑ (fresh buying)', 'Short build-up': 'price ↓ OI ↑ (fresh selling)',
      'Short covering': 'price ↑ OI ↓ (shorts exiting)', 'Long unwinding': 'price ↓ OI ↓ (longs exiting)'};
    html = order.filter(o => (m.buildup[o] || []).length).map(o => `<div class="item" style="cursor:default;background:var(--bg2)"><b>${o}</b> <span class="muted small">${what[o]}</span></div>` +
      m.buildup[o].map(r => row(r, pctSpan(r.pct), `futures OI ${r.oi_pct > 0 ? '+' : ''}${r.oi_pct}% · ${big(r.oi)}`)).join('')).join('');
    note = 'Current-month futures: price and open interest against yesterday.';
    if (!m.oi_ready) note = 'Reading yesterday\'s futures open interest (about a minute after the start).' + (m.job ? ' ' + m.job + '…' : '');
  } else if (k === 'sectors') {
    const mx = Math.max(0.5, ...m.sectors.map(s => Math.abs(s.pct)));
    html = `<div class="item" style="cursor:default;display:block"><table class="bars">${m.sectors.map(s => `<tr><td class="l">${esc(s.name)}<div class="small muted">${s.up}/${s.n} up</div></td>
      <td class="bar"><div class="wrap"><div class="half neg">${s.pct < 0 ? `<div class="b" style="width:${Math.abs(s.pct) / mx * 100}%;background:var(--down)"></div>` : ''}</div>
      <div class="half">${s.pct > 0 ? `<div class="b" style="width:${s.pct / mx * 100}%;background:var(--up)"></div>` : ''}</div></div></td>
      <td class="r">${pctSpan(s.pct)}</td></tr>`).join('')}</table></div>`;
    note = 'Average % change of each sector\'s F&O stocks today.';
  }
  $('#mkNote').innerHTML = note;
  $('#mkList').innerHTML = html || '<div class="item muted">Nothing here right now.</div>';
}
$('#mkList').onclick = e => { const r = e.target.closest('[data-key]'); if (r && r.dataset.key) openInstrument(r.dataset.key); };
$('#mkIdx').onclick = e => { const r = e.target.closest('[data-key]'); if (r) openInstrument(r.dataset.key); };

// ---- auto-trader ----------------------------------------------------------------------------------------------
async function autoAct(act, body) {
  try { const r = await api('/api/auto/' + act, body || {}); renderAuto(r.auto); return true; }
  catch (e) { toast(e.message, 'bad', 7000); return false; }
}
async function loadAuto() {
  try { renderAuto(await api('/api/auto')); } catch (e) { $('#auto').innerHTML = `<div class="badbox">${esc(e.message)}</div>`; }
}
function renderAuto(a) {
  S.auto = a;
  const c = a.settings, bt = a.backtest, live = a.mode === 'live';
  const status = a.halted ? `<span class="pill bad">STOPPED</span> ${esc(a.halted)}` : a.armed ? '<span class="pill ok">RUNNING</span>' : '<span class="pill">PAUSED</span>';
  const pos = a.positions.map(p => {
    const pl = p.booked + p.left * ((p.ltp || p.entry) - p.entry);
    return `<div class="item" style="cursor:default;align-items:flex-start"><div class="grow">
      <div class="name">${esc(p.sym)} <span class="tag">MTF</span><span class="tag">${p.mode.toUpperCase()}</span></div>
      <div class="sub">${p.left}/${p.qty} @ ₹${px(p.entry)} · LTP ₹${px(p.ltp)} · day ${p.sessions + 1} of 10</div>
      <div class="sub">Stop ₹${px(p.sl)}${p.half ? ' (at cost)' : ''} · T1 ₹${px(p.t1)}${p.half ? ' ✓' : ''} · T2 ₹${px(p.t2)}
        ${p.mode === 'live' ? (p.stop_kind === 'gtt' ? ' · stop held at Dhan (GTT)' : p.stop_kind === 'day' ? ' · day stop order at Dhan' : ' · <b class="down">no stop order at Dhan</b>') : ''}</div>
    </div><div class="right"><div class="ltp ${cls(pl)}">${inr(pl, 2)}</div></div></div>`;
  }).join('');
  const setups = a.setups.map(s => `<div class="item" style="cursor:default"><div class="grow"><div class="name">${esc(s.sym)} <span class="tag">score ${s.score}</span></div>
      <div class="sub">Buy above ${px(s.entry)} · stop ${px(s.sl)} · T1 ${px(s.t1)} · T2 ${px(s.t2)} · from ${esc(s.made)}${s.order ? ' · order #' + esc(s.order) : s.qty ? ' · paper order ' + s.qty : ''}</div>
      <div class="sub">${esc((s.why || []).slice(0, 3).join(' · '))}</div></div></div>`).join('');
  const closed = a.closed.slice(0, 25).map(t => `<div class="item" style="cursor:default;padding:8px 14px"><div class="grow">
      <div class="name">${esc(t.sym)} <span class="tag">${t.mode.toUpperCase()}</span></div>
      <div class="sub">${esc(t.opened.slice(0, 10))} → ${esc(t.closed.slice(0, 10))} · ${t.qty} @ ${px(t.entry)} → ${px(t.exit)} · ${esc(t.why)}</div></div>
      <div class="right"><div class="${cls(t.net)}">${inr(t.net)}</div><div class="small muted">${t.r}R</div></div></div>`).join('');
  const btRows = bt && bt.rows ? `<table class="bars"><tr class="small muted"><td>Min score</td><td class="r">Trades</td><td class="r">Won</td><td class="r">After costs</td><td class="r">Later days</td><td class="r">t</td></tr>
      ${bt.rows.map(r => `<tr${r.score === bt.score ? ' style="font-weight:700"' : ''}><td>${r.score || 'any'}${r.score === bt.score ? ' ★' : ''}</td><td class="r">${r.all.trades}</td><td class="r">${r.all.win_rate}%</td>
        <td class="r ${cls(r.all.net)}">${inrShort(r.all.net)}</td><td class="r ${cls(r.late.net)}">${inrShort(r.late.net)}</td><td class="r">${r.late.t_stat}</td></tr>`).join('')}</table>` : '';
  const dis = a.locked ? 'disabled' : '';
  $('#auto').innerHTML = `
    <div class="pnl-card">
      <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap">
        <div><span class="pill ${live ? 'bad' : 'warn'}" style="font-size:13px">${live ? 'LIVE · real orders' : 'PAPER · no real orders'}</span> ${status}</div>
        <div class="ord-actions" style="margin:0">
          ${a.armed ? '<button id="auPause">Pause</button>' : `<button id="auStart" class="primary" style="padding:6px 14px">${a.halted ? 'Restart' : 'Start'}</button>`}
        </div></div>
      <div class="kpis" style="margin:12px 0 0"><div class="kpi"><div class="l">${live ? 'Live' : 'Paper'} result</div><div class="v ${cls(a.summary.net)}">${inrShort(a.summary.net)}</div><div class="s">${a.summary.trades} trades · ${a.summary.win_rate}% won</div></div>
        <div class="kpi"><div class="l">Capital · risk a trade</div><div class="v">${inrShort(c.capital)}</div><div class="s">${c.risk_pct}% = ${inr(c.capital * c.risk_pct / 100)} at the stop</div></div></div>
      ${c.hands_off ? '<div class="muted small" style="margin-top:8px">Hands off: while the bot is on, manual orders can only close positions.</div>' : ''}
      ${a.locked ? '<div class="muted small">Market open: settings and mode are locked until 3:30 PM. Pause works any time.</div>' : ''}
    </div>
    <div class="card"><h3>The setup</h3><p class="muted small" style="margin:0">Swing buys from the scanner's daily-chart rules (trend + imbalance zone + price-action trigger), held up to 10 sessions on <b>MTF</b>.
      At 3:10 PM it keeps only the <b>top ${c.max_new_per_day}</b> setup${c.max_new_per_day > 1 ? 's' : ''} scoring <b>${c.min_score}+</b>; at most ${c.max_positions} open.
      Buy above the trigger high (not chased on a gap), stop-loss sent to Dhan as a GTT order, half sold at T1 and the stop moved to cost, the rest at T2.
      Size: ${c.risk_pct}% risk per trade; each position ≤ ${c.per_trade_cap_x}× capital, all ≤ ${c.total_cap_x}× (MTF margin). Stops itself after a ${c.max_drawdown_pct}% drawdown.</p></div>
    <div class="card"><h3>Edge check (backtest) ${bt ? (bt.passed ? '<span class="pill ok">PASSED</span>' : '<span class="pill bad">NO EDGE YET</span>') : ''}</h3>
      ${bt ? `<p class="small" style="margin:0 0 8px">${esc(bt.why)}</p><div class="muted small">${bt.stocks || ''} stocks · ${esc(bt.from || '')} to ${esc(bt.to || '')} · chosen on days before ${esc(bt.split || '')}, checked on the days after · costs 0.25% + MTF interest · ran ${esc(bt.ran_at || '')}</div>${btRows}` :
        '<p class="muted small">Not run yet. It needs about 2 years of daily candles for every F&amp;O stock (a few minutes).</p>'}
      <div class="ord-actions"><button id="auBt" ${a.job.running ? 'disabled' : ''}>${a.job.running ? 'Running… ' + esc(a.job.msg) : 'Run backtest now'}</button></div></div>
    <div class="card"><h3>Going live</h3>
      ${live ? '<p class="small">Live: real MTF orders on your Dhan account.</p><div class="ord-actions"><button id="auPaper" ' + dis + '>Back to paper</button></div>' :
        `<p class="small" style="margin:0">Paper sessions: <b>${a.paper_days} of ${c.paper_sessions_required}</b>.
          ${a.live_blockers.length ? 'Not yet: ' + esc(a.live_blockers.join('; ')) + '.' : '<b class="up">Ready.</b> Start with the same small capital and watch the first trades in Dhan.'}</p>
          <div class="ord-actions"><button id="auLive" class="danger" ${a.live_blockers.length || a.locked ? 'disabled' : ''}>Go live (real orders)</button></div>`}
    </div>
    <div class="section-title">Setups waiting to trigger</div><div class="list">${setups || '<div class="item muted">None. New ones are picked at 3:10 PM.</div>'}</div>
    <div class="section-title">Open positions</div><div class="list">${pos || '<div class="item muted">None.</div>'}</div>
    ${a.positions.length ? '<div class="row-end"><button id="auClose" class="danger ghost">Close all bot positions</button></div>' : ''}
    <div class="section-title">Finished trades</div><div class="list">${closed || '<div class="item muted">None yet.</div>'}</div>
    <div class="section-title">Settings</div>
    <div class="box"><div class="formrow">
      ${[['capital', 'Capital (₹)'], ['risk_pct', 'Risk per trade (% of capital)'], ['max_positions', 'Open positions at most (1–2)'], ['max_new_per_day', 'New setups a day (1–2)'],
         ['min_score', 'Minimum score (empty = backtest’s choice)'], ['per_trade_cap_x', 'One position at most (× capital)'], ['total_cap_x', 'All positions at most (× capital)'],
         ['max_drawdown_pct', 'Stop the bot after a drawdown of (%)'], ['paper_sessions_required', 'Paper sessions before live']]
        .map(([k, l]) => `<div class="field"><label>${l}</label><input data-set="${k}" inputmode="decimal" value="${k === 'min_score' && !(S.auto.settings_raw || {}).min_score ? '' : c[k] ?? ''}" ${dis}></div>`).join('')}
      </div>
      <label class="toggle"><input type="checkbox" id="auHands" ${c.hands_off ? 'checked' : ''} ${dis}> Hands off: while the bot is on, I can only close positions by hand</label>
      <button class="ghost" id="auSave" ${dis}>Save settings</button></div>
    <div class="section-title">Bot log</div>
    <div class="box small" style="max-height:300px;overflow:auto">${a.log.map(l => `<div class="kv"><span>${esc(l.at)}</span><span class="${l.kind === 'error' ? 'down' : l.kind === 'warn' ? '' : ''}" style="text-align:right;max-width:75%">${esc(l.msg)}</span></div>`).join('') || '<span class="muted">Nothing yet.</span>'}</div>`;
  const on = (id, f) => { const el = $('#' + id); if (el) el.onclick = f; };
  on('auStart', () => autoAct('arm', {on: true}));
  on('auPause', () => autoAct('arm', {on: false}));
  on('auBt', () => autoAct('backtest').then(() => setTimeout(loadAuto, 1500)));
  on('auPaper', () => autoAct('mode', {mode: 'paper'}));
  on('auLive', () => { const v = prompt('The bot will place REAL MTF orders on your Dhan account.\nType LIVE to confirm.'); if (v === 'LIVE') autoAct('mode', {mode: 'live', confirm: 'LIVE'}); });
  on('auClose', () => { const v = prompt('Sell every bot position at market now?\nType CLOSE to confirm.'); if (v === 'CLOSE') autoAct('close-all', {confirm: 'CLOSE'}); });
  on('auSave', () => {
    const b = {hands_off: $('#auHands').checked};
    $$('[data-set]').forEach(i => { b[i.dataset.set] = i.value.trim() === '' ? (i.dataset.set === 'min_score' ? null : undefined) : +i.value; });
    autoAct('settings', b).then(ok => ok && toast('Settings saved', 'ok'));
  });
  if (a.job.running) setTimeout(() => S.tab === 'auto' && loadAuto(), 2500);
}

// ---- start --------------------------------------------------------------------------------------------------
try { const th = localStorage.getItem('mt-theme'); if (th) document.documentElement.dataset.theme = th; } catch (e) { /* ignore */ }
(async function start() {
  try { S.pinSet = (await (await fetch('/api/pin', {headers: {'X-Requested-With': 'meridian-trade'}})).json()).set; } catch (e) { S.pinSet = false; }
  if (S.pinSet && !pinToken()) showLock();
  swReg();
  const h = location.hash.replace('#', '');
  if (['watch', 'market', 'chain', 'orders', 'positions', 'auto', 'insights', 'account'].includes(h)) setTimeout(() => showTab(h), 0);
  if (h === 'journal') setTimeout(() => openJournal(), 800);
  await loadStatus();
  renderWlTabs(); renderWl();
  loadFunds(); loadOrders(); loadPlan();
  setInterval(() => { if (!document.hidden && S.tab === 'watch') loadPlan(); }, 120000);
  setInterval(pollQuotes, 1000);
  setInterval(loadStatus, 15000);
  setInterval(loadFunds, 20000);
  setInterval(() => { if (document.hidden) return; if (S.tab === 'orders') loadOrders(); if (S.tab === 'positions') loadPositions(); }, 2500);
  setInterval(() => { if (!document.hidden && S.tab === 'chain') loadChain(false); }, 3500);
  setInterval(() => { if (!document.hidden && S.tab === 'auto') loadAuto(); }, 15000);
  setInterval(() => { if (!document.hidden && S.tab === 'market') loadMarket(); }, 20000);
  setInterval(() => { if (!document.hidden && S.tab !== 'orders') loadOrders(); }, 10000);
  pollQuotes();
})();
