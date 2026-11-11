const express = require('express');
const config = require('./config');
const delta = require('./deltaClient');

const app = express();
app.use(express.json());

const VALID_SIDES = new Set(['buy', 'sell']);

app.post('/webhook', async (req, res) => {
  const alert = req.body || {};

  if (alert.secret !== config.webhookSecret) {
    console.warn(`Rejected webhook: bad or missing secret from ${req.ip}`);
    return res.status(401).json({ error: 'unauthorized' });
  }

  const { symbol, side, order_type: orderType = 'market_order', limit_price: limitPrice, reduce_only: reduceOnly } = alert;
  const size = Number(alert.size);

  if (!symbol || !VALID_SIDES.has(side) || !Number.isFinite(size) || size <= 0) {
    return res.status(400).json({ error: 'symbol, side ("buy"/"sell") and a positive numeric size are required' });
  }

  if (config.maxOrderSize && size > config.maxOrderSize) {
    console.warn(`Rejected order: size ${size} exceeds MAX_ORDER_SIZE ${config.maxOrderSize}`);
    return res.status(400).json({ error: 'size exceeds configured maximum' });
  }

  try {
    const product = await delta.findProductBySymbol(symbol);

    if (config.dryRun) {
      console.log('[DRY RUN] would place order:', {
        symbol: product.symbol,
        productId: product.id,
        side,
        size,
        orderType,
        limitPrice,
      });
      return res.json({ dryRun: true, symbol: product.symbol, side, size, orderType });
    }

    const order = await delta.placeOrder({
      productId: product.id,
      side,
      size,
      orderType,
      limitPrice,
      reduceOnly,
      clientOrderId: `tv-${Date.now()}`,
    });

    console.log('Order placed:', JSON.stringify(order.result || order));
    res.json(order);
  } catch (err) {
    console.error('Order failed:', err.message);
    res.status(502).json({ error: err.message });
  }
});

app.get('/health', (req, res) => {
  res.json({ ok: true, dryRun: config.dryRun });
});

app.listen(config.port, () => {
  console.log(`Webhook server listening on port ${config.port} (dryRun=${config.dryRun})`);
});
