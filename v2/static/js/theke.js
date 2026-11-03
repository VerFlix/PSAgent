// Theke (#127), Schritt „Teile“: EI-Feld mit Art-Tasten und Ziffernblock, Vorschläge antippen,
// QR-Etiketten mit der Kamera lesen. Das Etikett enthält „…/q/<EI>“ (#14); der Server nimmt die
// EI daraus. Erkennung mit dem eingebauten BarcodeDetector, sonst mit jsQR (lokal, ohne Netz).
(function () {
  "use strict";

  var field = document.getElementById("ei-eingabe");
  var form = document.getElementById("ei-form");
  if (!field || !form) return;
  var prefix = field.dataset.prefix || "";

  function changed() {
    field.dispatchEvent(new Event("input", { bubbles: true }));
    field.focus();
  }

  document.addEventListener("click", function (event) {
    var art = event.target.closest("[data-ei-art]");
    if (art) {
      field.value = prefix + art.dataset.eiArt + "-";
      changed();
      return;
    }
    var key = event.target.closest("[data-ei-key]");
    if (key) {
      field.value += key.dataset.eiKey;
      changed();
      return;
    }
    if (event.target.closest("[data-ei-delete]")) {
      if (field.value.length > prefix.length) field.value = field.value.slice(0, -1);
      changed();
      return;
    }
    var pick = event.target.closest("[data-ei-pick]");
    if (pick) {
      field.value = pick.dataset.eiPick;
      form.requestSubmit();
    }
  });

  // Nach dem Hinzufügen: Feld zurück auf das Präfix, Vorschläge leeren. Bei einem Fehler bleibt
  // die Eingabe stehen, damit man sie korrigieren kann.
  form.addEventListener("htmx:afterRequest", function (event) {
    // Nur das Abschicken des Formulars, nicht die Vorschlagssuche des Feldes (steigt auf).
    if (event.detail.elt !== form || !event.detail.successful) return;
    if (document.querySelector("#schritt .notice.error")) return;
    field.value = prefix;
    var list = document.getElementById("ei-vorschlaege");
    if (list) list.replaceChildren();
  });

  var start = document.querySelector("[data-qr-start]");
  var stop = document.querySelector("[data-qr-stop]");
  var status = document.querySelector("[data-qr-status]");
  var video = document.querySelector("[data-qr-video]");
  if (!start || !stop || !status || !video) return;
  var canvas = document.createElement("canvas");
  var context = canvas.getContext("2d", { willReadFrequently: true });
  var stream = null;
  var timer = null;
  var detector = null;

  function halt() {
    if (timer) window.clearInterval(timer);
    timer = null;
    if (stream) stream.getTracks().forEach(function (track) { track.stop(); });
    stream = null;
    video.hidden = true;
    stop.hidden = true;
    start.hidden = false;
  }

  function found(value) {
    if (!value) return;
    halt();
    status.textContent = "QR-Etikett gelesen.";
    field.value = value;
    form.requestSubmit();
  }

  function scan() {
    if (!stream || video.readyState < 2) return;
    if (detector) {
      detector.detect(video).then(function (codes) {
        if (codes.length && stream) found(codes[0].rawValue);
      }).catch(function () {});
      return;
    }
    if (!window.jsQR) return;
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    context.drawImage(video, 0, 0, canvas.width, canvas.height);
    var image = context.getImageData(0, 0, canvas.width, canvas.height);
    var code = window.jsQR(image.data, image.width, image.height, { inversionAttempts: "dontInvert" });
    if (code) found(code.data);
  }

  start.addEventListener("click", function () {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      status.textContent = "Kamera nicht verfügbar (nur über HTTPS). Bitte die EI eingeben.";
      return;
    }
    navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" }, audio: false })
      .then(function (media) {
        stream = media;
        video.srcObject = media;
        video.hidden = false;
        start.hidden = true;
        stop.hidden = false;
        status.textContent = "Kamera läuft: QR-Etikett ins Bild halten.";
        if ("BarcodeDetector" in window) {
          detector = new window.BarcodeDetector({ formats: ["qr_code"] });
        }
        return video.play();
      })
      .then(function () { timer = window.setInterval(scan, 250); })
      .catch(function () {
        halt();
        status.textContent = "Kamera nicht freigegeben. Bitte die EI eingeben.";
      });
  });
  stop.addEventListener("click", function () {
    halt();
    status.textContent = "";
  });
  window.addEventListener("pagehide", halt);
})();
