require('dotenv').config({ path: require('path').join(__dirname, '..', '.env') });

function required(name) {
  const value = process.env[name];
  if (!value) throw new Error(`Missing required env var: ${name}`);
  return value;
}

let symbolMap = {};
try {
  symbolMap = JSON.parse(process.env.SYMBOL_MAP || '{}');
} catch (err) {
  throw new Error('SYMBOL_MAP must be valid JSON, e.g. {"BTCUSD":"BTCUSD"}');
}

module.exports = {
  port: Number(process.env.PORT) || 3000,
  webhookSecret: required('WEBHOOK_SECRET'),
  dryRun: process.env.DRY_RUN !== 'false',
  maxOrderSize: process.env.MAX_ORDER_SIZE ? Number(process.env.MAX_ORDER_SIZE) : null,
  symbolMap,
  delta: {
    apiKey: required('DELTA_API_KEY'),
    apiSecret: required('DELTA_API_SECRET'),
    baseUrl: process.env.DELTA_BASE_URL || 'https://api.india.delta.exchange',
  },
};
