// Offline-Kopie der Notfallmappe einrichten und aktuell halten (Parität 22).
//
// - Auf Vereinsgeräten (data-offline-binder="auto") registriert sich der Service Worker von
//   selbst und lädt bei jedem Seitenaufruf die neueste Mappe, wenn sie sich geändert hat.
// - Auf der Seite „Notbetrieb“ richtet ein Knopf die Offline-Kopie auch auf anderen Geräten
//   ein (data-offline-enable) und zeigt den Stand (data-offline-status).
"use strict";

(function () {
  const config = document.querySelector("[data-offline-binder]");
  if (!config || !("serviceWorker" in navigator)) {
    const status = document.querySelector("[data-offline-status]");
    if (status && !("serviceWorker" in navigator)) {
      status.textContent = "Dieser Browser kann keine Offline-Kopie speichern. Bitte die PDF von Hand sichern.";
    }
    return;
  }
  const script = config.dataset.sw;
  const scope = config.dataset.scope;
  const status = document.querySelector("[data-offline-status]");

  function show(message) {
    if (!status || !message) {
      return;
    }
    const data = message.data || {};
    if (!("notfallmappe" in data)) {
      return;
    }
    if (data.stand) {
      status.textContent = "Offline gespeichert: " + data.stand + ".";
    } else if (data.notfallmappe === "failed" || data.notfallmappe === "offline") {
      status.textContent = "Noch keine Offline-Kopie gespeichert.";
    } else {
      status.textContent = "Noch keine Offline-Kopie auf diesem Gerät.";
    }
    status.dataset.state = data.notfallmappe;
  }

  async function start(command) {
    const registration = await navigator.serviceWorker.register(script, { scope: scope });
    const worker = registration.active || registration.waiting || registration.installing;
    if (!worker) {
      return;
    }
    if (worker.state !== "activated") {
      await new Promise((resolve) => {
        worker.addEventListener("statechange", () => {
          if (worker.state === "activated") {
            resolve();
          }
        });
      });
    }
    worker.postMessage(command);
  }

  navigator.serviceWorker.addEventListener("message", show);

  if (config.dataset.offlineBinder === "auto") {
    start("refresh").catch(() => {});
  } else if (status) {
    navigator.serviceWorker.getRegistration(scope).then((registration) => {
      if (registration && registration.active) {
        registration.active.postMessage("status");
      } else {
        status.textContent = "Noch keine Offline-Kopie auf diesem Gerät.";
      }
    });
  }

  const button = document.querySelector("[data-offline-enable]");
  if (button) {
    button.addEventListener("click", () => {
      if (status) {
        status.textContent = "Wird gespeichert …";
      }
      start("refresh").catch(() => {
        if (status) {
          status.textContent = "Offline-Kopie konnte nicht eingerichtet werden.";
        }
      });
    });
  }
})();
