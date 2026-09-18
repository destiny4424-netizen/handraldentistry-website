# Wiring a Pine Script strategy to the webhook

Your Pine Script strategy needs to send a JSON alert whose fields match what
`src/server.js` expects: `secret`, `symbol`, `side`, `size`, and optionally
`order_type` / `limit_price`.

## 1. Add `alert_message` to your entries/exits

```pine
//@version=5
strategy("My Strategy", overlay=true)

longCondition = ta.crossover(ta.sma(close, 14), ta.sma(close, 28))
shortCondition = ta.crossunder(ta.sma(close, 14), ta.sma(close, 28))

if (longCondition)
    strategy.entry("Long", strategy.long,
         alert_message='{"secret":"YOUR_WEBHOOK_SECRET","symbol":"{{ticker}}","side":"buy","size":{{strategy.order.contracts}},"order_type":"market_order"}')

if (shortCondition)
    strategy.entry("Short", strategy.short,
         alert_message='{"secret":"YOUR_WEBHOOK_SECRET","symbol":"{{ticker}}","side":"sell","size":{{strategy.order.contracts}},"order_type":"market_order"}')
```

Replace `YOUR_WEBHOOK_SECRET` with the same value you put in `.env` as
`WEBHOOK_SECRET`.

## 2. Create the alert in TradingView

1. Right-click the chart -> **Add alert**.
2. Condition: your strategy, **Order fills only**.
3. Under **Notifications**, enable **Webhook URL** and set it to:
   `https://your-server-domain.com/webhook`
4. In the **Message** box, enter exactly:
   ```
   {{strategy.order.alert_message}}
   ```
   TradingView substitutes this with the JSON string you wrote in step 1.

## 3. Symbol matching

`{{ticker}}` sends TradingView's ticker for the chart (e.g. `BTCUSD`). Delta
Exchange's product `symbol` field must match after applying `SYMBOL_MAP` from
`.env`. Fetch `GET /v2/products?states=live` (no auth needed) to see the
exact symbols Delta lists, and add an entry to `SYMBOL_MAP` if TradingView's
ticker doesn't match verbatim, e.g.:

```
SYMBOL_MAP={"XBTUSD":"BTCUSD"}
```

## 4. Test with DRY_RUN=true first

With `DRY_RUN=true` (the default), the server logs what it *would* send to
Delta Exchange instead of placing a real order. Trigger a test alert, confirm
the log line looks right, then set `DRY_RUN=false` to go live.
