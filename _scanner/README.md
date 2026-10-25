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
- **Auto-scan:** intraday at 9:30, 9:45, 10:00 and 10:15; BTST every 30 minutes from 11:00 to 3:00 (IST, Mon–Fri),
  20 seconds after the candle closes. Between scans the day's trades are followed every 5 minutes. The header shows the time of the next scan.
  Use **Scan now** to rescan straight away.
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

## How intraday picks are chosen
1. **Scanned:** every liquid F&O stock (see Settings for the turnover and volume limits).
2. **Strong setup:** a bullish or bearish order block, or an HTF key level breakout, with a score of at
   least 55. If a stock has both in the same direction, it gets a bonus.
3. **Sector agrees:**
   - Bullish setups must be in a sector that is up and in the top half of the sectors today.
   - Bearish setups must be in a sector that is down and in the bottom half.
   - Sectors come from a built-in list. Stocks not on it follow the overall market.
4. **Good R:R:** the trade plan must work.
   - **Level:** the top of the order block (bottom for shorts), or the broken key level.
   - **Stop-loss:** just beyond the zone, or beyond the breakout candle.
   - **Targets:** the next key levels (swing high, previous day/week high, and so on), or 2R and 3R if
     there aren't any.
   - Target 1 must be at least 1.5R away. Price must not already be more than 1R past the entry.
5. **Held level (price action):** the trade is only given after price has **come back to the level
   and held it**:
   - Price touches the zone again, after the order block or breakout formed.
   - A **closed 15-minute candle** then finishes back on the right side of the level. For a buy, it
     closes above the zone top with a green body or a long lower wick, without closing below the zone.
   - **Entry** is the break of that candle: **buy above its high** (sell below its low).
   - **Stop-loss** goes just beyond that candle and the zone. Targets are the next key levels.
   - If a candle closes through the far side of the zone, the setup is dropped.
   - Setups that haven't held yet go on the **Watchlist**, with what to wait for. They are never picks.
6. **OI confirms:** futures open interest today, checked for the 30 best candidates.
   - Bullish trades need **long build-up** (price up, OI up).
   - Bearish trades need **short build-up** (price down, OI up).
   - Short covering or long unwinding only fills in if there aren't enough picks.
   - OI against the trade removes the stock.
7. **Intraday picks:** the best 6 by a score that combines setup strength, sector rank, OI and R:R.
   Picks come from the 9:30, 9:45, 10:00 and 10:15 scans (Settings can start them later). Up to 10:30 a
   long must clear yesterday's high and a short yesterday's low. After 10:15 there are no new picks:
   the day's list stays, each stock once, and is followed until 3:15.
8. **Option:** a call for a buy, a put for a sell, at the money (or one strike in the money when that trades
   thin), on this month's expiry, or next month's when this month's is 7 days away or less. Option entry, SL
   and targets are the option's price at the stock's entry, SL and targets, from its delta and gamma in Dhan's
   option chain. The lot size and ₹ risk per lot are shown.

Each pick has a status:
- **Buy above / Sell below …:** the level has held. Enter when price breaks that 15-minute candle.
- **Triggered:** price has broken the candle, so the trade is on. The card shows the time and the stop.

"Picked" shows when the stock first became a pick today.

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
