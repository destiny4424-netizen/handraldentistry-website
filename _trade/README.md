# Handral Trading Desk

(Called Meridian Trade until 22 Nov 2026. On the droplet it is still installed under that name: the
`meridian-trade` service, `/var/lib/meridian-trade`, `/etc/meridian-trade`, so nothing needs reinstalling.)

A personal trading app on your own Dhan account. It runs on the droplet and you open it on your iPhone or PC.
It places **real orders**. It needs nothing but Python 3: no packages, no build step.

## What it does
- **Watchlists:** as many lists as you like, with live prices. Indices are shown with their exchange.
- **Search everything Dhan lists:**
  - NSE and BSE stocks, and indices
  - Stock and index futures and options, MCX and currency
  - Examples: `sbin`, `reliance fut`, `nifty 26000 ce`, `banknifty 58000 pe`
- **Instrument page:** price and change, open, high, low and previous close, volume, OI, circuit limits,
  5-level market depth, and candles (1, 5, 15 and 60 minutes, or daily).
- **Option chain** for every index and stock with options:
  - expiries, spot, PCR and lot size
  - OI and change in OI, IV and delta, LTP with % change
  - ITM strikes are shaded and the ATM strike is marked. Tap a price to trade it.
  - The option prices shown come from the live price feed (every 1–2 seconds), so they move like the watchlist;
    Dhan's option chain (1 request every 3 seconds) still gives the strikes, spot and ATM, every 5 seconds
- **Order ticket:**
  - Buy or sell, as Limit or Market
  - Intraday, Delivery (CNC), MTF, or Carry forward (NRML) for F&O
  - Quantity in lots
  - IOC, after-market orders, and slicing for very large quantities
  - **Stop-loss, target and trailing jump, all optional.** Fill in a stop-loss or a target and the order goes as a
    Dhan Super Order with those legs; leave them empty and it's a plain order
- **Confirm screen before every order:**
  - quantity, price, order value
  - margin needed and money available (Dhan's margin calculator)
  - estimated charges, today's P&L so far
  - for a super order, the money at risk to the stop and the profit and R at the target
- **Orders:** open, executed and all. You can modify or cancel open orders, change a super order's target and
  stop legs, and see Dhan's reason for each rejection.
- **Positions:**
  - live P&L, with Exit (opens a closing ticket), Add and Convert (intraday ↔ delivery/NRML)
  - each open position has a big box with its LTP and P&L in large type, green or red, updated with every price tick, and how far price has moved for or against you (on a laptop it sits on the right of the card; on a phone, under the name)
  - **Exit all** closes every position at market and cancels open orders; you type `EXIT ALL` to confirm
- **Stocks you trade join the watchlist by themselves**, at the top: a stock you buy or sell, or the stock under a
  stock future / option you trade, from this app (within seconds) or Dhan's own app (read from Dhan's trade book
  every 2 minutes in market hours). Index, commodity and currency trades aren't added. A stock you take off the
  list isn't put back the same day. The list holds 100 stocks; past that the bottom ones drop off.
- **Menu:** Watchlist, Quick, Positions, Orders, Screener, Account. **Insights** and the **Auto-trader** open from the
  two buttons at the top of Account ("← Account" goes back).
- **Account:** funds, holdings with P&L, Dhan login, the static IP to set on Dhan, and the order log.

## Screener (the Meridian scanner's picks)
The **Screener** tab shows the picks of the Meridian scanner (`_scanner/`, the orderblock-scanner service on the
same droplet), compactly:
- **Intraday:** today's picks, locked in when each first appears, with **entry, stop-loss and exit** (target 1 /
  target 2) and where each stands (waiting, triggered, T1 hit, SL hit, or the result in R).
- **Swing:** today's swing setups (from 11:00; final list at 3 PM) and the swing trades still running.
- **Performance & backtest:** this month's live results next to the scanner's latest backtest (trades, win rate,
  average and total R, profit factor, max drawdown). **Run backtest** starts a new one on the scanner.
