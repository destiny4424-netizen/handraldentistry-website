// End-to-end test of the Zerodha copy of the desk in headless Chromium, against the pretend Zerodha (mockkite.py).
//   cd _trade && MT_BROKER=zerodha MT_PORT=3060 MT_DATA_DIR=/tmp/kite-demo python3 trade.py --demo &
//   node tests/e2e_kite.js [http://127.0.0.1:3060] [screenshot folder]
const {chromium, devices} = require('playwright');
const BASE = process.argv[2] || 'http://127.0.0.1:3060';
const SHOTS = process.argv[3] || '';
const MOCK = process.argv[4] || 'http://127.0.0.1:3095';   // the pretend Zerodha of trade.py --demo

let failures = 0;
function check(ok, what) {
  console.log((ok ? '  ok   ' : '  FAIL ') + what);
  if (!ok) failures++;
}

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({...devices['iPhone 13']});
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('console', m => { if (m.type() === 'error' && !/status of 40[0-9]|status of 423|status of 502/.test(m.text())) errors.push(m.text()); });
  page.on('dialog', d => d.accept(d.type() === 'prompt' ? 'EXIT ALL' : undefined));
  const shot = async name => { if (SHOTS) await page.screenshot({path: `${SHOTS}/kite-${name}.png`}); };
  const apiPost = (path, body) => page.evaluate(([p, b]) => fetch(p, {method: 'POST', body: JSON.stringify(b),
    headers: {'Content-Type': 'application/json', 'X-Requested-With': 'meridian-trade'}}).then(r => r.json()), [path, body]);
  const noDhan = async (sel, what) => {
    const t = await page.textContent(sel);
    check(!/Dhan/.test(t), `${what}: says Zerodha, never Dhan` + (/Dhan/.test(t) ? ` ("…${t.slice(Math.max(0, t.indexOf('Dhan') - 40), t.indexOf('Dhan') + 20)}…")` : ''));
  };

  await page.goto(BASE);
  await page.waitForSelector('#wl .item');
  await page.waitForFunction(() => [...document.querySelectorAll('#wl .ltp')].some(e => /\d/.test(e.textContent)), null, {timeout: 15000});
  check(true, 'watchlist shows live prices from Zerodha');
  check((await page.getAttribute('#conn', 'title')).startsWith('Zerodha'), 'connection dot says Zerodha');
  await shot('1-watchlist');

  // search, instrument, option chain built from quotes (Zerodha has no chain API)
  await page.fill('#q', 'nifty 26000 ce');
  await page.waitForFunction(() => document.querySelector('#results').textContent.includes('26,000 CE'));
  check(true, 'search finds an option by strike in Zerodha\'s list');
  await page.fill('#q', '');
  await page.dispatchEvent('#q', 'input');
  await page.click('#wl .item');
  await page.waitForSelector('#chart canvas');
  check(true, 'instrument chart drawn from Zerodha candles');
  await page.waitForSelector('#miniChain .mini-chain tbody tr', {timeout: 20000});
  check((await page.$$('#miniChain .mini-chain tbody tr')).length === 5, 'option chain under the chart (from the options\' quotes)');
  await shot('2-instrument');

  // a market buy with a stop-loss and target: the desk runs it as a super order
  await page.click('#shBuy');
  await page.waitForSelector('#tkReview');
  await page.click('#tkType button[data-v="MARKET"]');
  await page.fill('#tkLots', '2');
  await page.dispatchEvent('#tkLots', 'input');
  const ltp = await page.evaluate(() => S.ticket && S.ticket.ltp || parseFloat(document.querySelector('#tkPrice')?.value || '0'));
  const key = await page.evaluate(() => S.ticket.it.key);
  const r = await apiPost('/api/order', {key, side: 'BUY', qty: 2, product: 'INTRADAY', type: 'MARKET',
    super: {sl: (ltp * 0.97).toFixed(1), target: (ltp * 1.05).toFixed(1)}});
  check(r.ok, 'super order (stop-loss + target) accepted' + (r.error ? ': ' + r.error : ''));
  await page.click('.sh-close');
  await page.click('#tabs button[data-tab="positions"]');
  const stopIn = await page.waitForFunction(() => /stop/i.test(document.querySelector('#tab-positions').textContent) &&
    document.querySelector('.live-box'), null, {timeout: 20000}).then(() => true, () => false);
  check(stopIn, 'positions: the position shows its stop-loss, placed at Zerodha after the fill');
  await shot('3-positions');
  await noDhan('#tab-positions', 'positions');
  await page.click('#tabs button[data-tab="orders"]');
  await page.waitForSelector('#tab-orders.on');
  await page.click('#orderFilter button[data-f="all"]');
  await page.waitForFunction(() => document.querySelector('#orders').textContent.includes('TRADED'), null, {timeout: 10000});
  check(true, 'orders: the entry shows as traded');
  await noDhan('#tab-orders', 'orders');

  // account: Zerodha login box, kill switch note, log out and back in through Kite's login page
  await page.click('#tabs button[data-tab="account"]');
  await page.waitForFunction(() => document.querySelector('#account').textContent.includes('Zerodha login'), null, {timeout: 10000});
  const acc = await page.textContent('#account');
  check(acc.includes('Connected') && acc.includes('Login valid until') && acc.includes('06:00'), 'account: logged in, valid until 6 AM');
  check(acc.includes('Kill switch') && acc.includes('In Kite'), 'account: kill switch points to Kite');
  check(acc.includes('/kite/callback'), 'account: shows the redirect URL to give the Kite Connect app');
  check(!(await page.$('#dhSave')) && !(await page.$('#killBtn')), 'account: no Dhan token box or Dhan kill switch');
  await noDhan('#account', 'account');
  await shot('4-account');
  await page.click('#kiteOut');
  await page.waitForFunction(() => document.querySelector('#account').textContent.includes('Not logged in'), null, {timeout: 10000});
  await page.waitForFunction(() => !document.querySelector('#banner').classList.contains('hidden') &&
    document.querySelector('#banner').textContent.includes('Log in with Zerodha'), null, {timeout: 10000});
  check(true, 'logged out: the banner asks to log in with Zerodha');
  await shot('5-logged-out');
  await page.click('#kiteLogin');                           // demo: the pretend Kite login sends us straight back
  await page.waitForURL(u => !String(u).includes('/kite/callback'), {timeout: 15000});
  await page.waitForSelector('#wl .item');
  const back = await page.evaluate(() => api('/api/status').then(s => s.connected && s.kite_user_name));
  check(back === 'Demo Zerodha', 'logged in again through the Kite login page');
  await apiPost('/api/kite/logout', {});
  const nostate = await (await ctx.request.get(BASE + '/kite/callback?request_token=demorequest123&status=success&action=login')).text();
  check(nostate.includes('Zerodha connected'), 'a login that comes back without the state (or after a restart) still connects');
  const bad = await (await ctx.request.get(BASE + '/kite/callback?request_token=forged&status=success')).text();
  check(bad.includes('Zerodha login failed'), 'a request token Zerodha doesn\'t accept is refused');
  const note = await page.evaluate(() => api('/api/status').then(s => s.token_note || ''));
  check(/login failed/i.test(note), 'the reason shows in the banner');
  const odd = await (await ctx.request.get(BASE + '/kite/callback?request_token=demorequest123&status=SUCCESS&type=login')).text();
  check(odd.includes('Zerodha connected'), 'a token is tried whatever the status word says');
  const none = await (await ctx.request.get(BASE + '/kite/callback?status=error&message=Invalid+api_key')).text();
  check(none.includes('Invalid api_key'), 'with no token, the page shows what Zerodha sent');
  await apiPost('/api/kite/logout', {});
  const bad2 = await apiPost('/api/kite/token', {text: 'https://example.com/whatever'});
  check(/No request_token/.test(bad2.error || ''), 'paste: an address without a token is explained');
  await page.click('#tabs button[data-tab="watch"]');
  await page.click('#tabs button[data-tab="account"]');
  await page.waitForSelector('#kiPaste', {state: 'attached'});
  await page.$eval('#kiPaste', e => { e.closest('details').open = true; });
  const pasteBad = await apiPost('/api/kite/token', {text: 'https://x/?request_token=wrongtoken123'});
  check(/didn't accept/.test(pasteBad.error || ''), 'paste: a token Zerodha refuses is explained');
  await page.fill('#kiPaste', 'http://127.0.0.1/?action=login&type=login&status=success&request_token=demorequest123');
  await page.click('#kiPasteGo');
  const pasted = await page.waitForFunction(() => api('/api/status').then(s => s.connected), null, {timeout: 10000, polling: 500})
    .then(() => true, () => false);
  check(pasted, 'paste: the address copied after Zerodha\'s login connects the desk');

  // exit all
  await page.click('#tabs button[data-tab="positions"]');
  const ex = await apiPost('/api/exit-all', {confirm: 'EXIT ALL'});
  check(ex.ok && /closed at market/.test(ex.reply.message), 'exit all: positions closed, orders cancelled');
  const flat = await page.waitForFunction(() => api('/api/positions').then(p => p.positions.every(x => !x.netQty)), null,
    {timeout: 15000, polling: 1000}).then(() => true, () => false);
  check(flat, 'exit all: nothing left open');

  check(await page.$eval('#tabs button[data-tab="screener"]', b => b.classList.contains('hidden')),
    'stand-alone: no Screener tab (no link to the Meridian scanner)');
  const scr = await page.evaluate(() => api('/api/screener'));
  check(scr.ok === false, 'stand-alone: the server has no screener connection');
  for (const tab of ['watch', 'quick']) {
    await page.click(`#tabs button[data-tab="${tab}"]`);
    await page.waitForTimeout(1200);
    await noDhan(`#tab-${tab}`, tab);
  }
  const wide = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  check(!wide, 'no sideways scrolling on the phone');
  check(!errors.length, 'no JavaScript errors' + (errors.length ? ': ' + errors.join(' | ') : ''));
  await browser.close();
  console.log(failures ? `${failures} check(s) failed` : 'All checks passed');
  process.exit(failures ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
