# Pine Script -> Delta Exchange auto trading bridge

A small webhook server that receives TradingView alerts from your Pine
Script strategy and places matching orders on Delta Exchange via its
signed REST API.

**This places real orders with real money once `DRY_RUN=false`. Test
thoroughly with `DRY_RUN=true` first, use small size, and read the safety
notes below before going live.**

## How it fits together

```
Pine Script strategy (TradingView)
   -> alert fires with a JSON message
   -> TradingView sends it as an HTTP POST to this server's /webhook
   -> server verifies the shared secret, looks up the Delta product
   -> server calls Delta Exchange's POST /v2/orders (signed with your API key)
```

See `pine/alert-setup.md` for the Pine Script side.

## Setup

1. On Delta Exchange, go to Account -> API Keys and create a key with
   **trading permission only** (no withdrawal permission). If Delta offers
   IP whitelisting, restrict the key to your server's IP.
2. `cd trading-bot && npm install`
3. `cp .env.example .env` and fill in `WEBHOOK_SECRET`, `DELTA_API_KEY`,
   `DELTA_API_SECRET`, and `DELTA_BASE_URL` (India vs Global).
4. `npm start` — the server listens on `PORT` (default 3000) with
   `DRY_RUN=true`, so it logs intended orders instead of placing them.
5. Expose the server over HTTPS so TradingView can reach `/webhook`
   (a VPS behind a reverse proxy with TLS, or a host like Render/Railway/
   Fly.io; `ngrok http 3000` works for local testing only).
6. Wire up the TradingView alert per `pine/alert-setup.md`, trigger a test
   alert, and confirm the dry-run log line matches what you expect.
7. Set `DRY_RUN=false` in `.env`, restart, and you're live.

## Safety notes

- **Never commit `.env`** — it holds your exchange API secret. It's already
  in `.gitignore`.
- `WEBHOOK_SECRET` is the only thing stopping a stranger who finds your
  webhook URL from placing orders on your account — make it long and random.
- Set `MAX_ORDER_SIZE` in `.env` as a hard cap so a bad alert or a Pine
  Script bug can't send an oversized order.
- Watch the server logs (`Order placed:` / `Order failed:` / `Rejected
  order:`) — nothing here e-mails or pages you on failure, so monitor it
  yourself or wire the logs into whatever alerting you already use.
- Delta signatures are only valid for a few seconds, so keep the server's
  clock in sync (NTP) or every real request will be rejected as unauthorized.

## Files

- `src/config.js` — loads and validates environment variables.
- `src/deltaClient.js` — Delta Exchange request signing, product lookup,
  and order placement.
- `src/server.js` — Express app exposing `POST /webhook` and `GET /health`.
- `pine/alert-setup.md` — how to shape your Pine Script alert JSON and wire
  it to TradingView's webhook alert feature.
