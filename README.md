# Handral Dentistry Website

## Files
- `index.html` — Public patient website (handraldentistry.com)
- `clinic/` — Staff clinic management app (handraldentistry.com/clinic)
- `photos/` — Clinic Photos: organise clinic and patient photos on the iPhone (handraldentistry.com/photos).
  Open it in Safari, Share → Add to Home Screen. Photos are picked from the Photo Library and kept only on
  that phone (nothing is uploaded); use ⋯ → Save backup to keep a copy in Files / iCloud Drive.
  **From Gmail** imports photos and PDFs from matching emails (and Google Drive links in them) straight
  to the phone after a Google sign-in; it needs a Google OAuth Client ID (web app, origin
  https://handraldentistry.com, Gmail API and Drive API enabled), pasted into the app once.

## Deploy to GitHub Pages

1. Create repo on GitHub named `handraldentistry-website`
2. Upload all files
3. Settings → Pages → Source: main branch / root
4. Add custom domain: handraldentistry.com

## DNS Setup (at your domain registrar)
Add these DNS records:
```
Type: A      Name: @    Value: 185.199.108.153
Type: A      Name: @    Value: 185.199.109.153
Type: A      Name: @    Value: 185.199.110.153
Type: A      Name: @    Value: 185.199.111.153
Type: CNAME  Name: www  Value: YOUR-USERNAME.github.io
```

## Before Going Live - Update These
1. Replace all `+91 94480 XXXXX` with real phone numbers
2. Replace `https://wa.me/919448000000` with real WhatsApp number
3. Add real clinic addresses if needed
4. Add real photos to replace placeholders

## Handral Books (bookkeeping app on the droplet)

The app lives in `_books/books.py` (folders starting with `_` are not published by GitHub Pages).

### One-time setup on the droplet
SSH in once and run:
```
curl -fsSL https://raw.githubusercontent.com/destiny4424-netizen/handraldentistry-website/main/_books/deploy/install.sh | sudo bash
```
It finds your existing `books.db`, copies it into `/var/lib/handral-books/` (the original
stays where it was), stops any copy you started by hand, and installs it as a service.
To point it at a specific file: `... | sudo BOOKS_OLD_DB=/root/books.db bash`.

### After that, nothing to run
- Starts on boot and restarts itself within seconds if it ever stops.
- Every 5 minutes it checks GitHub. Edit or upload `_books/books.py` on GitHub and the
  droplet picks it up by itself. It backs up the database first, and if the new version
  fails to start it goes back to the previous one automatically.
- Backs up the database every night to `/var/lib/handral-books/backups` (45 days kept).
- Served at a secure `https://` address with a username and password (via Caddy, which
  renews the certificate by itself). The setup prints the address and password at the
  end; to see them again: `sudo cat /etc/handral-books/login.txt`.

### Opening it on the Windows laptop
Open the printed address in Chrome or Edge, log in, and let the browser save the
password. To make it feel like an app: in Edge, menu, Apps, "Install this site as an
app" (Chrome: menu, Cast save and share, "Install page as app"). It then has its own
Start menu and taskbar icon.

### What's in the app
Banking (with **Cash collections**: cash received, by date and amount with an optional
note, counted as patient receipts), Sort, Entries, **Trading** (yearly P&L per broker, entered from each broker's
Tax P&L report, with money moved to and from your banks filled in), **Assets** (mutual
funds, shares, gold, plot, house, FD and so on, with gains and short or long term on
sale; **Upload statement** reads CAMS/KFintech CAS PDFs, capital gains statements
and Groww/Coin/Kuvera transaction lists, matches sales oldest units first and shows short
and long term gains and holdings), **Loans** (each car, jewel, personal, home, education or business loan with
its bank text, EMIs, interest from the certificate or estimated from rate and EMI,
outstanding and the tax treatment), **Insurance** (term, LIC, health, vehicle and clinic policies with their bank
text; premiums filed under the right head, renewals due in 30 days, 80C used and 80D
claimable within the limits), **Salaries** (add each staff member once; their bank payments are filed under
Staff salaries automatically, with a month-by-month table), Consultants, Rules and ITR. School fees are a category under tax deductions
(80C); bank payments mentioning SCHOOL or VIDYALAYA are sorted there automatically.
The ITR summary and Excel download include the trading and investment figures.
In the ITR tab, **Upload ITR** takes previous returns (the JSON from the income-tax
portal, or the ITR / ITR-V PDF). It shows a year-by-year comparison with this year's
books and checks for anything missed: deductions claimed before, income heads, bank
accounts not added, the 44ADA scheme and advance tax.

### Statements from Gmail
Banking → **Statements from Gmail** reads HDFC statement emails every 6 hours over IMAP
with a Gmail App Password (myaccount.google.com/apppasswords) and imports them:
combined monthly statements (PDF attached; split by account), single-account
SmartStatements (the link is opened with the saved password; HDFC keeps these for about
3 months), and credit card statements (PDF attached). Statement passwords are typed into
the app, one per line, and stay on the droplet. Only the accounts listed are imported;
the daughter's account numbers are added under Daughter. Statements that fail are listed
with the reason and the PDF, and are tried again on every check.

### Incentives and nicknames
The **Incentives** tab keeps incentives apart from fixed salary (Tally ledger Staff Incentives). Enter each
person's fixed monthly salary (or the app uses the amount paid most months); a bank payment above it is
listed so the extra can be filed as incentive, splitting that entry in two. Cash incentives get a voucher,
alone or with the month's cash salary, and any month's cash amount can be changed. **Merge names** (Sort,
Salaries, Consultants) puts different spellings of one person under one nickname, now and on every import;
Rules lists the nicknames with Undo.

### Tax to pay
The ITR tab works out the year's income tax from the books for both regimes, with clinic income
either at 50% of receipts (section 44ADA) or from the books (receipts less expenses and
depreciation): salary after standard deduction, F&O as business income with losses set off
(never against salary) and carried forward, intraday as speculative, capital gains at their own
rates, interest and dividends, 80C/80D/80G/80TTA in the old regime, 87A rebate, surcharge and 4%
cess, less tax already paid from the bank. It shows the cheapest legal option, the full working,
losses to carry forward and audit or ITR-form notes. It is an estimate for the CA to confirm.

### File on the income-tax portal
The ITR tab names the form (ITR-4 or ITR-3, and why), the regime (with the Form 10-IEA note for the
old regime) and the due date, gives the steps on incometax.gov.in, and lists every figure to enter
schedule by schedule (S, BP / 44ADA with bank and cash receipts, financial particulars, OS, CG,
VI-A, CFL, TDS, IT challans, tax computation, bank accounts). **Upload Form 26AS / AIS** reads the
TRACES text (zip) or PDF, or the AIS JSON or PDF, and counts the TDS and TCS in the tax; lines can
also be added by hand. **Download filing sheet (Excel)** has the sheet, the tax working, the TDS
list and the steps. The portal's own JSON is not generated: the return is filled online (mostly
prefilled) or by the CA.

