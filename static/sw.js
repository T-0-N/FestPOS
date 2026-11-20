/* FestPOS Service Worker: App startet sofort und auch ohne Server (z.B. während eines Failovers).
   - App-Dateien: Cache zuerst, im Hintergrund aktualisieren
   - Produktbilder (/media/…): unveränderlich -> Cache zuerst
   - API: immer Netzwerk (die App hat ihren eigenen Offline-Puffer) */
const VERSION = "festpos-__FESTPOS_VERSION__";   // setzt der Server aus der Datei VERSION ein
const SHELL = [
  "/", "/index.html", "/app.css", "/common.js", "/i18n.js", "/app.js", "/manifest.webmanifest",
  // die übrigen Skripte der App (index.html, gleiche Reihenfolge) – fehlt eines, startet die App offline nicht
  "/app/device.js", "/app/net.js", "/app/view.js", "/app/plan.js", "/app/tabs.js", "/app/order.js", "/app/send.js", "/app/pay.js",
  "/app/payment.js", "/app/payrexx.js", "/app/storno.js", "/app/lock.js", "/app/settings.js", "/app/tour.js", "/app/sheets.js",
  "/app/scanner.js", "/app/input.js", "/app/start.js",
  "/fonts/barlow-condensed-latin-500-normal.woff2",
  "/fonts/barlow-condensed-latin-600-normal.woff2",
  "/fonts/barlow-condensed-latin-700-normal.woff2",
  "/icons/icon-192.png", "/icons/icon-512.png",
];

self.addEventListener("install", (e) => {
  // cache: "reload" – am HTTP-Cache des Browsers vorbei, sonst landet evtl. das ALTE app.js im neuen Cache
  e.waitUntil(caches.open(VERSION)
    .then((c) => c.addAll(SHELL.map((u) => new Request(u, { cache: "reload" }))))
    .then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  if (url.pathname.startsWith("/api/")) return;              // Netzwerk, kein Cache
  if (url.pathname.startsWith("/admin") || url.pathname.startsWith("/status")) return;   // Verwaltung, Statusanzeige: frisch vom Server

  if (url.pathname.startsWith("/media/") || url.pathname.startsWith("/vendor/")) {
    e.respondWith(caches.open(VERSION).then(async (c) => {
      const hit = await c.match(e.request);
      if (hit) return hit;
      const res = await fetch(e.request);
      if (res.ok) c.put(e.request, res.clone());
      return res;
    }));
    return;
  }

  // App-Dateien: sofort aus dem Cache, parallel frisch holen (stale-while-revalidate)
  e.respondWith(caches.open(VERSION).then(async (c) => {
    const key = url.pathname === "/" ? "/index.html" : e.request;
    const hit = await c.match(key);
    // beim Server nachfragen (no-cache), nicht eine evtl. veraltete Kopie aus dem HTTP-Cache nehmen
    const fresh = fetch(url.pathname === "/" ? "/index.html" : url.pathname + url.search, { cache: "no-cache" }).then((res) => {
      if (res.ok) c.put(key, res.clone());
      return res;
    }).catch(() => hit);
    return hit || fresh;
  }));
});
