const {onRequest} = require('firebase-functions/v2/https');
const {defineSecret} = require('firebase-functions/params');
const admin = require('firebase-admin');

admin.initializeApp();

const ANTHROPIC_API_KEY = defineSecret('ANTHROPIC_API_KEY');
const MAX_TOKENS_CEILING = 4000;

// Proxies AI-scan/agent requests to Anthropic. The clinic app never holds the
// Anthropic key itself; it sends its Firebase sign-in token here instead, and
// this function checks that token before spending the key on a real request.
exports.aiProxy = onRequest({secrets: [ANTHROPIC_API_KEY], cors: true, region: 'us-central1'}, async (req, res) => {
  if (req.method === 'OPTIONS') { res.status(204).send(''); return; }
  if (req.method !== 'POST') { res.status(405).json({error: {message: 'Method not allowed'}}); return; }

  const authHeader = req.get('Authorization') || '';
  const match = authHeader.match(/^Bearer (.+)$/);
  if (!match) { res.status(401).json({error: {message: 'Missing Authorization bearer token'}}); return; }

  try {
    await admin.auth().verifyIdToken(match[1]);
  } catch (e) {
    res.status(401).json({error: {message: 'Your sign-in has expired. Please sign in again.'}});
    return;
  }

  const body = req.body || {};
  if (!body.model || !Array.isArray(body.messages)) {
    res.status(400).json({error: {message: 'Bad request'}});
    return;
  }

  const upstreamBody = {
    model: body.model,
    max_tokens: Math.min(Number(body.max_tokens) || 1000, MAX_TOKENS_CEILING),
    messages: body.messages,
  };
  if (body.system) upstreamBody.system = body.system;

  try {
    const upstream = await fetch('https://api.anthropic.com/v1/messages', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-api-key': ANTHROPIC_API_KEY.value(),
        'anthropic-version': '2023-06-01',
      },
      body: JSON.stringify(upstreamBody),
    });
    const data = await upstream.json();
    res.status(upstream.status).json(data);
  } catch (e) {
    res.status(502).json({error: {message: 'Upstream request failed: ' + e.message}});
  }
});