### Clinic equipment and depreciation
Payments to dental suppliers of Rs 50,000 or more (changeable in Assets) are filed as fixed assets,
not expenses. In Assets, Clinic equipment and depreciation lists each one; pick what it is from a list
of modern dental equipment and its income-tax block follows (plant and machinery 15%, computers 40%,
furniture and interiors 10%, life-saving equipment 40%). Depreciation is worked out every year on
the WDV method, half rate for equipment used under 180 days in its first year, and counted as a
clinic expense in the ITR working and the Tally P&L.

### For the CA (Tally terms)
Heads are Tally ledgers under Tally groups (Direct/Indirect Incomes and Expenses, Purchase
Accounts, Capital Account with Drawings, Secured/Unsecured Loans, Current Liabilities,
Investments, Loans & Advances, Bank Accounts, Cash-in-Hand, Suspense A/c, and the rest of Tally's
predefined groups: Fixed Assets, Deposits, Duties & Taxes, Bank OD A/c, Sundry Creditors and Sundry
Debtors, where each supplier or debtor gets its own party ledger). Each entry is a
Receipt, Payment or Contra voucher; clinics are cost centres. The ITR tab shows the Profit &
Loss A/c and Trial Balance, the Excel download has them plus the Day Book, and **Download for
Tally (XML)** gives masters and vouchers to import into TallyPrime.

### Web address
By default it uses `books.handraldentistry.com` if that name points at the droplet,
otherwise a free `<droplet-ip>.sslip.io` address that needs no DNS changes. To use the
nicer name, add a DNS **A** record `books` pointing at the droplet IP at your domain
registrar, then run the setup command again. Re-running keeps the same password;
add `BOOKS_NEW_PASSWORD=1` after `sudo` for a new one.

If something ever looks wrong: `systemctl status handral-books` or
`journalctl -u handral-books -u handral-books-update -n 50`.

## OrderBlock Scanner (F&O scanner on the droplet)

