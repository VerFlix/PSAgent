// Prüfmodus (Parität 7): QR-Codes mit der Kamera lesen und die EI ohne Seitenwechsel in die
// Runde geben. Erkennung mit dem eingebauten BarcodeDetector, sonst mit jsQR (lokal, ohne Netz).
// Der QR-Code enthält „…/q/<EI>“; der Server nimmt die EI daraus. Ohne Kamera bleibt das Feld.
(function () {
  "use strict";

  // Schnellwahl für „nächste Prüfung“: setzt das Datumsfeld im selben Formular.
  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-set-date]");
    if (!button || !button.form) return;
    var input = button.form.querySelector('input[type="date"]');
    if (input) input.value = button.dataset.setDate;
  });

  var form = document.getElementById("scan-form");
  if (!form) return;
  var input = form.querySelector('input[name="ei"]');
  var video = document.getElementById("camera");
  var start = document.getElementById("camera-start");
  var stop = document.getElementById("camera-stop");
  var status = document.getElementById("camera-status");
  var canvas = document.createElement("canvas");
  var context = canvas.getContext("2d", { willReadFrequently: true });
  var stream = null;
  var timer = null;
  var detector = null;
  var last = "";
  var lastAt = 0;

  // Nach jedem Hinzufügen: Feld leeren, bereit für das nächste Teil.
  form.addEventListener("htmx:afterRequest", function () {
    input.value = "";
    input.focus();
  });

  function found(value) {
    if (!value) return;
    var now = Date.now();
    if (value === last && now - lastAt < 3000) return; // derselbe Code noch im Bild
    last = value;
    lastAt = now;
    input.value = value;
    form.requestSubmit();
  }

  function scan() {
    if (!stream || video.readyState < 2) return;
    if (detector) {
      detector.detect(video).then(function (codes) {
        if (codes.length) found(codes[0].rawValue);
      }).catch(function () {});
      return;
    }
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    context.drawImage(video, 0, 0, canvas.width, canvas.height);
    var image = context.getImageData(0, 0, canvas.width, canvas.height);
    var code = window.jsQR(image.data, image.width, image.height, { inversionAttempts: "dontInvert" });
    if (code) found(code.data);
  }

  function halt() {
    if (timer) window.clearInterval(timer);
    timer = null;
    if (stream) stream.getTracks().forEach(function (track) { track.stop(); });
    stream = null;
    video.hidden = true;
    stop.hidden = true;
    start.hidden = false;
    status.textContent = "";
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
  stop.addEventListener("click", halt);
  window.addEventListener("pagehide", halt);
})();
