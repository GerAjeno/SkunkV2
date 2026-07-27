const CACHE = "skunk-pc-static-v12";
const STATIC_ASSETS = [
  "/static/app.css",
  "/static/app.js",
  "/static/manifest.webmanifest",
  "/static/icon-192.png",
  "/static/icon-512.png",
  "/static/apple-touch-icon.png",
  "/static/favicon-32.png"
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(STATIC_ASSETS)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const requestUrl = new URL(event.request.url);
  if (
    event.request.method !== "GET" ||
    requestUrl.origin !== self.location.origin ||
    !requestUrl.pathname.startsWith("/static/")
  ) {
    return;
  }
  event.respondWith(
    caches.match(event.request).then((cached) =>
      cached || fetch(event.request).then((response) => {
        const copy = response.clone();
        caches.open(CACHE).then((cache) => cache.put(event.request, copy));
        return response;
      })
    )
  );
});
