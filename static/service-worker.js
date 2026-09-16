const CACHE = "unifix-shell-v5";
// Precache only assets that are public and never redirect — a single failed or
// redirected (login-gated) request would abort the whole install.
const SHELL = ["/offline", "/static/css/app.css", "/static/js/report.js",
               "/static/icons/icon-192.png"];

self.addEventListener("install", (e) => {
  e.waitUntil((async () => {
    const c = await caches.open(CACHE);
    await Promise.allSettled(SHELL.map((u) => c.add(u)));
    await self.skipWaiting();
  })());
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) =>
    Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
  ).then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  // Network-first, never-cache for auth-gated / always-live surfaces. The admin
  // portal is dynamic and session-scoped: a stale or login-redirected copy in
  // the cache must never be replayed (that is what makes an installed PWA show
  // "no access" after the session changes).
  if (url.pathname === "/login" || url.pathname === "/logout"
      || url.pathname.startsWith("/admin")
      || url.pathname.endsWith("/data") || url.pathname.startsWith("/report/")
      || url.pathname.startsWith("/photo/")
      || url.pathname === "/" || url.pathname === "/profile"
      || url.pathname === "/my-reports" || url.pathname === "/report"
      || url.pathname === "/notices" || url.pathname === "/set-pin"
      || url.pathname.startsWith("/grievance/")
      || url.pathname.startsWith("/auth/")) {
    e.respondWith(fetch(req).catch(() => caches.match(req)));
    return;
  }
  e.respondWith(caches.match(req).then((hit) => hit || fetch(req).then((res) => {
    const copy = res.clone();
    caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => {});
    return res;
  }).catch(() => caches.match(req.mode === "navigate" ? "/offline" : "/"))));
});
