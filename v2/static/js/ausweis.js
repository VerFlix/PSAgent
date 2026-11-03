// Mitgliedsausweis (#125): den Strichcode mit der Kamera des Tablets lesen und die Nummer ins
// Feld geben. Erkennung mit dem eingebauten BarcodeDetector (Chrome auf Android); ohne ihn
// bleiben Handscanner und Eintippen.
//
// Aufbau des Strichcodes (Felix, 03.11.2026, nach dem DAV-Muster):
// „SSS/OO/MMMMMM*Kategorie*Eintritt DAV*Eintritt Sektion*Geburtsdatum“. Übernommen werden nur
// Sektion, Ortsgruppe und Mitgliedsnummer; alles ab dem ersten „*“, z. B. das Geburtsdatum,
// verlässt das Tablet nicht. Der Server verwirft es zusätzlich, falls es doch ankommt.
(function () {
  "use strict";

  var FORMATS = [
    "code_128", "code_39", "code_93", "codabar", "ean_13", "ean_8", "itf", "upc_a", "upc_e",
    "qr_code", "data_matrix", "pdf417", "aztec",
  ];
  var CARD = /^\s*(\d{3})\s*[/.\-]?\s*(\d{2})\s*[/.\-]?\s*(\d{6})\s*(?:\*[\s\S]*)?$/;
  var SETTLE_MS = 120; // Handscanner tippen schneller; danach ist die Zeile vollständig
  var NO_DETECTOR = "Dieser Browser liest keine Strichcodes mit der Kamera. Bitte den Handscanner "
    + "nutzen oder die Nummer vorne in der Zeile unter dem Strichcode eintippen.";

  function memberNumber(value) {
    var match = CARD.exec(value || "");
    return match ? match[1] + "/" + match[2] + "/" + match[3] : "";
  }

  // Nur der Aufbau, ohne Ziffern und Buchstaben: hilft bei der Fehlersuche, ohne Personendaten
  // auf den Bildschirm zu bringen.
  function shape(value) {
    return (value || "").replace(/[0-9]/g, "9").replace(/[A-Za-zÄÖÜäöüß]/g, "A").slice(0, 60);
  }

  function setup(box) {
    var input = document.getElementById(box.dataset.cardScan);
    var start = box.querySelector("[data-camera-start]");
    var stop = box.querySelector("[data-camera-stop]");
    var status = box.querySelector("[data-camera-status]");
    var video = box.querySelector("video");
    var stream = null;
    var timer = null;
    var detector = null;
    var settle = null;
    if (!input || !start || !stop || !status || !video) return;

    // Übernimmt einen gelesenen Ausweis: nur die Nummer kommt ins Feld.
    function take(value) {
      var number = memberNumber(value);
      if (number) {
        input.value = number;
        status.textContent = "Ausweis gelesen.";
      } else {
        status.textContent = "Strichcode gelesen, aber keine Mitgliedsnummer erkannt (Aufbau: "
          + shape(value) + "). Bitte die Nummer unter dem Strichcode eintippen.";
        // Was ein Handscanner nach dem ersten „*“ getippt hat, bleibt nicht im Feld.
        if (input.value.indexOf("*") >= 0) input.value = input.value.split("*")[0].trim();
      }
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
      input.focus();
    }

    // Handscanner tippen die ganze Zeile ins Feld. Sobald ein „*“ darin steht, wird sie nach
    // kurzer Ruhe auf die Nummer gekürzt; die Suche (htmx, 300 ms Verzögerung) sendet erst danach.
    input.addEventListener("input", function () {
      if (input.value.indexOf("*") < 0) return;
      window.clearTimeout(settle);
      settle = window.setTimeout(function () { take(input.value); }, SETTLE_MS);
    });
    // Viele Handscanner schicken am Ende Enter; das soll kein Formular abschicken.
    input.addEventListener("keydown", function (event) {
      if (event.key !== "Enter") return;
      if (input.value.indexOf("*") < 0 && !memberNumber(input.value)) return;
      event.preventDefault();
      window.clearTimeout(settle);
      take(input.value);
    });

    function halt() {
      if (timer) window.clearInterval(timer);
      timer = null;
      if (stream) stream.getTracks().forEach(function (track) { track.stop(); });
      stream = null;
      video.hidden = true;
      stop.hidden = true;
      start.hidden = false;
    }

    function scan() {
      if (!stream || !detector || video.readyState < 2) return;
      detector.detect(video).then(function (codes) {
        if (!codes.length || !stream) return;
        halt();
        take(codes[0].rawValue);
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
