# GoldBot (XAUUSD bot for MetaTrader 5)

`GoldBot.mq5` is an Expert Advisor: a bot that runs inside MetaTrader 5 (MT5) and places gold trades
by itself on an Exness MT5 account. The folder is not published by GitHub Pages.

**It runs only in MT5 on a computer** (the Windows laptop or a VPS), never in the MT5 phone app.
The phone app is where you watch the trades and get its notifications.

## What it trades
- **Trend (1-hour candles):** buys only when the 50-EMA is above the 200-EMA and price is above the 200-EMA.
  Sells only in the opposite case.
- **Entry (15-minute candles):** a candle dips to the 20-EMA and closes back above it as a green candle,
  with RSI above 50. Sells are the mirror image: a red candle that touches the 20-EMA from below and closes
  under it, with RSI below 50. It enters at the open of the next candle.
- **Stop-loss:** beyond the last 3 candles' low (high for a sell), plus 0.2 × ATR. The stop is at least
  0.5 × ATR away. If it would be more than 2.5 × ATR away, the trade is skipped.
- **Target:** 2R (twice the risk). At 1R in profit the stop moves to cost.
- **One trade at a time.**

These are sensible starting rules, not a proven money-maker. Find out with the backtest and the
demo test below before you risk real money.

## Safety limits (all in the bot's settings)
| Setting | Default | What it does |
|---|---|---|
| AllowRealAccount | false | Refuses to start on a real account until you change this |
| RiskPercent | 1% | Lot size is worked out so a stop-loss loses this much of the balance |
| MaxDailyLossPercent | 3% | When today's loss reaches this, closes everything and stops until tomorrow |
| MaxTradesPerDay | 4 | At most this many new trades a day |
| MaxSpread | $0.40 | Skips the trade if the spread is wider |
| Trading hours | 07:00–20:00 | Server time (Exness = GMT, so 12:30–01:30 IST); nothing at weekends |
| Friday | 18:00 / 20:00 | No new trades from 18:00; everything closed at 20:00 |
| AvoidNews | on | No new trades from 30 min before to 30 min after high-impact USD news (Fed, CPI, jobs) |

Every trade is sent with its stop-loss and target attached, so they still work if the computer turns off.

**Account size.** If even the smallest lot (0.01) would risk more than RiskPercent, the bot skips the
trade and tells you why. On a standard account 0.01 lot of XAUUSD is $1 per $1 move in gold, and a
typical stop is $5–15. A 1% risk therefore needs roughly **$500–1,500**. With a small amount (like the
$41 in the reel), use an Exness **Cent** account (symbol `XAUUSDc`), where 0.01 lot is 100× smaller.

**On the chart** the bot shows its status and today's P/L, with two buttons:
- **STOP BOT:** no new trades; open trades keep their stop and target. Click again to start it.
- **CLOSE ALL + STOP:** closes the bot's trades and stops it.

Every open, close and skipped trade is also written to `MQL5/Files/GoldBot_journal.csv`.

## Setup
1. **Exness:** in the Personal Area, open a **Demo** MT5 account (Standard or Standard Cent).
2. **MT5 on the laptop:** download MetaTrader 5 for Windows from the Exness Personal Area and log in to the
   demo account.
3. **Install the bot:**
   1. In MT5: **File → Open Data Folder**, then go into `MQL5 → Experts`.
   2. Copy `GoldBot.mq5` into that folder.
   3. Press **F4** to open MetaEditor, open `GoldBot.mq5` and press **F7** (Compile). It must say **0 errors**.
4. **Backtest:**
   1. In MT5: **View → Strategy Tester**.
   2. Expert: `GoldBot`. Symbol: `XAUUSDm` (or `XAUUSDc`). Timeframe: M15.
   3. Modelling: **Every tick based on real ticks**. Dates: the last 12 months. Deposit: what you plan to trade with.
   4. Press Start. Look at the net profit, the maximum drawdown, the profit factor (above 1.3 is decent) and
      the number of trades.

   The news filter can't run in the tester, so live results will differ a little. Don't keep changing
   settings until the backtest looks perfect; that only fits the past.
5. **Demo test for 2–4 weeks:**
   1. Open an `XAUUSDm` chart on **M15** and drag GoldBot onto it.
   2. Tick **Allow Algo Trading** and press OK.
   3. Make sure the **Algo Trading** button in the toolbar is green.
6. **Notifications on the iPhone:**
   1. In the MT5 iPhone app: **Settings → Messages**. Copy the **MetaQuotes ID**.
   2. On the laptop: **Tools → Options → Notifications**. Tick *Enable Push Notifications*, paste the ID and press Test.
7. **Running all the time:** MT5 must stay open. Either keep the laptop on, or rent a VPS.
   - **MetaQuotes Virtual Hosting:** in MT5's Navigator, right-click the account → *Register a Virtual Server*.
     It costs about $15 a month, then *Migrate* the chart with the bot.
   - **Exness:** offers a free VPS to accounts above a minimum deposit.
8. **Going live:** only if the backtest and the demo both made money. Attach the bot to the real
   account's chart and set **AllowRealAccount = true**. Start with the smallest risk.

## Be aware
- Most retail traders lose money on leveraged gold. A bot follows its rules without emotion, but it
  doesn't make bad rules profitable.
- In India, RBI allows forex trading only through authorised Indian brokers on Indian exchanges, in INR pairs.
  Trading XAUUSD CFDs with an offshore broker such as Exness from India can breach FEMA. Check before using real money.
