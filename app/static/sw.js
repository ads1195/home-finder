/* Service worker: app-shell cache + offline viewing of listings you have already opened.
   - shell files: cache-first (versioned)
   - /api/listings*, /api/state, /api/refs...: network-first, fall back to last cached copy (offline)
   - /media/* images: cache-first (stale-while-revalidate)
   Also handles web-push + notification clicks. */
const VER = "v15";
const SHELL = "shell-" + VER, DATA = "data-" + VER, MEDIA = "media-" + VER;
const SHELL_FILES = ["/static/style.css", "/static/app.js", "/static/favs.js", "/static/votes.js", "/static/vendor/leaflet.js", "/static/vendor/leaflet.css",
  "/manifest.webmanifest", "/icons/icon-192.png", "/icons/icon-512.png"];

// Never cache an HTML page (e.g. the sign-in page) in place of a CSS/JS/image file.
function okAsset(path, r) {
  if (!r || !r.ok) return false;
  const ct = r.headers.get("content-type") || "";
  return !(ct.includes("text/html") && !/\.html?$/.test(path) && path !== "/");
}
self.addEventListener("install", e => {
  e.waitUntil(caches.open(SHELL).then(c => Promise.all(SHELL_FILES.map(f => fetch(f, {cache: "reload"}).then(r => { if (okAsset(f, r)) return c.put(f, r); }).catch(() => {})))).then(() => self.skipWaiting()));
});
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => !k.endsWith(VER)).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", e => {
  const req = e.request; if (req.method !== "GET") return;
  const u = new URL(req.url); if (u.origin !== location.origin) return;
  if (u.pathname.startsWith("/api/")) {
    if (/\/api\/(push|logout|config)/.test(u.pathname)) return;
    e.respondWith(fetch(req).then(r => { if (r.ok) { const cp = r.clone(); caches.open(DATA).then(c => c.put(req, cp)); } return r; })
      .catch(() => caches.match(req).then(r => r || new Response(JSON.stringify({offline: true, items: [], error: "offline"}), {status: 503, headers: {"Content-Type": "application/json"}}))));
    return;
  }
  if (u.pathname.startsWith("/media/")) {
    e.respondWith(caches.open(MEDIA).then(c => c.match(req).then(hit => hit || fetch(req).then(r => { if (r.ok) c.put(req, r.clone()); return r; }).catch(() => new Response("", {status: 504})))));
    return;
  }
  if (req.mode === "navigate") {
    e.respondWith(fetch(req).catch(() => caches.match("/")));
    return;
  }
  e.respondWith(caches.match(req).then(hit => hit || fetch(req).then(r => { if (okAsset(u.pathname, r)) { const cp = r.clone(); caches.open(SHELL).then(c => c.put(req, cp)); } return r; })));
});

self.addEventListener("push", e => {
  let d = {title: "New property match", body: "", url: "/"};
  try { d = Object.assign(d, e.data.json()); } catch (_) {}
  e.waitUntil(self.registration.showNotification(d.title, {body: d.body, icon: "/icons/icon-192.png", badge: "/icons/icon-192.png", tag: d.tag, data: {url: d.url}}));
});
self.addEventListener("notificationclick", e => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || "/";
  e.waitUntil(clients.matchAll({type: "window", includeUncontrolled: true}).then(ws => {
    for (const w of ws) if ("focus" in w) { w.navigate(url); return w.focus(); }
    return clients.openWindow(url);
  }));
});
