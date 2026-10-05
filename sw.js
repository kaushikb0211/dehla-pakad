// Offline support: cache the app and the Pyodide engine after first use.
const CACHE = "dehla-7e7706129d2a";
const SHELL = [
  "./",
  "icon-192.png",
  "icon-512.png",
  "icon.svg",
  "index.html",
  "manifest.webmanifest",
  "static/app.js",
  "static/py/dehla/__init__.py",
  "static/py/dehla/bots.py",
  "static/py/dehla/cards.py",
  "static/py/dehla/engine.py",
  "static/py/dehla/match.py",
  "static/py/dehla/mc.py",
  "static/py/dehla/session.py",
  "static/style.css",
  "static/worker.js"
];
self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener("fetch", e => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  const ok = url.origin === location.origin || url.hostname === "cdn.jsdelivr.net" ||
             url.hostname === "fonts.googleapis.com" || url.hostname === "fonts.gstatic.com";
  if (!ok) return;
  e.respondWith(caches.match(req, { ignoreSearch: true }).then(hit => hit || fetch(req).then(res => {
    if (res.ok || res.type === "opaque") {
      const copy = res.clone();
      caches.open(CACHE).then(c => c.put(req, copy));
    }
    return res;
  })));
});
