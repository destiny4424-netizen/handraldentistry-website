const crypto = require('crypto');
const config = require('./config');

const PRODUCT_CACHE_TTL_MS = 5 * 60 * 1000;
let productCache = null;
let productCacheAt = 0;

function signRequest(method, path, queryString, payload) {
  const timestamp = Math.floor(Date.now() / 1000).toString();
  const message = method + timestamp + path + queryString + payload;
  const signature = crypto.createHmac('sha256', config.delta.apiSecret).update(message).digest('hex');
  return { timestamp, signature };
}

async function signedRequest(method, path, { query = '', body } = {}) {
  const payload = body ? JSON.stringify(body) : '';
  const { timestamp, signature } = signRequest(method, path, query, payload);

  const response = await fetch(`${config.delta.baseUrl}${path}${query}`, {
    method,
    headers: {
      'api-key': config.delta.apiKey,
      signature,
      timestamp,
      'Content-Type': 'application/json',
      'User-Agent': 'pine-delta-auto-trading',
    },
    body: payload || undefined,
  });

  const data = await response.json().catch(() => ({}));
  if (!response.ok || data.success === false) {
    const err = new Error(`Delta API error ${response.status}: ${JSON.stringify(data)}`);
    err.status = response.status;
    err.data = data;
    throw err;
  }
  return data;
}

// Public endpoint, no signing required.
async function getProducts() {
  const response = await fetch(`${config.delta.baseUrl}/v2/products?states=live`);
  const data = await response.json();
  if (!data.success) throw new Error(`Failed to fetch products: ${JSON.stringify(data)}`);
  return data.result;
}

async function findProductBySymbol(rawSymbol) {
  const symbol = (config.symbolMap[rawSymbol] || rawSymbol).toUpperCase();
  const now = Date.now();
  if (!productCache || now - productCacheAt > PRODUCT_CACHE_TTL_MS) {
    const products = await getProducts();
    productCache = new Map(products.map((p) => [p.symbol.toUpperCase(), p]));
    productCacheAt = now;
  }
  const product = productCache.get(symbol);
  if (!product) throw new Error(`Unknown Delta Exchange symbol: ${symbol}`);
  return product;
}

async function placeOrder({ productId, side, size, orderType = 'market_order', limitPrice, reduceOnly, clientOrderId }) {
  const body = {
    product_id: productId,
    side,
    size,
    order_type: orderType,
  };
  if (orderType === 'limit_order') {
    if (!limitPrice) throw new Error('limitPrice is required for limit_order');
    body.limit_price = String(limitPrice);
  }
  if (reduceOnly) body.reduce_only = true;
  if (clientOrderId) body.client_order_id = clientOrderId;

  return signedRequest('POST', '/v2/orders', { body });
}

async function getPositions(productId) {
  const query = productId ? `?product_id=${productId}` : '';
  return signedRequest('GET', '/v2/positions', { query });
}

module.exports = { getProducts, findProductBySymbol, placeOrder, getPositions };
