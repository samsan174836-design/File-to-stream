const CACHE_NAME = "karva-pwa-v1";
const APP_SHELL = [
    "/manifest.webmanifest",
    "/static/pwa-register.js",
    "/static/offline.html",
    "/static/icons/app-icon-180.png",
    "/static/icons/app-icon-192.png",
    "/static/icons/app-icon-512.png",
    "/static/icons/app-icon-512-maskable.png"
];

self.addEventListener("install", (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL))
    );
    self.skipWaiting();
});

self.addEventListener("activate", (event) => {
    event.waitUntil((async () => {
        const cacheNames = await caches.keys();
        await Promise.all(
            cacheNames
                .filter((cacheName) => cacheName.startsWith("karva-pwa-") && cacheName !== CACHE_NAME)
                .map((cacheName) => caches.delete(cacheName))
        );
        await self.clients.claim();
    })());
});

self.addEventListener("fetch", (event) => {
    const request = event.request;
    if (request.method !== "GET") return;

    const url = new URL(request.url);
    if (url.origin !== self.location.origin) return;

    if (request.mode === "navigate") {
        event.respondWith(
            fetch(request).catch(async () =>
                (await caches.match("/static/offline.html")) || Response.error()
            )
        );
        return;
    }

    if (!APP_SHELL.includes(url.pathname)) return;
    event.respondWith((async () => {
        const cached = await caches.match(request);
        if (cached) return cached;
        const response = await fetch(request);
        if (response.ok) {
            const cache = await caches.open(CACHE_NAME);
            await cache.put(request, response.clone());
        }
        return response;
    })());
});
