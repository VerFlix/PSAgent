// Service Worker für die Offline-Kopie der Notfallmappe (Parität 22, Konzept Abschnitt 7).
//
// Er kümmert sich nur um eine Adresse: die neueste Notfallmappe. Ist der Server erreichbar,
// kommt sie vom Server; sonst aus dem Gerätespeicher. Aktualisiert wird auf Nachricht der Seite
// („refresh“), mit ETag, damit eine unveränderte Mappe nicht erneut übertragen wird.
"use strict";

const CACHE = "psagent-notfallmappe-v1";
const PDF = new URL("mappe/neueste.pdf", self.registration.scope).pathname;

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

async function refresh() {
  const cache = await caches.open(CACHE);
  const cached = await cache.match(PDF);
  const headers = {};
  if (cached && cached.headers.get("ETag")) {
    headers["If-None-Match"] = cached.headers.get("ETag");
  }
  let response;
  try {
    response = await fetch(PDF, {
      credentials: "same-origin",
      headers,
      redirect: "manual",
      cache: "no-store",
    });
  } catch (error) {
    return "offline";
  }
  if (response.status === 304) {
    return "unchanged";
  }
  const type = response.headers.get("Content-Type") || "";
  if (response.status === 200 && type.startsWith("application/pdf")) {
    await cache.put(PDF, response);
    return "updated";
  }
  return "failed";
}

async function status() {
  const cached = await (await caches.open(CACHE)).match(PDF);
  return cached ? cached.headers.get("X-Notfallmappe-Stand") || "" : "";
}

self.addEventListener("message", (event) => {
  if (event.data !== "refresh" && event.data !== "status") {
    return;
  }
  const work = event.data === "refresh" ? refresh() : Promise.resolve("status");
  event.waitUntil(
    work.then(async (result) => {
      if (event.source) {
        event.source.postMessage({ notfallmappe: result, stand: await status() });
      }
    })
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin || url.pathname !== PDF || event.request.method !== "GET") {
    return;
  }
  event.respondWith(
    fetch(event.request).catch(async () => {
      const cached = await (await caches.open(CACHE)).match(PDF);
      return cached || Response.error();
    })
  );
});
