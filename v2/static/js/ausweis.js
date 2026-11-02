// Mitgliedsausweis (#125): den Strichcode mit der Kamera des Tablets lesen und die Nummer ins
// Feld geben. Erkennung mit dem eingebauten BarcodeDetector (Chrome auf Android); ohne ihn
// bleiben Handscanner und Eintippen. Ob die Nummer gilt, entscheidet der Server (11 Ziffern).
(function () {
  "use strict";

  var FORMATS = [
    "code_128", "code_39", "code_93", "codabar", "ean_13", "ean_8", "itf", "upc_a", "upc_e",
    "qr_code", "data_matrix", "pdf417", "aztec",
  ];
  var NO_DETECTOR = "Dieser Browser liest keine Strichcodes mit der Kamera. Bitte den Handscanner "
    + "nutzen oder die 11 Ziffern unter dem Strichcode eintippen.";

  function setup(box) {
    var input = document.getElementById(box.dataset.cardScan);
    var start = box.querySelector("[data-camera-start]");
    var stop = box.querySelector("[data-camera-stop]");
    var status = box.querySelector("[data-camera-status]");
    var video = box.querySelector("video");
    var stream = null;
    var timer = null;
    var detector = null;
    if (!input || !start || !stop || !status || !video) return;

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
      halt();
      input.value = (value || "").trim();
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
      input.focus();
      var digits = input.value.replace(/[\s/.\-]/g, "");
      status.textContent = /^[0-9]{11}$/.test(digits)
        ? "Ausweis gelesen."
        : "Gelesen, aber keine 11 Ziffern: bitte die Nummer unter dem Strichcode eintippen.";
    }

    function scan() {
      if (!stream || !detector || video.readyState < 2) return;
      detector.detect(video).then(function (codes) {
        if (codes.length && stream) found(codes[0].rawValue);
      }).catch(function () {});
    }

    start.addEventListener("click", function () {
      if (!("BarcodeDetector" in window)) {
        status.textContent = NO_DETECTOR;
        return;
      }
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        status.textContent = "Kamera nicht verfügbar (nur über HTTPS). Bitte die Nummer eintippen.";
        return;
      }
      window.BarcodeDetector.getSupportedFormats()
        .then(function (supported) {
          var formats = FORMATS.filter(function (format) { return supported.indexOf(format) >= 0; });
          if (!formats.length) throw new Error("keine Strichcode-Formate");
          detector = new window.BarcodeDetector({ formats: formats });
          return navigator.mediaDevices.getUserMedia({
            video: { facingMode: "environment" }, audio: false,
          });
        })
        .then(function (media) {
          stream = media;
          video.srcObject = media;
          video.hidden = false;
          start.hidden = true;
          stop.hidden = false;
          status.textContent = "Kamera läuft: Strichcode des Ausweises ins Bild halten.";
          return video.play();
        })
        .then(function () { timer = window.setInterval(scan, 250); })
        .catch(function () {
          halt();
          status.textContent = detector
            ? "Kamera nicht freigegeben. Bitte die Nummer eintippen."
            : NO_DETECTOR;
        });
    });
    stop.addEventListener("click", function () {
      halt();
      status.textContent = "";
    });
    window.addEventListener("pagehide", halt);
  }

  document.querySelectorAll("[data-card-scan]").forEach(setup);
})();
