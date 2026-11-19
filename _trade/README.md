# Meridian Trade

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
- **Order ticket:**
  - Buy or sell, as Limit, Market, SL or SL-M
  - Intraday, Delivery (CNC), MTF, or Carry forward (NRML) for F&O
  - Quantity in lots
  - IOC, after-market orders, and slicing for very large quantities
  - **Super order:** entry, target, stop-loss and trailing jump in one order (Dhan's Super Order)
- **Confirm screen before every order:**
  - quantity, price, order value
  - margin needed and money available (Dhan's margin calculator)
  - estimated charges, today's P&L so far
  - for a super order, the money at risk to the stop and the profit and R at the target
- **Orders:** open, executed and all. You can modify or cancel open orders, change a super order's target and
  stop legs, and see Dhan's reason for each rejection.
- **Positions:**
  - live P&L, with Exit (opens a closing ticket), Add and Convert (intraday ↔ delivery/NRML)
  - **Exit all** closes every position at market and cancels open orders; you type `EXIT ALL` to confirm
- **Account:** funds, holdings with P&L, Dhan login, the static IP to set on Dhan, and the order log.

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

## Demo and tests
```
python3 trade.py --demo          # pretend Dhan account on http://127.0.0.1:3050, nothing real is touched
python3 -m unittest discover tests
node tests/e2e.js http://127.0.0.1:3050      # with the demo running; drives the app in headless Chromium
```
`mockdhan.py` is the pretend Dhan:
- prices move at random
- orders fill like on an exchange, and super orders get target and stop legs
- about 50 days of made-up trade history, so Insights has something to show

The unit tests check the exact request bodies the app sends to Dhan, the order checks, the loss limit, and the
insights maths.

## Files
- `trade.py`: web server, order checks, live price feed, insights sync
- `dhan.py`: Dhan v2 REST client (orders, super orders, portfolio, funds, quotes, option chain, charts)
- `instruments.py`: Dhan's scrip master, search, option-chain lookups
- `insights.py`: round trips, statistics, habits
- `autotrader.py`: the auto-trader (swing on MTF), its backtest and edge check
- `static/`: the app (HTML, CSS, JavaScript)
- `deploy/`: droplet installer, updater, web address (Caddy) and systemd units
