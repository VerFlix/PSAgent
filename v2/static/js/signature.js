// Unterschrift mit Finger, Stift oder Maus (Parität 14, 17). Die Striche landen als JSON
// ([[[x, y], …], …], ganzzahlig in Canvas-Koordinaten) im versteckten Feld des Formulars.
(function () {
  "use strict";

  function setup(canvas) {
    var input = document.getElementById(canvas.dataset.signatureFor);
    var context = canvas.getContext("2d");
    var strokes = [];
    var current = null;
    context.lineWidth = 2;
    context.lineCap = "round";
    context.strokeStyle = "#111";

    function point(event) {
      var box = canvas.getBoundingClientRect();
      return [
        Math.round(((event.clientX - box.left) * canvas.width) / box.width),
        Math.round(((event.clientY - box.top) * canvas.height) / box.height),
      ];
    }

    function save() {
      input.value = strokes.length ? JSON.stringify(strokes) : "";
    }

    canvas.addEventListener("pointerdown", function (event) {
      event.preventDefault();
      canvas.setPointerCapture(event.pointerId);
      current = [point(event)];
      strokes.push(current);
    });
    canvas.addEventListener("pointermove", function (event) {
      if (!current) {
        return;
      }
      var next = point(event);
      var last = current[current.length - 1];
      context.beginPath();
      context.moveTo(last[0], last[1]);
      context.lineTo(next[0], next[1]);
      context.stroke();
      current.push(next);
    });
    ["pointerup", "pointercancel"].forEach(function (name) {
      canvas.addEventListener(name, function () {
        current = null;
        save();
      });
    });

    var clear = document.querySelector('[data-signature-clear="' + input.id + '"]');
    if (clear) {
      clear.addEventListener("click", function () {
        strokes = [];
        context.clearRect(0, 0, canvas.width, canvas.height);
        save();
      });
    }
  }

  document.querySelectorAll("canvas[data-signature-for]").forEach(setup);
})();
