# Handral Dentistry Website

## Files
- `index.html` — Public patient website (handraldentistry.com)
- `clinic/` — Staff clinic management app (handraldentistry.com/clinic)

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

### Web address
By default it uses `books.handraldentistry.com` if that name points at the droplet,
otherwise a free `<droplet-ip>.sslip.io` address that needs no DNS changes. To use the
nicer name, add a DNS **A** record `books` pointing at the droplet IP at your domain
registrar, then run the setup command again. Re-running keeps the same password;
add `BOOKS_NEW_PASSWORD=1` after `sudo` for a new one.

If something ever looks wrong: `systemctl status handral-books` or
`journalctl -u handral-books -u handral-books-update -n 50`.