- **When the scanner stops scanning:** if its last scan failed, it's still scanning, or (in market hours) it hasn't
  scanned for 25 minutes, a box at the top shows the scanner's own message and the problems from its health report
  (Dhan token, last scan, stocks that couldn't be fetched), with **Scan now**. Picks from an earlier day are labelled.
- **Check a stock:** type any NSE stock at the top and press **Check**. It runs the scanner's own rules
  (`_scanner/intraday.py`, `_scanner/swing.py`) on that stock right away, with the desk's Dhan connection:
  - **Intraday:** if a key level broke today, the scanner's entry, stop-loss and targets, and which of its 5 checks
    pass (all 5 = a scanner pick). If none broke, the nearest level above and below with entry (the level), stop
    (0.6 × the 15-minute ATR) and targets (next levels, or 1.5R / 3R). Before 9:15 it plans today from yesterday.
  - **Swing:** the scanner's daily-chart setup if there is one today.
  - **Trade** opens the ticket with that side, stop-loss and target 1 filled in. Answers are kept for 30 seconds.
- Tap a pick to open the ticket with its side, stop-loss and target 1 filled in. The price stays at the LTP: the
  entry is a trigger ("buy above"), so check that price has crossed it before sending.

**One Dhan token:** the scanner uses this app's Dhan token. The desk renews it as before and hands each new one
to the scanner (`POST /api/keys/shared`, checked every 5 minutes and right after a renewal or a new token); the scanner
then doesn't renew it itself. Account → Dhan login shows "Screener ✓ Uses this token".

How it connects: the desk signs in to the scanner on `127.0.0.1:3040` with the scanner's own password. The desk's
systemd unit loads `/etc/default/orderblock-scanner`, so the password (`OBS_PASSWORD`) arrives with nothing to copy.
Picks are re-read every 20 seconds, performance and backtest every 5 minutes (every 20 seconds while a backtest runs).
`MT_SCANNER_URL` changes the address. `--demo` starts the real scanner in its demo mode on port 3041.

## Live prices
Prices come from **Dhan's live market feed**: one WebSocket connection (`wss://api-feed.dhan.co`) that Dhan pushes
prices down about every second, for everything on screen (watchlist, indices, positions, the open instrument and the
options shown under its chart). It needs the Dhan Data API subscription.

- Only the **price** is taken from the stream. Volume, OI, depth, circuits and the previous close come from Dhan's
  REST quotes, asked every 10 seconds while the stream is up.
- Those quotes also **cross-check** the stream: if most prices differ by more than 3% (and ₹1) twice in a row,
  the stream is dropped for an hour.
- If the stream isn't connected, goes quiet, or is dropped, the app goes back to asking Dhan once a second, as
  before. Nothing else changes.
- Account → Dhan login shows which one is in use ("Live stream from Dhan" or "Asked every second", with the reason).
- `MT_STREAM=0` switches the stream off; `MT_DHAN_FEED` changes its address.

## Charts
- **HTF Key Level Breakout (HTF-KLB):** the owner's own TradingView indicator (Pine Script v6), ported line by line
  to `static/htfklb.js` and drawn on every chart (the **HTF-KLB** button turns it on and off; on by default):
  - key levels = the previous **completed** higher-timeframe candle's high / low (no repainting). The higher timeframe
    follows the script's pairing: 5m chart → 1h, 15m → 4h, 1h → Day (1m → 15m, Day → Week), or pick one in ⚙.
  - a long when a decisive candle (body > 0.8 × ATR) opens at or under the level + 0.1 × ATR and closes above it
    (shorts mirrored), once per level; optional retest. Entry at that close, stop 1.5 × ATR, target = the higher
    timeframe's ATR; refused when the stop is under 1 × ATR or R:R is outside 7–9 (the script's defaults). Trail,
    max 60 bars, 3-bar cooldown; fees (0.0472% a side) come off the R.
  - the chart shows the HTF high / low lines, entries ("LONG 1:7.4") and exits ("TP +7.2R", "SL −1.1R"), the open
    trade's Entry / SL / TP lines, and the script's Position / Entry / SL / TP table with this chart's results.
    **Trade** opens the ticket with that side, SL and TP filled in. ⚙ has every input of the script.
  - closed candles only, like the script's alerts. ATR is Wilder's (TradingView's `ta.atr`). The target needs 14+
    higher-timeframe candles on the chart: the panel says so when there are fewer.
  - tested on made-up candles in `tests/klb.test.js` (run by `tests/test_klb.py`).
- **HTF-KLB on every F&O stock (Screener → HTF-KLB 15m):** the 15-minute candles the Market lists already read
  every 15 minutes (now 12 days of them, enough for the 4-hour ATR) also go through the indicator's server copy
  (`klb.py`, checked trade for trade against the chart's `static/htfklb.js` in `tests/test_klb.py`). No extra Dhan
  requests. The list shows today's **crossings**: a 15-minute close beyond the previous 4-hour candle's high (green)
  or low (red). Each is either a **signal** (all the rules pass: entry, SL, TP, R:R and how it's doing) or says why
  not (body too small, R:R outside the band, stop too tight, level already fired…). "Signals only" filters it.
  Tap one to open that stock on the 15m chart. A new signal on the candle that just closed sends a phone alert
  (Account → Notifications → "HTF-KLB signals"). ⚙ on any chart saves the inputs on the server, so the chart and
  the scan always use the same ones; changing them re-checks the same candles at once.
  - **Good volume only:** a stock is listed only if, from its own 15-minute candles (the earlier sessions in the
    12 days), it trades ₹200 crore and 5 lakh shares a day on average and today is at 0.8× its usual pace for the
    time of day or more (the Market's "good volume" rule; the header says how many were left out).
  - **Once a stock a day** (owner: a stock given at 9:30 isn't given again): its first signal of the day, or before
    one, its first crossing. One alert a stock a day.
  - **Intraday checks** on every row (✓ / ✗): breakout candle volume 1.5× the usual for that time of day; close on
    the right side of the day's VWAP; with NIFTY's direction (NIFTY then vs its open; one extra request a scan);
    before 2 PM; and for signals, price not already 1R past the entry. A signal passing all of them is
    **★ Strong**; strong ones sort first and the alert marks them. The indicator's own rules are unchanged.
- **The chart on every instrument** uses TradingView's open-source **Lightweight Charts** v4.2.3 (Apache 2.0; an
  unmodified copy in `static/vendor/` with its licence and NOTICE, the same file the scanner uses). Candles from Dhan
  (1, 5, 15 or 60 minutes, or daily) with volume. Pinch or scroll to zoom, drag to go back in time, crosshair for
  prices. Times in IST. With no live quote (e.g. after the close), the header shows the last traded price.
- **Under the chart:** the options of the stock or index (or of the underlying, for a future or an option): the ATM
  strike with 2 in-the-money and 2 out-of-the-money strikes, calls on the left and puts on the right. Each has a
  **Buy** button that opens that option's order ticket. Up to 3 expiries, refreshed every 5 seconds. Market depth and
  the Open/High/Low boxes were removed to keep the screen simple. (An "Open in TradingView" link was tried and
  removed: on the iPhone it opened the website, not the app.)
- **Option chain sources:** Dhan's option chain is asked with the index's segment; for a stock, NSE_EQ first and then
  NSE_FNO, as the scanner does. The segment that works is remembered. If Dhan's chain still has nothing, the chain is
  built from the options' own quotes (one request), so stocks always show prices and Buy buttons ("prices from each
  option's own quote" under the table).
- **Option chain** (on the stock's or index's screen): by default the ATM strike with 2 in-the-money and 2
  out-of-the-money strikes on each side, with Buy buttons. "ATM ±2" switches to all strikes, and the choice is
  remembered on the phone.

## Market lists (in the Watchlist tab)
There are no separate Market or Options tabs any more: the Watchlist tab has everything. It shows the index cards and
breadth at the top, then one row of chips, with your watchlists first and then the market lists below. Tap any stock
to open it and trade. The full option chain is on each stock's or index's own screen, under the chart, with an
"ATM ±2 / All strikes" switch.

The market lists cover the liquid F&O stocks (about 200, those with futures).
- **Indices** (NIFTY, BANK NIFTY, SENSEX) and **breadth** (how many are up or down).
- **Top gainers / top losers:** 15 each by % change today, with volume against usual.
- **15-min breakouts:** the first 15-minute candle (9:15–9:30) is the range. A later 15-minute candle closing above
  its high (or below its low) is a breakout (breakdown), and so is a close through yesterday's high or low. Each
  shows the time, how far beyond the level, and the candle's volume against the day's average. Newest first.
  Re-checked 20 seconds after every 15-minute candle closes; outside market hours it shows the last session.
- **Volume shockers:** today's volume against the 20-day average, adjusted for how much of the session has gone
  (2× or more).
- **52-week high / low:** new ones today, and stocks within 2% of one.
- **OI build-up:** current-month futures, price and open interest against yesterday: long build-up, short
  build-up, short covering, long unwinding.
- **Sectors:** the average % change of each sector's F&O stocks (the scanner's sector list).

**"F&O · good volume only"** (on by default, for the market lists and the watchlists):
- **Good volume** is the scanner's liquidity rule: over the last 20 sessions the stock traded on average at least
  ₹200 crore and 5 lakh shares a day, and today it has traded at least 0.8× its usual volume for this time of day.
  "Change" edits the three numbers (saved in settings.json as `liquidity`).
- **Market tab:** gainers, losers, breakouts, volume shockers and 52-week lists show only stocks that pass. Rows show
  their ₹ crore a day. Switch the filter off to see every F&O stock.
- **Watchlist:** stocks that aren't F&O, or are thin today, are hidden, with "N hidden · Show all". Indices, futures
  and options are always shown. Rows are tagged F&O, with today's volume against usual ("1.4× vol"). Search marks
  F&O stocks too.

Prices come in one request per kind, refreshed every 20 seconds while the tab is open. The 15-minute candles take
about a minute for all stocks. Yesterday's futures OI is read once a day. The daily candles (20-day volume, 52-week)
are the auto-trader's, downloaded each morning. `movers.py`.

## MCX (commodities)
On MCX, Dhan counts the quantity in **lots**, and a price is quoted for a smaller unit than the lot. For example,
SILVER100 is priced per 10 g but a lot is 100 g, so a ₹1 move is ₹10 per lot. `mcx.py` has the contract sizes:
GOLD, GOLDM, GOLDGUINEA, GOLDPETAL, GOLDTEN, SILVER, SILVERM, SILVERMIC, SILVER100, CRUDEOIL(M), NATURALGAS,
NATGASMINI, COPPER, ZINC(MINI), LEAD(MINI), ALUMINIUM, ALUMINI.
- **Positions:** Dhan's API sends the open P&L of an MCX position per unit, while its app shows it in rupees.
  SILVER100 came through 10 times too small. Open MCX P&L is now multiplied before it is shown, or used for the loss
  limit and the alerts. It is checked against the live price, so the multiplier is never applied twice. Closed MCX
  P&L already comes in rupees.
- **Order ticket:** the quantity is in lots ("1 lot of 100 g (price per 10 g) · ₹10 per ₹1 move"). The value, the
  money at risk and the Rules-mode sizing all include the contract size.
- **Unknown contracts:** an MCX contract that isn't in the table can't be ordered from the app.
- **Insights:** MCX round trips are in rupees too.

## Phone notifications and the PIN lock (Account, at the top)
**Notifications** reach the phone even when the app is closed. They use Web Push: iPhone (iOS 16.4+) only in the
home-screen app, so first Safari → Share → Add to Home Screen, open the app from that icon, then Account → Turn on
notifications. Checked every 30 seconds:
- **Orders:** an order filled (a stop-loss or target leg says so) or was rejected, with Dhan's reason.
- **P&L:** today's P&L crosses another loss step (every ₹5,000 by default), and the daily loss limit.
- **Auto-trader:** it buys, sells, stops itself, or has a problem.
- **Rules:** an order waiting for confirmation is sent, or dropped.
- **Routine:** 9 AM "plan your day" and 3:45 PM "journal time" (only if the journal isn't written), weekdays.

Each kind can be switched off, and the loss step changed. "Send a test" checks the phone. The last alerts and a
delivery log are shown in the panel.

How it works:
- Messages are encrypted for the phone (RFC 8291) and signed with the server's own VAPID key (RFC 8292), made on
  first use and kept in settings.json.
- Subscriptions are kept in `push.json`, and the alert state in `alerts.json`.
- It needs `python3-cryptography`: the installer and the updater install it. Without it the app works as before and
  the panel says notifications aren't available yet.

**PIN lock:**
- A 4–8 digit PIN is asked when the app opens, and again after 5 minutes in the background. The server checks it
  too: without a PIN token (valid 8 hours) every API call is refused, not just hidden.
- 5 wrong PINs lock it for 15 minutes. "Forgot PIN?" clears it with the sign-in password.

## Rules mode (removed)
**Removed from the app on 25 Nov 2026 at the owner's request.** The server switches it off when it starts, drops any
order still waiting for a reaction, and the settings call can no longer switch it on. Orders are no longer checked
against levels, and a stop-loss is optional. The journal (Account) still works. The old behaviour is kept below for
reference; `rules.py` still holds it.

Every manual order that **opened** a position had to follow four rules; closing a position was never blocked.

1. **Level first.** The ticket lists the key levels of the stock, or of the underlying for futures and options:
   - yesterday's high and low, and last week's high and low
   - daily swing highs and lows
   - imbalance zones (FVG, inverse FVG, BPR, gap, from the scanner's `swing.py`)

   Levels within 0.15 ATR of each other are merged ("Yesterday's low +2"). You pick the one you're trading. Only
   levels within 0.3 of a daily ATR of the price can be picked. **No level near the price, no trade.**
2. **Wait for the reaction** (on by default). The order waits at the server, and nothing goes to Dhan, until a closed
   15-minute candle of the underlying touches the level and closes back beyond it in the trade's direction:
   - for a buy, a green candle closing above a support level
   - for a sell, a red candle closing below a resistance level

   Then it is sent at market, with its stop. It is resized if needed, so the risk still stays within 1%. If the stop
   trades first, or nothing confirms by 3 PM, it is dropped. Waiting orders are listed under Orders and can be
   cancelled.
3. **Risk before the click.** A stop-loss is required. The quantity is worked out from it: 1% of capital (₹1,500
   of ₹1.5 lakh) at risk, in whole lots. You can trade less, never more. Entries are Limit or Market, sent as a
   Super order with the stop. The "trades a day" limit (Account → Safety, default 5) is enforced too.
4. **Same routine every day.**
   - **The plan card at the top of the Watchlist:**
     - trades left and loss limit left
     - yesterday's "fix tomorrow" note
     - the auto-trader's setups
     - orders waiting for confirmation
     - which watchlist stocks are at a level right now
   - **After 3:30 PM, a two-minute journal:**
     - the day's trades, each marked with its level, its stop, whether it waited for confirmation, or "placed
       outside this app"
     - did you follow your rules (yes / partly / no), what went well, one thing to fix tomorrow, and how you felt
   - The journal builds a streak of days. Kept in `rules.json` in the data folder.

## Auto-trader (the Auto tab)
It trades for you, within hard limits. It is built for ₹1.5 lakh of capital, the top one or two setups only,
and MTF.

**The setup** is the scanner's Swing rule set (`_scanner/swing.py`, imported as is):
- a daily-chart uptrend
- price back in an imbalance zone (FVG, inverse FVG, BPR or gap) and holding it
- a price-action trigger: a hammer, a bullish engulfing or a strong close

The bot only buys, because MTF can't be sold short.

**Each trading day:**
- **3:10 PM:** it scores every liquid F&O stock on today's forming daily candle and keeps only the best 1–2 that
  reach the minimum score. The backtest chooses that score. A setup stays valid for 3 sessions.
- **9:16 AM:** it places a buy stop-limit order on MTF, just above the trigger day's high. It doesn't chase: if the
  stock opened more than half the risk above the entry, it waits.
- **When a buy fills:** a stop-loss for the whole position goes to Dhan at once, as a **Forever (GTT)** order. It
  stays at the exchange even if the server is down. If Dhan refuses the GTT, a day stop-loss order is used instead
  and placed again every morning.
- **Every 20 seconds while the market is open:**
  - At T1, half is sold and the stop moves to cost. At T2, the rest is sold.
  - After 10 sessions, anything left is sold at 3:15 PM.
  - Before it sells, it cancels the stop and checks whether the stop had already filled, so a position is never
    sold twice.
  - If you sell a bot position yourself, the bot notices and books it.

**Size and limits:**
- 1% of capital is at risk per trade: shares = risk ÷ distance to the stop.
- Each position is at most 1× capital, and all positions together at most 2×. That's where MTF's margin is used.
  The leverage doesn't change the risk: the stop does.
- At most 2 open positions.
- After a drawdown of 8% of capital, the bot stops itself.
- **Settings and mode are locked while the market is open (9:15–3:30).** Pause and Close all always work.
- **Hands off** (on by default): while the bot is on, manual orders in this app can only close positions. Pausing
  mid-day doesn't lift it until the close.

**Edge check (backtest):**
- Once a week (Saturday), and at the very start, it downloads about 2 years of daily candles for every liquid F&O
  stock. It replays the bot exactly: same entries, stop first when a day touches both stop and target, 0.25%
  delivery costs plus MTF interest (0.04% a day).
- It tries minimum scores 0–70 on the earlier two thirds of the days and picks the best by ₹ a day. Then it checks
  that pick on the last third, which wasn't used to choose. The pick passes only if all of these hold:
  - the later days made money
  - at least 20 trades on the later days
  - a profit factor of 1.15 or more on the later days
  - the later days' profit per trade is clearly above zero (t-stat ≥ 1.65)
  - most of the other scores also made money on the later days
- On 10 sets of pure random prices (no edge exists), it rejected all 10.

**Paper first:** the bot starts in PAPER mode: live prices, no orders. **Go live** unlocks only when:
- the edge check has passed
- 10 paper sessions are done
- you type `LIVE`, after the close

If a later weekly backtest shows no edge, the bot goes back to paper once it has no open positions.

The bot's state and log are in `autotrader.json` in the data folder. Its daily candles are in
`daily-history.json`.

## Trailing the stop-loss (Positions)
Every open position shows its working stop-loss: a pending SL / SL-M order on the closing side, or a Super order's
stop-loss leg.
- **Each − / + tap moves the stop at Dhan by itself.** There is no Move button.
- Quick taps are combined: the app waits ¾ of a second after the last tap and sends the final level once. The
  exchange limits how many times one order can be modified, so 100 → 103 in three taps is one change.
- "✓ at Dhan ₹…" confirms each change. If Dhan refuses one, the screen goes back to the stop Dhan still has and says
  so.
- **"step"** changes the size of a tap. It starts at about 0.2% of the price (₹0.05 to ₹500).
- An SL-limit order keeps the same gap to its limit price.
- The line above shows what the stop means in rupees: "₹X at risk" or "locks ₹X profit", and how far it is from the
  price (with the contract size for MCX).
- **No stop:** a position without one says so. − / + choose a level, then **Add stop** (one tap, since it places a new
  order) puts a stop on the whole position: an SL-M for stocks and futures, a **stop-limit for options**.
- **Options: stop-limit, not stop-market** (owner's choice, after an option stop filled at market). Once the trigger
  trades, the order becomes a limit 1% past it (at least 2 ticks), e.g. trigger ₹1,003 → sells down to ₹992.95, so a
  sudden spike can't fill it far away. If price jumps straight through the limit it may not fill: watch fast falls.
  An older option SL-M becomes a stop-limit the next time it's moved. The card shows "limit, sells down to ₹X".
- **No stop without a live price:** the stop's side is checked against the price from the live feed (under 15 s old)
  or, failing that, a fresh quote from Dhan; with neither, the stop isn't sent.
- **Safety:** a tap that would move the stop past the price (it would fill at once) is refused on the phone, and the
  server refuses such a stop too.

## Safety checks (before anything reaches Dhan)
- The instrument, side, product, order type and quantity must make sense. F&O quantity must be a whole number of
  lots. Prices are rounded to the tick. A stop-loss trigger must be on the right side of its price. A super order
  needs the target and stop on the right sides of the entry. The price must be inside today's circuit limits.
- **Double-tap guard:** the same order sent again within 4 seconds is refused.
- **Largest single order** (default ₹10 lakh): a bigger order needs an extra "I'm sure" tick.
- **Daily loss limit** (off until you set it): once today's P&L (booked plus open) is past it, only orders that
  close a position go through.
- **Warnings** on the confirm screen:
  - market orders on options
  - a limit price far from the last price
  - a big number of lots
  - a super order without a stop
  - not enough funds
- **Dhan kill switch** button: stops all trading on the account for the rest of the day. You type `STOP` to confirm.
- **Order log:** every order sent, failed, modified or cancelled is written to `orders-log.jsonl` in the data
  folder and shown under Account.

## Insights (your own trading)
Every 2 minutes in market hours the app reads today's trades from Dhan. Once a day it imports your trade history
(the last year at first), with Dhan's real charges. Until the history has a day's charges, they are estimated.
Fills are kept in `trades.db`.

- **Trades:** fills are grouped into round trips, from flat back to flat, per instrument and product. Each round
  trip shows its P&L after charges, how long it was held, and whether you added to a losing position.
- **Numbers:**
  - net P&L, win rate, average win and loss, profit factor, expectancy
  - charges, worst drawdown, and a P&L curve
- **Breakdowns:** by what you trade (e.g. index options bought or sold, stocks intraday or delivery), the time you
  entered, the weekday, how long you held, long vs short, and best and worst symbols.
- **Habits, with what they cost you:**
  - too many trades in a day
  - jumping back in within 15 minutes of a loss
  - bigger size after a loss
  - holding losers longer than winners
  - small wins with big losses
  - a few big losses doing most of the damage
  - averaging down
  - the first 15 minutes, and intraday entries after 2:30
  - options on expiry day
  - charges eating the profit
  - your best and worst kind of trade
- **Today's report card:** the day's trades, P&L and charges, and the day's rule breaks.

## Running it on the droplet
```
curl -fsSL https://raw.githubusercontent.com/destiny4424-netizen/handraldentistry-website/main/_trade/deploy/install.sh | sudo bash
```
At the end it prints the address (`https://trade.<droplet-ip>.sslip.io`, or `trade.handraldentistry.com` if a DNS
**A** record `trade` points at the droplet), the password, and the droplet's IP.

1. **Static IP (needed for orders):** SEBI's rules make Dhan accept API orders only from a static IP you register.
   On web.dhan.co → My Profile → **Access DhanHQ APIs**, set the static IP to the droplet's IP that the installer
   printed.
2. Open the address, sign in, go to **Account → Dhan login**, and paste your Client ID and access token (from the
   same Dhan page). The app renews the token by itself before it expires, so you paste it only once. Prices,
   charts and the option chain need Dhan's **Data API** plan, which the scanner already uses.
3. **Start with one small order** (e.g. 1 share at a limit price) and check it in Dhan's own app. This app was
   tested against a simulated Dhan built from Dhan's official Python library (dhanhq 2.3), not against your live
   account.

It keeps itself up to date like the scanner: every 5 minutes it checks GitHub, installs a new `_trade/`, and rolls
back if the new version doesn't start.
- Address and password: `sudo cat /etc/meridian-trade/login.txt`
- New password: `curl -fsSL .../_trade/deploy/install.sh | sudo TRADE_NEW_PASSWORD=1 bash`
- If something looks wrong: `journalctl -u meridian-trade -u meridian-trade-update -n 50`

Your data (Dhan token, settings, trades, order log) stays in `/var/lib/meridian-trade` on the droplet. None of it is
in this repository.

**Dhan tokens:** if you generate a new access token on web.dhan.co, older tokens may stop working. If the scanner
then shows a token error, paste the new token there too.

## Zerodha desk (stand-alone, for a Zerodha account)
The same desk can run for a **Zerodha** account (e.g. a family member's), completely separate from the Dhan desk:
its own copy of the code, its own system user, data, password, web address and updater, and **no link to the Dhan
desk, its token, or the Meridian scanner** (its Screener tab is hidden). It can run on its own droplet or next to the
other apps on this one; nothing of one can read the other's data. It is the same code with `MT_BROKER=zerodha`:
`kite.py` talks to Zerodha's **Kite Connect** API and hands the rest of the app the same shapes `dhan.py` does, so the
watchlist, ticket, positions, stop-loss buttons, Quick, Insights, alerts and the auto-trader work the same. The app
says "Zerodha" everywhere and shows a **Zerodha** tag next to the name.

**Install** (on its own droplet, or on this one; it doesn't need the Dhan desk):
```
curl -fsSL https://raw.githubusercontent.com/destiny4424-netizen/handraldentistry-website/main/_trade/deploy/install-zerodha.sh | sudo bash
```
It installs to `/opt/handral-zerodha` (code), `/var/lib/handral-zerodha` (data, user `zerodha`), service
`handral-zerodha` on port 3051 and its own updater `handral-zerodha-update.timer`, and prints the address
(`https://zerodha.<droplet-ip>.sslip.io`, or `ZERODHA_DOMAIN=...`), the password, and the Kite Connect steps:
1. On **developers.kite.trade** (logged in as the account holder) create a **Kite Connect** app (Zerodha charges a
   monthly fee for it). Redirect URL: `https://<the address>/kite/callback`. Static IP: the IP of the droplet it
   runs on (SEBI's rules: Zerodha accepts API orders only from the registered IP).
2. In the desk: **Account → Zerodha login**, paste the app's **API key** and **API secret**, Save.
3. Tap **Log in with Zerodha**: Zerodha's own login page (password + TOTP go to Zerodha, never to this app), then back
   to the desk. **Zerodha ends every login at 6 AM**, so this is once each trading morning; until then the banner
   says "Not logged in to Zerodha". (Dhan's token renews itself; Zerodha's can't.)

What differs from the Dhan desk:
- **Super orders** (stop-loss / target / trailing from the ticket): Zerodha has none, so the desk runs them. The entry
  goes as a normal order; once it fills, the stop-loss goes in at Zerodha as a real SL order (options: stop-limit,
  as on Dhan). The target is watched every 2 seconds: when the price reaches it, the stop order is changed into a
  LIMIT at the target, so only one exit order ever sits at Zerodha. Trailing moves the stop by the jump each time
  price moves that much your way. If the position is closed some other way (e.g. in Kite), the stop is cancelled so
  it can't open a new position. **The target needs the desk running** (it does, on the droplet); the stop-loss is
  at Zerodha from the moment the entry fills.
- **Option chain:** Zerodha has no chain API; the chain is built from the options' own quotes (prices, OI, bid/ask;
  no IV or greeks).
- **Kill switch:** not in Zerodha's API. Account shows where it is in Kite. **Exit all** works (the desk cancels open
  orders, then closes each position at market).
- **Insights:** Kite Connect has no trade history API, so Insights fills up from the first day the desk is used.
- **Live prices:** Zerodha's WebSocket ticker (`kitestream.py`), with the same fallback to asking every second.
- **Market orders** carry `market_protection=-1` (Zerodha's own protection); if Zerodha ever refuses the field, the
  desk stops sending it.
- **Screener:** none (it would need the Meridian scanner, which belongs to the Dhan setup). Setting `MT_SCANNER_URL`
  on purpose would bring it back.

Its data stays in `/var/lib/handral-zerodha` (settings with the Kite keys and the day's login, order log, trades).
- Address and password: `sudo cat /etc/handral-zerodha/login.txt`
- If something looks wrong: `journalctl -u handral-zerodha -u handral-zerodha-update -n 50`
- Its updater (`deploy/zerodha/`) and the Dhan desk's (`deploy/update.sh`) don't know about each other.

## Demo and tests
```
python3 trade.py --demo          # pretend Dhan account on http://127.0.0.1:3050, nothing real is touched
python3 -m unittest discover tests
node tests/e2e.js http://127.0.0.1:3050      # with the demo running; drives the app in headless Chromium

MT_BROKER=zerodha MT_PORT=3060 MT_DATA_DIR=/tmp/kite-demo python3 trade.py --demo   # the Zerodha desk, pretend Zerodha
node tests/e2e_kite.js http://127.0.0.1:3060
```
`mockkite.py` is the pretend Zerodha: Kite Connect's paths, fields and answers in front of the same pretend market
(`tests/test_kite.py` checks the adapter, the super orders, the instrument list, the login and the ticker against it).
In the demo the market-hours lock on Rules settings is off, so everything can be tried at any time.
`mockdhan.py` is the pretend Dhan:
- prices move at random
- orders fill like on an exchange, and super orders get target and stop legs
- about 50 days of made-up trade history, so Insights has something to show

The unit tests check the exact request bodies the app sends to Dhan, the order checks, the loss limit, and the
insights maths.

## Files
- `trade.py`: web server, order checks, live price feed, insights sync
- `screener.py`: the Screener tab's link to the Meridian scanner (sign-in, picks, performance, backtest)
- `stream.py`: Dhan's live market feed (WebSocket): subscriptions, the binary price packets, reconnects
- `wsclient.py`: a small WebSocket client (standard library only)
- `dhan.py`: Dhan v2 REST client (orders, super orders, portfolio, funds, quotes, option chain, charts)
- `kite.py`: Zerodha Kite Connect client in Dhan's shapes, with the desk's own super orders (Zerodha desk)
- `kitestream.py`: Zerodha's live ticker; `mockkite.py`: the pretend Zerodha
- `instruments.py`: Dhan's scrip master (and Zerodha's instrument list), search, option-chain lookups
- `insights.py`: round trips, statistics, habits
- `autotrader.py`: the auto-trader (swing on MTF), its backtest and edge check
- `rules.py`: Rules mode: key levels, the order checks, confirmation, plan and journal
- `mcx.py`: MCX contract sizes; fixes the open MCX P&L Dhan sends per unit
- `movers.py`: the Market tab (gainers, losers, breakouts, volume, 52-week, OI, sectors)
- `webpush.py`: Web Push (VAPID + aes128gcm); `alerts.py`: what gets notified; `static/sw.js`: the service worker
- `static/`: the app (HTML, CSS, JavaScript)
- `deploy/`: droplet installer, updater, web address (Caddy) and systemd units; `install-zerodha.sh` and
  `deploy/zerodha/` (service, updater) for the stand-alone Zerodha desk
