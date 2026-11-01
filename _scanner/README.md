# OrderBlock Scanner

**Running it in the cloud (recommended):** see "OrderBlock Scanner" in the repository's main
README. One command on the droplet, then it runs 24/7 and you open it from your iPhone anywhere.
The steps below are for running it on a Windows PC instead.

A live scanner that checks every NSE F&O stock for order blocks and liquidity using
your Dhan account, and ranks the strongest bullish and bearish setups.

- **Intraday (opens first):** the scans at 9:30, 9:45, 10:00 and 10:15 put all liquid F&O stocks through a
  series of filters. The best 5–6 trades are shown with **entry, stop-loss, target 1, target 2, R:R and
  risk %**, plus a stock option (call or put) with its own entry, SL and targets. From 10:30 the tab shows
  **Today's intraday list**: every stock given that morning, once each, to trade when its entry breaks. A flow chart
  shows how many stocks passed each filter, next to a sector strength table.
- **Market panel (top of Intraday):**
  - **NIFTY 50 and BANK NIFTY** each get a **Bullish / Bearish / Sideways** verdict with the reasons: above or below
    today's open and yesterday's close, the 20-EMA on 5-minute candles, and breaks of the first-15-minute range.
  - **India VIX** is shown against its own 20-day average. **Breadth** is how many F&O stocks are up vs down.
  - The **top 5 F&O gainers and losers** come with relative volume.
  - A one-line verdict says which side to favour.
  - Reminders: NIFTY weekly expiry day (from Dhan's contract list), the first 15 minutes, and no new entries after 3 PM.
  - The header shows NIFTY's direction on every tab.
  - Each pick card shows whether it is **with or against NIFTY**, above or below **VWAP**, its **relative volume**
    and its move **vs NIFTY** today.
- **Event risk (Market panel):** Fed (FOMC) and RBI policy days, the Budget, MCX crude oil, USD/INR and world
  headlines (war, sanctions, tariffs...) give a Low / Medium / High risk level. Picks are tagged when crude, the
  rupee, an RBI day or the company's quarterly results affect them, with the company's latest news.
- **Today's trades:** every intraday pick is **locked in the moment it first appears**, with its entry, stop and
  targets frozen. Later scans can bring new picks, but earlier ones never disappear. Each trade is followed
  on 5-minute candles:
  - Waiting for trigger → Triggered → T1 hit (half booked, stop moved to cost) → T2 hit, or SL hit
  - Not triggered by 3 PM, or closed at 3:15 PM
  - The result is in R (multiples of the risk), with a running total and win rate for the day
  - Saved on the server, so it survives restarts
- **Ranked lists:** the top 20 bullish and top 20 bearish stocks, strongest first.
- **Strength map:** how many stocks are choppy and how many are strong, on each side.
- **Chart:** recent candles with the order block zone, support, resistance and the liquidity level.
- **HTF Key Level Breakouts (15 min):** a second tab lists stocks whose 15-minute candle has closed
  through a higher-timeframe level. The levels are the previous day high/low, the previous week high/low,
  and daily swing highs/lows from the last 60 days.
- **Auto-scan:** every 15 minutes from 9:30 to 14:00 for intraday breakouts (BTST worked out in each scan
  from 11:00), then 14:30 and 15:00 (IST, Mon–Fri), 20 seconds after the candle closes. After 14:00 the
  day's trades are followed every 5 minutes. The header shows the time of the next scan.
  Use **Scan now** to rescan straight away.
- **Gold & Silver (MCX, positional):** a tab for long futures held 3–4 months. Every 30 minutes while MCX is
  open it reads about 2 years of daily candles of the near GOLD and SILVER futures and gives each metal a verdict:
  - **BUY**: uptrend (price and the 50-day average above the 200-day) and price is near the 50-day average, or a
    fresh breakout to a 60-day high from a tight base. **BUY ON DIP**: uptrend but stretched; a limit-buy zone is shown.
    **WAIT**: above the 200-day but the 50-day hasn't crossed it. **AVOID**: downtrend.
  - A score out of 100 from 8 checks: trend, slope of both averages, 20-week EMA, 3-month return, weekly RSI, stretch.
  - Entry, stop-loss (under the 50-day average or the 20-day low, 1.5–4 ATR away), targets at 2R and 3R, and a
    trailing rule (20-week EMA on weekly closes).
  - Risk and value per lot for GOLD, GOLDM, GOLDGUINEA, GOLDPETAL, SILVER, SILVERM and SILVERMIC; enter your capital
    and risk % to see how many lots fit.
  - The gold/silver ratio (which metal is cheaper), the USD/INR trend, and which expiry to buy with the date to roll
    it (7 days before expiry, ahead of MCX's delivery period).
- **Breakout alerts:** turn on **🔔 Breakout alerts** and allow notifications. Each new breakout then
  pops up on your screen with a beep, and its card flashes.

## Setup (Windows)
Unzip the folder and double-click **start.bat**. It does the rest:
1. If Python isn't installed, it installs it, which takes a few minutes the first time.
2. If your Dhan keys aren't saved yet, it opens Notepad. Type your Client ID after
   `DHAN_CLIENT_ID=` and paste your access token after `DHAN_ACCESS_TOKEN=`
   (web.dhan.co → My Profile → Access DhanHQ APIs). Save and close Notepad.
3. It starts the scanner and opens the dashboard in your browser. Keep the black window open
   while you use it.

The first real scan takes about a minute, because Dhan allows 5 data requests a second and
there are about 200 stocks.

**Notes**
- The dashboard uses Dhan's historical-candle (Data) API, which needs Dhan's **Data API**
  subscription. Without it, the dashboard shows Dhan's error.
- An access token generated on web.dhan.co lasts **24 hours**, but the scanner **renews it by
  itself** (Dhan's RenewToken) when less than 4 hours are left, so you paste it only once. If the
  scanner was off long enough for the token to expire, paste a new one in Settings.
- Your keys stay in `.env` on your computer. They are only sent to api.dhan.co. Don't share that
  file or upload it anywhere.

## Saved settings
Your Dhan Client ID, access token and phone PIN are saved in
`%APPDATA%\OrderBlockScanner\settings.json`, outside the app folder. Deleting the folder or unzipping a new
version doesn't lose them. To change them, click **Settings** on the dashboard (on the PC).

## Opening it on your iPhone
1. On the PC, click **Settings** → tick **Allow my phone to open the scanner** → choose a PIN → **Save phone settings**.
   The first time, Windows Firewall may ask about Python. Click **Allow**.
2. **At home:** with the iPhone on the same Wi-Fi, open Safari and go to the **Home Wi-Fi** address shown
   in Settings, for example `http://192.168.1.5:8000`. Enter the PIN.
3. **From anywhere (mobile data):** install the free **Tailscale** app on the PC and on the iPhone, and
   sign in to both with the same account. A `100.x.x.x` address appears in Settings. Use it in Safari.
   Tailscale is private: only your own devices can reach it.
4. In Safari tap **Share → Add to Home Screen** to get an OrderBlock app icon.
5. The PC has to be **on, awake and running the scanner**. To keep it running:
   - Double-click **autostart.bat** once, and the scanner starts by itself whenever you log in to Windows.
     Run it again to turn this off.
   - In Windows Settings → System → Power, set **Sleep** to **Never** when the PC is plugged in.

The phone can view everything and press Scan now. Keys and settings can only be changed on the PC.
After 5 wrong PINs, that device is locked out for 10 minutes.

## How a stock is scored
1. **Swing highs and lows** are found on 5-minute candles from the last 5 days.
2. **Order blocks:** when price closes above the last swing high, the lowest candle before the
   breakout becomes a **bullish order block**. When price closes below the last swing low, the
   highest candle before the breakdown becomes a **bearish order block**.
3. **Mitigation:** a zone is dropped once price closes through it.
4. **Score (0–100):** each live zone gets a score:
   - 30 points for the size of the move away from it, measured in ATR
   - 25 points for how close price is to the zone now
   - 15 points if price hasn't come back to test it yet
   - 15 points for volume during the move
   - 15 points for the day's % change in the same direction

   Each stock is listed on its stronger side.
5. **Levels:**
   - Support and resistance are the nearest swing points.
   - Liquidity is the next swing point beyond them, where stop-losses usually sit. If there isn't one, it is the previous day's low (for bullish) or high (for bearish).

## Intraday setup 2: Opening Range Breakout on stocks in play
This follows Zarattini, Barbon & Aziz, "A Profitable Day Trading Strategy For The U.S. Equity Market"
(2023–24), the best-documented published intraday setup, adapted to NSE (`orb.py`). The paper reports a
Sharpe ratio of 2.81 on US stocks, 2016–2023.
- **Opening range:** the first 5 minutes (9:15–9:20) or the first 15 minutes (9:15–9:30).
- **In play:** the opening range traded at least as much as usual. "Usual" is the average of the same
  minutes over the last 14 sessions. The most in play come first, up to the risk engine's open-trade limit.
- **Direction:** the colour of the opening range. Green → buy above its high; red → sell below its low;
  a doji → no trade.
- **Stop:** 10% of the 14-day daily ATR (the paper's rule), or the other side of the range.
- **Exit:** the stop or 3:15 PM. There is no target, so winners run all day (`exit_rule: "eod"` in Today's trades).
- **Live:** chosen on the 9:20 scan (5-min) or the 9:30 scan (15-min), and not after 10:00.

The four ORB variants sit in Compare rules next to the breakout variants.

## Compare rules
**Performance → Compare rule variants** runs about 10 small rule changes on the same ~3 months of history and
shows each one's trades, win rate, average R, profit factor and worst drawdown. Examples:
- only in NIFTY's direction
- breakout volume 2× or more
- skip 9:15-candle breakouts
- targets 2R/4R
- BTST buys only

The history is downloaded once a day and kept (`history.pkl` in the settings folder), so re-runs don't
download again.

**Earlier / Later:** the last third of the days is kept aside. A variant earns the ⭐ only if it is best on
the earlier days, still positive on the later days, and has enough trades. That guards against rules that
just happen to fit the past.

**₹ after costs:** every variant is also judged in ₹. Each trade is sized for your capital and risk
(risk engine), then costs are taken off: intraday 0.06% and BTST delivery 0.25% of the position (brokerage,
STT, exchange fees, GST, stamp duty, slippage). The ⭐ is judged in ₹ a day, because R alone flatters tight stops.

**Auto-pick:** you asked Claude to decide, so after each comparison the ⭐ variants are switched on
automatically, for intraday and BTST separately. A variant you pick with **Use** stays until you tap
**Let Claude pick**. If no BTST variant makes money after costs, BTST is marked paper only.

**Use** switches the live scanner (and later backtests) to that variant from the next scan. It is stored in
settings.json as `rules` / `rules_names`. "Current rules" goes back to the defaults.

## Scanner health
**Scanner health** (in the footer, or under More on the phone; also `GET /api/health`) checks:
- the server and the version it runs
- the Dhan token
- the last scan: when, how long it took, and whether one is overdue during market hours
- how many liquid stocks were analysed and which ones failed
- the setup in use and today's picks
- trade tracking
- the NIFTY GEX, crude/USD-INR, news and NSE results sources
- the risk engine
- the last rule comparison

It also lists the last problems the scanner logged. Each line is ✓ ok, ⚠ worth a look, or ✗ needs fixing.

## NIFTY gamma (GEX)
The Market panel shows NIFTY's dealer gamma exposure, read from the nearest-expiry option chain
(`gex.py`, refreshed every 5 minutes).

**Assumption:** dealers are long calls and short puts, so call gamma counts + and put gamma counts −.

**What it shows:**
- **Regime:**
  - Positive gamma: dealers dampen moves. Expect a range day, so be choosy and book profits sooner.
  - Negative gamma: dealers add to moves. Expect a trend day, where breakouts work better.
- **Gamma flip:** the NIFTY level where total dealer gamma changes sign. It is found by recomputing
  total gamma at NIFTY levels ±5% away, using each option's implied volatility.
- **Call wall / put wall:** the strikes above / below NIFTY with the most call / put gamma.
- **Chart:** a bar chart of net GEX by strike.

The regime is information only, not a filter yet. Past option chains aren't available, so it can't be
backtested. Instead, every intraday and BTST trade records the day's regime, and the Learning journal
groups trades by it. After a few weeks it shows whether "trend day" readings really do better.

## Risk engine
Set in Settings → Risk engine. The defaults are for a ₹1,50,000 account:
- **Risk a trade:** 1% of capital (₹1,500). Each pick shows its size: shares = risk ÷ distance to the stop.
  - Intraday position value is capped at 2× capital (intraday margin); BTST at 1×, because it is held overnight.
  - The option is sized in lots the same way. If 1 lot risks more than ₹1,500, the card says to trade the
    stock instead.
- **Daily loss limit:** 3% of capital (−3R). Once today's closed + open trades reach it, no new intraday
  trades are taken for the day.
- **At most 3 intraday trades open at a time, and at most 5 new intraday trades a day.**
- **At most 2 BTST/STBT trades a night:** the best 2 are taken, the rest are shown as backups.

Picks the risk engine blocks are listed ("Not taken"), but they don't go into Today's trades or Performance.
The bar above the Intraday list shows today's result, open trades, trades taken, and OK / FULL / STOPPED.
The backtest applies the same rules.

## Score breakdown
Each pick's score (out of 100) is shown in parts.
- **Intraday:**
  - Volume /25 (breakout candle vs usual for that time)
  - Candle /20 (close near the high, real body)
  - Structure /15 (fresh breakout, retest, also beyond last week's high/low)
  - Trend /25 (VWAP, Supertrend, sector, NIFTY)
  - RVOL+OI /15
- **BTST:**
  - Close /25
  - Volume /25
  - Breakout /20 (20-day high/low)
  - Trend /15
  - OI /15

## Learning journal
Performance → Learning journal groups the finished trades (backtest or live, whichever has more) by how each
trade looked when it was given. For intraday these are:
- breakout volume
- time of breakout
- buy/sell
- with/against NIFTY
- retest
- gap
- score
- sector

For BTST they are:
- side
- 20-day breakout
- the day's move
- volume
- score
- sector

For each group it compares win rate and average R. Clear differences (groups of 8+ trades, 0.3R+ apart) become
plain insights. A group that loses money becomes a suggestion. Suggestions are advice only: rules change when
you ask for it.

## Backtest
**Performance → Run backtest** replays the intraday and BTST rules over the last 60 sessions:
- **Data:** about 3 months of 5-minute candles for every liquid F&O stock, downloaded from Dhan in 30-day
  pieces (around 10 minutes; slower during market hours so the live scanner isn't held up).
- **Intraday:** for each day it rebuilds what each 15-minute scan (9:30–14:00) would have seen, using
  closed candles only. It runs the same `intraday.build()`, locks the picks like Today's trades, and
  follows them on that day's 5-minute candles: trigger, T1/T2, stop, momentum review, 3:15 close.
  - An entry only counts from the moment the pick was given.
  - If price had already jumped past the entry at that moment, the fill is at the next candle's open.
- **BTST:** picked at 3:00 PM with the same rules, bought at the 3:20 price, and followed into the next
  session (target, stop or 11:00).
- **Not replayed:** futures OI, news/events and option prices; today's liquid-stock list is used throughout.
- **Results:** in R, before brokerage and slippage, saved in `backtest.json` in the settings folder.
- **Sanity check:** on random-walk data the intraday rules come out near breakeven (as they should), so
  the replay isn't seeing the future.

## Starting the performance record afresh
`PERF_RESET` in `app.py` names the current record. When it changes, the next start moves every
recorded trade (`trades-*.json`, `btst.json`) to `old-trades/<name>/` in the settings folder. Performance
and Today's trades then start from zero. The files are kept there, not deleted, in case they are ever wanted.
The last reset was on 28 Oct 2026, when intraday switched to PDH/PDL breakouts.

## How intraday picks are chosen
Intraday is one setup only: a **15-minute breakout of yesterday's high (buy) or low (sell) on volume**.
Order blocks have their own tab and are not mixed in. Scans run every 15 minutes from 9:30 to 14:00,
20 seconds after each candle closes, over every liquid F&O stock.
1. **Broke PDH/PDL:** a 15-minute candle closed above yesterday's high (or below its low). If a later candle
   closes back through the level, that breakout has failed. A fresh break after that counts again.
2. **Volume 1.5×+:** the breakout candle traded at least 1.5× the usual volume for that time of day
   (the average of the same 15 minutes on the previous sessions).
3. **Strong candle:** it closed in the top 40% of its range (bottom 40% for a sell) with a real body.
   A gap open beyond the level also counts.
4. **Holding:** no 15-minute close back through the level since, and price is no more than 1R past the
   entry. If it ran further, it waits on the Watchlist for a retest.
5. **Trend agrees:** price is on the right side of VWAP, and the 15-minute Supertrend agrees.
6. **Intraday picks:** the best 6, ranked by:
   - breakout volume (counts the most)
   - candle strength and how fresh the breakout is
   - a retest of the level that held
   - also clearing last week's high/low
   - relative volume for the day
   - sector and NIFTY direction
   - futures OI (build-up adds, the opposite subtracts)
- **Entry:** above the breakout candle's high (below its low for a sell). After a retest, entry is on the
  retest candle.
- **Stop-loss:** just beyond the broken level, which should now act as support (resistance). It is never
  tighter than 0.6 or wider than 2 ATR of the 15-minute candles.
- **Targets:** the next higher-timeframe level (last week's high, swing highs), or 1.5R and 3R.
- **Today's breakouts list:** every pick is locked in once (each stock once, newest first) and followed until 3:15:
  - waiting for trigger, then triggered
  - T1 (half booked, stop to cost), then T2
  - SL, or exited when momentum fades
- **Watchlist:** stocks within 0.5% of yesterday's high/low on good volume, and breakouts that are extended
  or not yet backed by VWAP/Supertrend.

## How BTST / STBT is chosen
Worked out in every scan from 11:00; the 3:00 PM list is locked in. A BTST buy needs **all** of these:
- up 1.5–6% today (bigger moves often give back the next morning)
- closing in the top 20% of the day's range, and still at the last hour's high
- above yesterday's high, and above VWAP
- volume at least 1.5× a normal day (projected for the full day)
- NIFTY not bearish

STBT is the mirror image. The rest is ranked:
- closing strength and volume
- a 20-day high/low breakout (big plus)
- above (below) the 20-day average close
- sector agrees
- long build-up in futures OI
Long unwinding (money leaving) removes the stock, and so do results due before the next session.
- **Stop-loss:** beyond the last hour's low, 0.5–2.5% away.
- **Targets:** the next level, or 1.2R and 2.2R.
- **Exit:** next morning at T1/T2, the stop, or by 11:00.

## How an HTF key level breakout is found
1. **Key levels** come from daily candles:
   - PDH/PDL: previous day high and low
   - PWH/PWL: previous week high and low
   - Daily swing highs and lows from the last 60 days

   Levels within 0.2% of each other are merged and count as a stronger level.
2. **Breakout:** in today's session, a **closed** 15-minute candle closes above a level when the candle
   before it closed below. A breakdown is the same in reverse. To count, the candle must also:
   - close in its top 40% (bottom 40% for a breakdown)
   - trade at least 1.2× the average volume of the last 20 candles
3. **Status:**
   - **New:** it happened on the candle that just closed.
   - **Holding:** price is still on the right side of the level.
   - **Retest:** price came back to the level and held.

   If a later candle closes back through the level, the breakout counts as failed and is removed.
4. **Score (0–100):**
   - 30 points for volume
   - 20 points for the size of the candle's body
   - 20 points for the level's importance (weekly > daily > swing, plus a bonus for merged levels)
   - 15 points for how recent it is
   - 15 points for how far price has run from the level (less is better)
   - +5 points for a retest
5. **Stop** is the far end of the breakout candle. **Target** is the next key level.

The 15-minute candles are built from the same 5-minute data, so the only extra Dhan calls are the daily
candles. Those are fetched once a day per stock, which makes the first scan of the day take about twice as long.

This is a tool for finding charts to look at. It is not trading advice.
