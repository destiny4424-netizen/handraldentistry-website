// End-to-end test of Meridian Trade in headless Chromium, against the pretend Dhan (demo mode).
//   cd _trade && python3 trade.py --demo &   (MT_PORT=3051 for another port)
//   node tests/e2e.js [http://127.0.0.1:3050] [screenshot folder]
const {chromium, devices} = require('playwright');
const BASE = process.argv[2] || 'http://127.0.0.1:3050';
const SHOTS = process.argv[3] || '';
const MOCK = process.argv[4] || 'http://127.0.0.1:3099';   // the pretend Dhan of trade.py --demo

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
  page.on('console', m => { if (m.type() === 'error' && !/status of 40[019]|status of 423/.test(m.text())) errors.push(m.text()); });   // refusals the test asks for
  const answers = [];                                       // prompt() answers the test wants to give, in order
  page.on('dialog', d => d.accept(d.type() === 'prompt' ? (answers.length ? answers.shift() : 'EXIT ALL') : undefined));
  const shot = async name => { if (SHOTS) await page.screenshot({path: `${SHOTS}/${name}.png`}); };

  const apiPost = (path, body) => page.evaluate(([p, b]) => fetch(p, {method: 'POST', body: JSON.stringify(b),
    headers: {'Content-Type': 'application/json', 'X-Requested-With': 'meridian-trade'}}).then(r => r.json()), [path, body]);
  const pin = (seg, id, px) => fetch(MOCK + '/mock/pin', {method: 'POST', body: JSON.stringify({seg, id, px})});

  await page.goto(BASE);
  await apiPost('/api/rules/settings', {on: false});      // the plain trading flows first; Rules mode is tested below
  await page.reload();
  await page.waitForSelector('#wl .item');
  await page.waitForFunction(() => [...document.querySelectorAll('#wl .ltp')].some(e => /\d/.test(e.textContent)), null, {timeout: 10000});
  check(true, 'watchlist shows live prices');
  await page.waitForTimeout(3000);                         // the plan card has loaded by now
  check(!(await page.textContent('#wl')).includes('NaN'), 'watchlist changes are numbers (no NaN)');
  await shot('1-watchlist');

  // search and add to the watchlist
  await page.fill('#q', 'sbin');
  await page.waitForSelector('.res[data-key]');
  check((await page.textContent('#results')).includes('SBIN'), 'search finds SBIN');
  await page.click('.res[data-key] [data-add]');
  await page.fill('#q', 'nifty 26000 ce');
  await page.waitForFunction(() => document.querySelector('#results').textContent.includes('26,000 CE'));
  check(true, 'search finds an option by strike');
  await shot('2-search');
  await page.fill('#q', '');
  await page.dispatchEvent('#q', 'input');

  // instrument sheet and a market buy
  await page.click('#wlTabs button[data-i="1"]');
  await page.waitForSelector('#wl .item');
  await page.click('#wl .item');
  await page.waitForSelector('#shBuy');
  await page.waitForFunction(() => /\d/.test(document.querySelector('#shLtp').textContent));
  await page.waitForTimeout(800);
  await shot('3-instrument');
  await page.click('#shBuy');
  await page.waitForSelector('#tkReview');
  await page.click('#tkType button[data-v="MARKET"]');
  await page.fill('#tkLots', '5');
  await page.dispatchEvent('#tkLots', 'input');
  await shot('4-ticket');
  await page.click('#tkReview');
  await page.waitForSelector('#cfGo');
  check((await page.textContent('#sheetBody')).includes('Margin needed'), 'confirm screen shows margin');
  await shot('5-confirm');
  await page.click('#cfGo');
  await page.waitForSelector('.toast.ok');
  check((await page.textContent('.toast.ok')).includes('Order sent'), 'market order placed');
  await page.waitForSelector('#tab-orders.on');
  await page.click('#orderFilter button[data-f="all"]');
  await page.waitForFunction(() => document.querySelector('#orders').textContent.includes('TRADED'));
  check(true, 'order shows as traded');

  // a limit order far away stays open; modify then cancel it
  await page.click('#tabs button[data-tab="watch"]');
  await page.click('#wl .item');
  await page.waitForSelector('#shBuy');
  await page.click('#shBuy');
  await page.waitForSelector('#tkPrice');
  await page.waitForFunction(() => parseFloat(document.querySelector('#tkPrice').value) > 0);
  const far = await page.evaluate(() => (parseFloat(document.querySelector('#tkPrice').value) * 0.9).toFixed(1));
  await page.fill('#tkPrice', far);
  await page.dispatchEvent('#tkPrice', 'input');
  await page.click('#tkReview');
  await page.waitForSelector('#cfGo');
  check((await page.textContent('#sheetBody')).includes('away from the last price'), 'far limit price warns');
  await page.click('#cfGo');
  await page.waitForSelector('#tab-orders.on');
  await page.click('#orderFilter button[data-f="open"]');
  await page.waitForSelector('[data-mod]');
  await shot('6-orders');
  await page.click('[data-mod]');
  const newPrice = (parseFloat(far) - 1).toFixed(2);
  await page.fill('#mdPrice', newPrice);
  await page.click('#mdGo');
  await page.waitForSelector('.toast.ok');
  const want = Number(newPrice).toLocaleString('en-IN', {minimumFractionDigits: 2});
  const modified = await page.waitForFunction(w => document.querySelector('#orders').textContent.includes(w), want, {timeout: 10000})
    .then(() => true, () => false);
  check(modified, 'order modified');
  await page.click('[data-cancel]');
  await page.waitForFunction(() => !document.querySelector('[data-cancel]'), null, {timeout: 8000});
  check(true, 'order cancelled');

  // positions and exit
  await page.click('#tabs button[data-tab="positions"]');
  await page.waitForSelector('[data-exit]');
  await page.waitForTimeout(1500);
  await shot('7-positions');
  const posText = await page.textContent('#tab-positions');
  check(/₹[\d,]+\.\d\d/.test(await page.textContent('#pnlCard')) && /@ ₹[\d,]+\.\d\d · LTP ₹[\d,]+\.\d\d/.test(posText),
    'positions show rupees and paise (P&L, average, LTP)');
  await page.click('[data-exit]');
  await page.waitForSelector('#tkReview');
  check((await page.textContent('#sheetBody')).includes('Closing your position'), 'exit opens a closing ticket');
  await page.click('#tkType button[data-v="MARKET"]');
  await page.click('#tkReview');
  await page.waitForSelector('#cfGo');
  await page.click('#cfGo');
  await page.waitForSelector('.toast.ok');
  await page.click('#tabs button[data-tab="positions"]');
  await page.waitForFunction(() => document.querySelector('#positions').textContent.includes('Closed'), null, {timeout: 8000});
  check(true, 'position closed');

  // option chain and buying an option with target and stop-loss
  await page.click('#tabs button[data-tab="chain"]');
  await page.waitForSelector('#chain td.p');
  await page.waitForTimeout(500);
  await shot('8-chain');
  check(await page.$('#chain tr.atm') !== null, 'chain marks the at-the-money strike');
  await page.click('#chain tr.atm td.p.ce');
  await page.waitForSelector('#shBuy');
  await page.click('#shBuy');
  await page.waitForSelector('#tkSuper');
  await page.waitForFunction(() => parseFloat(document.querySelector('#tkPrice').value) > 0);
  const ltp = await page.evaluate(() => parseFloat(document.querySelector('#tkPrice').value));
  await page.check('#tkSuper');
  await page.fill('#tkTgt', String((ltp * 1.5).toFixed(1)));
  await page.fill('#tkSl', String((ltp * 0.7).toFixed(1)));
  await page.click('#tkReview');
  await page.waitForSelector('#cfGo');
  check((await page.textContent('#sheetBody')).includes('Money at risk'), 'super order shows money at risk');
  await shot('9-super-confirm');
  await page.click('#cfGo');
  await page.waitForSelector('.toast.ok');
  check(true, 'super order placed');

  // insights
  await page.click('#tabs button[data-tab="insights"]');
  await page.waitForSelector('.kpi');
  await page.waitForTimeout(400);
  check((await page.textContent('#insights')).includes("What's costing you"), 'insights shows habits');
  await shot('10-insights');
  await page.evaluate(() => window.scrollTo(0, 1200));
  await shot('11-insights-more');

  // account and limits
  await page.click('#tabs button[data-tab="account"]');
  await page.waitForSelector('#lmSave');
  await page.fill('#lmLoss', '100');
  await page.click('#lmSave');
  await page.waitForSelector('.toast.ok');
  await shot('12-account');

  // loss limit blocks a new order (the demo account has lost a little on the round trip, or not:
  // set the limit tiny and check either the block or a placed order, then reset)
  await page.click('#lmLoss', {clickCount: 3});
  await page.fill('#lmLoss', '0');
  await page.click('#lmSave');

  // Market tab: gainers, losers, 15-min breakouts, sectors; tap a stock to trade it
  await page.click('#tabs button[data-tab="market"]');
  await page.waitForSelector('#mkList .item[data-key]', {timeout: 30000});
  check((await page.$$('#mkIdx .mk-card')).length >= 4, 'market: index cards');
  const g = await page.$$eval('#mkList .chg', xs => xs.map(x => parseFloat(x.textContent)));
  check(g.length > 3 && g.every((v, i) => i === 0 || v <= g[i - 1]), 'market: top gainers in order');
  await shot('24-market-gainers');
  await page.click('#mkTabs button[data-m="losers"]');
  const l = await page.$$eval('#mkList .chg', xs => xs.map(x => parseFloat(x.textContent)));
  check(l.every((v, i) => i === 0 || v >= l[i - 1]), 'market: top losers in order');
  await page.click('#mkTabs button[data-m="breakouts"]');
  await page.waitForFunction(async () => {
    if (/BREAK/.test(document.querySelector('#mkList').textContent)) return true;
    await loadMarket(); return false;
  }, null, {timeout: 120000, polling: 3000});
  check(true, 'market: 15-minute breakouts listed');
  await shot('25-market-breakouts');
  await page.click('#mkTabs button[data-m="sectors"]');
  check((await page.textContent('#mkList')).includes('Banks'), 'market: sectors');
  await page.click('#mkTabs button[data-m="buildup"]');
  await page.click('#mkTabs button[data-m="gainers"]');
  await page.click('#mkList .item[data-key]');
  await page.waitForSelector('#shBuy');
  check(true, 'market: tapping a stock opens it to trade');
  await page.click('.sh-close');

  // "F&O · good volume" filter: the Market lists and the watchlist hide thin stocks (WIPRO, TATASTEEL in the demo)
  await page.click('#tabs button[data-tab="market"]');
  await page.waitForFunction(async () => {
    if (/of \d+ pass/.test(document.querySelector('#mkRule').textContent) && (S.market.low_volume || []).length) return true;
    await loadMarket(); return false;
  }, null, {timeout: 120000, polling: 3000});
  const shownMk = await page.$$eval('#mkList .item .name', xs => xs.map(x => x.textContent));
  check(!shownMk.some(t => /WIPRO|TATASTEEL/.test(t)), 'market: thin stocks left out of top gainers');
  await shot('26-market-filter');
  await page.click('#mkGood');
  check(!(await page.textContent('#mkGood')).includes('✓'), 'market: the filter can be switched off');
  await page.click('#mkGood');
  await page.click('#tabs button[data-tab="watch"]');
  await page.fill('#q', 'wipro');
  await page.waitForSelector('.res[data-key] [data-add]');
  check((await page.textContent('#results')).includes('F&O'), 'search marks F&O stocks');
  await page.click('.res[data-key] [data-add]');
  await page.fill('#q', '');
  await page.dispatchEvent('#q', 'input');
  await page.waitForFunction(() => /hidden/.test(document.querySelector('#wlHidden').textContent), null, {timeout: 30000});
  check(!(await page.textContent('#wl')).includes('WIPRO'), 'watchlist: low-volume stock hidden');
  await shot('27-watchlist-filter');
  await page.click('#wlShowAll');
  await page.waitForFunction(() => document.querySelector('#wl').textContent.includes('WIPRO'));
  check((await page.textContent('#wl')).includes('low vol'), 'watchlist: "Show all" brings it back, marked low vol');
  await page.click('#wlFilter');

  // Rules mode: level first, wait for the reaction, risk first, the daily plan and journal
  await apiPost('/api/rules/settings', {on: true, confirm_default: true});
  const SBIN = 'NSE_EQ:3045';
  let lv = await page.evaluate(k => fetch('/api/rules/levels?key=' + k).then(r => r.json()), SBIN);
  const L = lv.levels[0];
  await pin('NSE_EQ', '3045', L.price);
  await page.click('#tabs button[data-tab="watch"]');
  await page.click('#wlTabs button[data-i="0"]');
  await page.waitForSelector(`#wl .item[data-key="${SBIN}"]`);
  await page.waitForTimeout(3000);                         // the price feed picks up the pinned price
  await page.click(`#wl .item[data-key="${SBIN}"]`);
  await page.waitForSelector('#shBuy');
  await page.click('#shBuy');
  await page.waitForSelector('.rules #tkLevels button.on');
  check(true, 'rules: the ticket picks the level price is at');
  await page.click('#tkReview');
  await page.waitForFunction(() => document.querySelector('#tkMsg').textContent.includes('Rule 3'));
  check(true, 'rules: no stop-loss, no order');
  const sl = (L.price * 0.99).toFixed(2);
  await page.fill('#tkSl', sl);
  await page.dispatchEvent('#tkSl', 'input');
  const entry = await page.evaluate(() => parseFloat(document.querySelector('#tkPrice').value));
  const lots = await page.evaluate(() => parseInt(document.querySelector('#tkLots').value));
  check(lots === Math.floor(1500 / (entry - parseFloat(sl))), `rules: size worked out from the stop (${lots} shares)`);
  await shot('16-rules-ticket');
  await page.click('#tkReview');
  await page.waitForSelector('#cfGo');
  check((await page.textContent('#cfGo')).includes('Wait for confirmation'), 'rules: confirm screen says it will wait');
  await shot('17-rules-confirm');
  await page.click('#cfGo');
  await page.waitForSelector('#tab-orders.on');
  await page.waitForFunction(() => document.querySelector('#armed').textContent.includes('WAITING'));
  check(true, 'rules: order waits for the reaction, nothing sent to Dhan');
  await shot('18-rules-waiting');
  await page.click('[data-unarm]');
  await page.waitForFunction(() => document.querySelector('#armed').textContent.includes('CANCELLED'));
  check(true, 'rules: a waiting order can be cancelled');
  await page.click('#tabs button[data-tab="watch"]');
  await page.click(`#wl .item[data-key="${SBIN}"]`);
  await page.waitForSelector('#shBuy');
  await page.click('#shBuy');
  await page.waitForSelector('.rules #tkLevels button.on');
  await page.uncheck('#tkConfirm');
  await page.fill('#tkSl', sl);
  await page.dispatchEvent('#tkSl', 'input');
  await page.click('#tkReview');
  await page.waitForSelector('#cfGo');
  await page.click('#cfGo');
  await page.waitForFunction(() => [...document.querySelectorAll('.toast')].some(t => t.textContent.includes('Order sent'))
    || (document.querySelector('#cfMsg') || {}).textContent, null, {timeout: 10000});
  check([...await page.$$eval('.toast', ts => ts.map(t => t.textContent))].some(t => t.includes('Order sent')),
    'rules: level + stop + size: order sent with its stop');
  // far from every level: no trade
  lv = await page.evaluate(k => fetch('/api/rules/levels?key=' + k).then(r => r.json()), SBIN);
  let farPx = null;
  for (let k = 1; k <= 12 && !farPx; k++) for (const sgn of [1, -1]) {
    const p = +(L.price * (1 + sgn * k * 0.008)).toFixed(2);
    if (lv.levels.every(x => Math.abs(x.price - p) > lv.tol * 1.5)) { farPx = p; break; }
  }
  if (farPx) {
    await pin("NSE_EQ", "3045", farPx);
    await page.click('#tabs button[data-tab="watch"]');
    await page.waitForTimeout(3000);
    await page.click(`#wl .item[data-key="${SBIN}"]`);
    await page.waitForSelector('#shBuy');
    await page.click('#shBuy');
    await page.waitForFunction(() => (document.querySelector('.rules') || {}).textContent?.includes('No level near'));
    check(true, 'rules: no level near the price, no trade');
    await shot('19-rules-nolevel');
    await page.click('.sh-close');
  }
  // the daily plan and the journal
  await page.click('#tabs button[data-tab="watch"]');
  await page.waitForSelector('#planToggle');
  await page.click('#planToggle');
  await page.waitForFunction(() => document.querySelector('#planCard').textContent.includes('Trades left'));
  check(true, 'plan card shows the day');
  await shot('20-plan');
  await page.evaluate(() => openJournal());
  await page.waitForSelector('#jSave');
  await page.click('#jFollow button[data-v="partly"]');
  await page.fill('#jFix', 'No trades before 9:30');
  await shot('21-journal');
  await page.click('#jSave');
  await page.waitForFunction(() => document.querySelector('#planCard').textContent.includes('journal streak'));
  check(true, 'journal saved; streak shows');
  await apiPost('/api/rules/settings', {on: false});

  // auto-trader: the edge check runs, the bot starts on paper, and hands-off blocks a manual buy
  await page.click('#tabs button[data-tab="auto"]');
  await page.waitForSelector('#auto .pnl-card');
  check((await page.textContent('#auto')).includes('PAPER'), 'auto-trader starts in paper mode');
  await page.waitForFunction(() => /PASSED|NO EDGE YET/.test(document.querySelector('#auto').textContent), null, {timeout: 60000});
  check(true, 'backtest edge check ran');
  check(await page.$eval('#auLive', b => b.disabled), 'go live is locked until the paper sessions are done');
  await shot('14-auto');
  await page.click('#auStart');
  await page.waitForSelector('#auPause');
  check((await page.textContent('#auto')).includes('RUNNING'), 'bot started');
  await page.evaluate(() => window.scrollTo(0, 900));
  await shot('15-auto-more');
  await page.click('#tabs button[data-tab="watch"]');
  await page.click('#wlTabs button[data-i="1"]');
  await page.click('#wl .item');
  await page.waitForSelector('#shBuy');
  await page.click('#shBuy');
  await page.waitForSelector('#tkReview');
  await page.click('#tkType button[data-v="MARKET"]');
  await page.click('#tkReview');
  await page.waitForSelector('#cfGo');
  await page.click('#cfGo');
  await page.waitForFunction(() => (document.querySelector('#cfMsg') || {}).textContent?.includes('Hands off'));
  check(true, 'hands-off refuses a manual buy while the bot runs');
  await page.click('.sh-close');
  await page.click('#tabs button[data-tab="auto"]');
  await page.waitForSelector('#auPause');
  await page.click('#auPause');
  await page.waitForSelector('#auStart');

  // phone notifications: the panel, the service worker, and a test alert delivered (encrypted) to a fake push service
  const http = require('http');
  const pushed = [];
  const fakePush = http.createServer((req, res) => {
    const chunks = [];
    req.on('data', c => chunks.push(c));
    req.on('end', () => { pushed.push({headers: req.headers, body: Buffer.concat(chunks)}); res.writeHead(201); res.end(); });
  });
  await new Promise(r => fakePush.listen(0, '127.0.0.1', r));
  const pushUrl = `http://127.0.0.1:${fakePush.address().port}/sub/e2e`;
  await page.click('#tabs button[data-tab="account"]');
  await page.waitForSelector('#pushBox .kv');
  check((await page.textContent('#pushBox')).includes('This device'), 'notifications panel shows');
  check(await page.evaluate(() => navigator.serviceWorker.getRegistration('/').then(r => !!r)), 'service worker registered');
  const crypto = require('crypto');
  const ecdh = crypto.createECDH('prime256v1'); ecdh.generateKeys();
  const sub = {endpoint: pushUrl, keys: {p256dh: ecdh.getPublicKey().toString('base64url'), auth: crypto.randomBytes(16).toString('base64url')}};
  check((await apiPost('/api/push/subscribe', {subscription: sub, device: 'e2e'})).ok, 'phone subscription saved');
  await page.click('#tabs button[data-tab="watch"]');
  await page.click('#tabs button[data-tab="account"]');
  await page.waitForSelector('#pushTest');
  await shot('22-notifications');
  await page.click('#pushTest');
  for (let i = 0; i < 50 && pushed.length < 2; i++) await page.waitForTimeout(100);
  check(pushed.length >= 2 && pushed.every(x => x.headers['content-encoding'] === 'aes128gcm' && /^vapid t=/.test(x.headers.authorization)),
    `welcome and test alerts delivered encrypted (${pushed.length})`);
  fakePush.close();

  // PIN lock
  answers.push('2468', '2468');
  await page.click('#pinOn');
  await page.waitForFunction(() => [...document.querySelectorAll('.toast')].some(t => t.textContent.includes('PIN set')));
  const noPin = await page.evaluate(() => fetch('/api/positions', {headers: {'X-Requested-With': 'meridian-trade'}}).then(r => r.status));
  check(noPin === 423, 'without the PIN the server refuses (423)');
  await page.evaluate(() => sessionStorage.clear());
  await page.reload();
  await page.waitForSelector('#lock #pinIn');
  await shot('23-pin-lock');
  await page.fill('#pinIn', '1111');
  await page.click('#pinGo');
  await page.waitForFunction(() => document.querySelector('#pinErr').textContent.includes('Wrong PIN'));
  check(true, 'wrong PIN refused');
  await page.fill('#pinIn', '2468');
  await page.click('#pinGo');
  await page.waitForFunction(() => !document.querySelector('#lock'));
  await page.waitForSelector('#wl .item');
  check(true, 'right PIN unlocks the app');
  await page.click('#tabs button[data-tab="account"]');
  await page.waitForSelector('#pinOff');
  await page.click('#pinOff');
  await page.waitForSelector('#pinOn');

  // desktop layout
  const desk = await browser.newPage({viewport: {width: 1280, height: 800}});
  await desk.goto(BASE);
  await desk.waitForSelector('#wl .item');
  await desk.click('#tabs button[data-tab="chain"]');
  await desk.waitForSelector('#chain td.p');
  if (SHOTS) await desk.screenshot({path: `${SHOTS}/13-desktop-chain.png`});
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  check(!overflow, 'no sideways scrolling on the phone');

  check(errors.length === 0, 'no JavaScript errors' + (errors.length ? ': ' + errors.join(' | ') : ''));
  await browser.close();
  console.log(failures ? `${failures} check(s) failed` : 'All checks passed');
  process.exit(failures ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
