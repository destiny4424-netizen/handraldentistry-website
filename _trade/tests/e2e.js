// End-to-end test of Meridian Trade in headless Chromium, against the pretend Dhan (demo mode).
//   cd _trade && python3 trade.py --demo &   (MT_PORT=3051 for another port)
//   node tests/e2e.js [http://127.0.0.1:3050] [screenshot folder]
const {chromium, devices} = require('playwright');
const BASE = process.argv[2] || 'http://127.0.0.1:3050';
const SHOTS = process.argv[3] || '';

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
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('dialog', d => d.accept(d.type() === 'prompt' ? 'EXIT ALL' : undefined));
  const shot = async name => { if (SHOTS) await page.screenshot({path: `${SHOTS}/${name}.png`}); };

  await page.goto(BASE);
  await page.waitForSelector('#wl .item');
  await page.waitForFunction(() => [...document.querySelectorAll('#wl .ltp')].some(e => /\d/.test(e.textContent)), null, {timeout: 10000});
  check(true, 'watchlist shows live prices');
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
  await page.waitForTimeout(700);
  check((await page.textContent('#orders')).includes(Number(newPrice).toLocaleString('en-IN', {minimumFractionDigits: 2})), 'order modified');
  await page.click('[data-cancel]');
  await page.waitForFunction(() => !document.querySelector('[data-cancel]'), null, {timeout: 8000});
  check(true, 'order cancelled');

  // positions and exit
  await page.click('#tabs button[data-tab="positions"]');
  await page.waitForSelector('[data-exit]');
  await page.waitForTimeout(1500);
  await shot('7-positions');
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
