# OrderBlock Scanner

**Running it in the cloud (recommended):** see "OrderBlock Scanner" in the repository's main
README. One command on the droplet, then it runs 24/7 and you open it from your iPhone anywhere.
The steps below are for running it on a Windows PC instead.

A live scanner that checks every NSE F&O stock for order blocks and liquidity using
your Dhan account, and ranks the strongest bullish and bearish setups.

- **Top Picks (opens first):** after every scan, all F&O stocks go through a series of filters and the
  best 5–6 trades are shown with **entry, stop-loss, target 1, target 2, R:R and risk %**. A flow chart
  shows how many stocks passed each filter, next to a sector strength table.
- **Ranked lists:** the top 20 bullish and top 20 bearish stocks, strongest first.
- **Strength map:** how many stocks are choppy and how many are strong, on each side.
- **Chart:** recent candles with the order block zone, support, resistance and the liquidity level.
- **HTF Key Level Breakouts (15 min):** a second tab lists stocks whose 15-minute candle has closed
  through a higher-timeframe level. The levels are the previous day high/low, the previous week high/low,
  and daily swing highs/lows from the last 60 days.
- **Auto-scan:** runs 20 seconds after every 5-minute candle closes, 9:15–15:30 IST, Mon–Fri, so new
  15-minute breakouts show up right after their candle closes. The header shows the time of the next scan.
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
- An access token generated on web.dhan.co lasts **24 hours**. When it expires, the dashboard says
  so. Paste a new token into `.env` and restart.
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

## How Top Picks are chosen
1. **Scanned:** every F&O stock.
2. **Strong setup:** a bullish or bearish order block, or an HTF key level breakout, with a score of at
   least 55. If a stock has both in the same direction, it gets a bonus.
3. **Sector agrees:**
   - Bullish setups must be in a sector that is up and in the top half of the sectors today.
   - Bearish setups must be in a sector that is down and in the bottom half.
   - Sectors come from a built-in list. Stocks not on it follow the overall market.
4. **Good R:R:** the trade plan must work.
   - **Entry:** the top of the order block (bottom for shorts), or the broken key level.
   - **Stop-loss:** just beyond the zone, or beyond the breakout candle.
   - **Targets:** the next key levels (swing high, previous day/week high, and so on), or 2R and 3R if
     there aren't any.
   - Target 1 must be at least 1.5R away. Price must not already be more than 1R past the entry.
5. **OI confirms:** futures open interest today, checked for the 30 best candidates.
   - Bullish trades need **long build-up** (price up, OI up).
   - Bearish trades need **short build-up** (price down, OI up).
   - Short covering or long unwinding only fills in if there aren't enough picks.
   - OI against the trade removes the stock.
6. **Top picks:** the best 6 by a score that combines setup strength, sector rank, OI and R:R.

Each pick has a status:
- **At entry:** price is at the entry now.
- **Wait for pullback:** price is above the entry (below it for shorts). Wait for it to come back.

"Since" shows when the stock first became a pick today.

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
