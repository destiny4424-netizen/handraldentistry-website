/* Handral Trading Desk service worker: shows the phone notifications the server pushes,
   and opens the right screen when one is tapped. Nothing is cached: prices must always be live. */
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));

self.addEventListener('push', e => {
  let m = {};
  try { m = e.data ? e.data.json() : {}; } catch (err) { m = {title: 'Handral Trading Desk', body: e.data ? e.data.text() : ''}; }
  e.waitUntil(self.registration.showNotification(m.title || 'Handral Trading Desk', {
    body: m.body || '', tag: m.tag || undefined, icon: '/icon.svg', badge: '/icon.svg',
    data: {url: m.url || '/'}, timestamp: (m.ts || Date.now() / 1000) * 1000,
  }));
});

self.addEventListener('notificationclick', e => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || '/';
  e.waitUntil((async () => {
    const wins = await self.clients.matchAll({type: 'window', includeUncontrolled: true});
    for (const w of wins) {
      if ('focus' in w) { await w.focus(); if ('navigate' in w) { try { await w.navigate(url); } catch (err) { /* same page */ } } return; }
    }
    if (self.clients.openWindow) await self.clients.openWindow(url);
  })());
});
