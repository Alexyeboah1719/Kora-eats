/* KORA EATS service worker.
 *
 * What is cached (and what is NOT) matters for privacy:
 *  - App shell + static files ............ cached, so the app opens instantly and works offline.
 *  - PUBLIC catalog API (menus, vendors) .. network first, falls back to the last copy when offline.
 *  - EVERYTHING ELSE under /api/ .......... never cached (cart, orders, payments, profile, notifications,
 *                                           admin, rider...). Private data must not sit in a shared cache.
 *  - Map tiles ............................ never cached (OpenStreetMap tile usage policy).
 */
const VERSION = "v2";
const SHELL_CACHE = `kora-eats-shell-${VERSION}`;
const RUNTIME_CACHE = `kora-eats-runtime-${VERSION}`;
const API_CACHE = `kora-eats-api-${VERSION}`;
const SHELL = ["/", "/offline.html", "/js/offline.js", "/css/styles.css", "/css/app.css", "/js/ui.js", "/js/app.js", "/js/cart.js", "/js/notifs.js", "/js/pwa.js",
  "/icons/icon-192.png", "/icons/favicon-64.png", "/manifest.webmanifest"];
// The only API responses that may be stored: public, identical for everyone.
const PUBLIC_API = [/^\/api\/home$/, /^\/api\/categories$/, /^\/api\/vendors(\/\d+)?$/, /^\/api\/menu-items$/,
  /^\/api\/taxonomy$/, /^\/api\/vendor-requirements$/, /^\/api\/ai\/options$/];   // same for everyone: safe to keep for offline use

/** Decides how to handle a request. Pure function (also unit-tested from Node). */
function classify(url, method, mode, sameOrigin) {
  if (method !== "GET") return "network-only";
  if (url.hostname.endsWith("tile.openstreetmap.org")) return "network-only";
  if (!sameOrigin) return /(jsdelivr|googleapis|gstatic|cdnjs)/.test(url.hostname) ? "stale-while-revalidate" : "network-only";
  if (url.pathname.startsWith("/api/")) return PUBLIC_API.some((re) => re.test(url.pathname)) ? "network-first-api" : "network-only";
  if (url.pathname.startsWith("/uploads/") || /\.(css|js|png|jpg|jpeg|webp|svg|webmanifest|woff2?)$/.test(url.pathname)) return "stale-while-revalidate";
  if (mode === "navigate") return "network-first-page";
  return "network-only";
}

if (typeof self !== "undefined" && self.addEventListener) {
  self.addEventListener("install", (e) => {
    e.waitUntil(caches.open(SHELL_CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
  });

  self.addEventListener("activate", (e) => {
    e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => ![SHELL_CACHE, RUNTIME_CACHE, API_CACHE].includes(k)).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()));
  });

  const putCopy = (cacheName, req, res) => { if (res && res.ok) { const copy = res.clone(); caches.open(cacheName).then((c) => c.put(req, copy)); } return res; };

  self.addEventListener("fetch", (e) => {
    const req = e.request, url = new URL(req.url);
    const kind = classify(url, req.method, req.mode, url.origin === self.location.origin);
    if (kind === "network-only") return;                       // let the browser handle it normally

    if (kind === "stale-while-revalidate") {
      e.respondWith(caches.open(RUNTIME_CACHE).then(async (cache) => {
        const cached = await cache.match(req);
        const fresh = fetch(req).then((res) => { if (res && (res.ok || res.type === "opaque")) cache.put(req, res.clone()); return res; }).catch(() => cached);
        return cached || fresh;
      }));
    } else if (kind === "network-first-api") {
      e.respondWith(fetch(req).then((res) => putCopy(API_CACHE, req, res)).catch(async () => {
        const hit = await caches.match(req, { cacheName: API_CACHE });
        return hit || new Response(JSON.stringify({ detail: { code: "offline", message: "You're offline." } }), { status: 503, headers: { "Content-Type": "application/json" } });
      }));
    } else if (kind === "network-first-page") {
      e.respondWith(fetch(req).then((res) => putCopy(SHELL_CACHE, req, res)).catch(async () => (await caches.match(req)) || (await caches.match("/")) || caches.match("/offline.html")));
    }
  });

  // ---- Web Push
  self.addEventListener("push", (e) => {
    let data = {};
    try { data = e.data ? e.data.json() : {}; } catch (_) { data = { title: "KORA EATS", body: e.data ? e.data.text() : "" }; }
    e.waitUntil(self.registration.showNotification(data.title || "KORA EATS", {
      body: data.body || "", icon: "/icons/icon-192.png", badge: "/icons/favicon-64.png", tag: data.type || "foodapp", renotify: true,
      data: { url: typeof data.url === "string" && data.url.startsWith("/") ? data.url : "/" },
    }));
  });

  self.addEventListener("notificationclick", (e) => {
    e.notification.close();
    const target = new URL(e.notification.data && e.notification.data.url ? e.notification.data.url : "/", self.location.origin).href;
    e.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((wins) => {
      const w = wins.find((c) => c.url.startsWith(self.location.origin));
      return w ? w.focus().then(() => w.navigate(target)) : self.clients.openWindow(target);
    }));
  });
}

if (typeof module !== "undefined") module.exports = { classify, PUBLIC_API };