`_scanner/` is a scanner for NSE F&O stocks using Dhan market data (it never places orders):
order blocks, HTF key level breakouts on 15-minute candles, and **Top Picks**, the best 5–6
trades after sector, risk:reward and futures-OI filters, each with entry, stop-loss and targets.
Like `_books/`, the folder is not published by GitHub Pages. Details: `_scanner/README.md`.

### One-time setup on the droplet
Open the droplet's console (DigitalOcean → the droplet → **Access → Launch Droplet Console**)
and run:
```
curl -fsSL https://raw.githubusercontent.com/destiny4424-netizen/handraldentistry-website/main/_scanner/deploy/install.sh | sudo bash
```
At the end it prints the scanner's address (`https://scanner.<droplet-ip>.sslip.io`, or
`scanner.handraldentistry.com` if a DNS **A** record `scanner` points at the droplet) and its
password. To see them again: `sudo cat /etc/orderblock-scanner/login.txt`.
It sits next to Handral Books in the same Caddy web server and doesn't touch Books.

### After that
- **Schedule:** 15-minute scans from 9:30 to 12:00 give the intraday Top Picks. After that, hourly scans at
  12:15, 13:15, 14:15 and 15:00 build the **BTST / STBT** list: strong closers to buy (or, in F&O, sell)
  between 3:15 and 3:25 PM and exit the next morning. The 3 PM list is locked in and followed into the
  next session (target, stop, or 11:00 exit).
- The app looks like the TradingView app:
  - a dark theme, with a candle chart drawn by TradingView's own Lightweight Charts library
    (no volume bars), with entry, stop and target lines, a crosshair and an OHLC readout
  - an **Open in TradingView** button that opens the same stock in the TradingView app on Android and iPhone
    or on tradingview.com on a computer. The iPhone doesn't let web pages open apps, so the first tap shows how to
    make a one-step Apple Shortcut named **TradingView Chart** (Open App → TradingView). After that the button
    copies the symbol (e.g. NSE:SBIN) and opens the app; paste it in the app's search. Change it under Settings.
  - a watchlist on the right
  - a bottom bar on the iPhone: Picks, BTST, Trades, Market, Stats
- Runs 24/7 on the droplet, whether your PC is on or off. It scans during
  market hours (9:15–15:30 IST, Mon–Fri), starts on boot and restarts itself if it ever stops.
- A trade is only listed after price has come back to its level and a 15-minute candle has closed
  holding it. It then says "Buy above …" (the high of that candle) with the stop beyond the level.
  Setups still waiting for that go on the Watchlist. No picks appear before 10:00 IST (changeable
  in Settings), because confirmations after the first hour are cleaner.
- **Early picks (up to 10:30):** a long must also clear yesterday's high, and a short must break
  yesterday's low. If the level is still inside yesterday's range, the entry is moved to yesterday's high/low.
- **Supertrend (10, 3) on 15 minutes** is part of the score; a pick shows ✓ when it agrees.
- **Momentum review:** at every 15-minute close after entry, a trade is checked against VWAP and Supertrend.
  - If both turn against it, the trade is exited.
  - If one turns, Target 2 is cut to Target 1 and the stop goes to cost once the trade is 0.5R up. If Target 1 is already booked, the rest is booked.
  - The Trades list marks these "↻ revised".
- **Performance tab (Stats on the iPhone):** every intraday pick and BTST trade for the month is shown, including:
  - win rate and total R
  - profit factor
  - an equity curve
  - a day-by-day table
  Pick the month at the top, and enter ₹ per trade to see rupees.
- Open the address on the iPhone in Safari, sign in with the password (it stays signed in for
  90 days), then **Share → Add to Home Screen** for an app icon.
- **Dhan token:** tap **Settings** (or **Connect Dhan**) and paste the Client ID and access token
  **once**. Dhan tokens last 24 hours, but the scanner renews it by itself (Dhan's RenewToken) when
  less than 4 hours are left, so it keeps going without daily pasting. Settings shows "Valid until …
  · renews automatically". Only if the droplet was off long enough for the token to expire does it
  need pasting again; the button then turns green and says **Update token**.
- Every 5 minutes the droplet checks GitHub. Changes to `_scanner/` are installed by themselves,
  and if a new version fails to start, the previous one comes back automatically.
- New password: `curl -fsSL .../_scanner/deploy/install.sh | sudo SCANNER_NEW_PASSWORD=1 bash`
  (signs every device out).

If something ever looks wrong: `systemctl status orderblock-scanner` or
`journalctl -u orderblock-scanner -u orderblock-scanner-update -n 50`.
