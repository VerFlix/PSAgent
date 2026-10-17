// QR-Etiketten (#14): A4-Bogen mit 65 Etiketten 38 x 21,2 mm (5 x 13), z. B. Avery Zweckform
// 3666/L7651. Werte kommen als JSON und werden nur als Text gesetzt; der QR-Code als SVG-Bild.
#let data = json(bytes(sys.inputs.data))

// Maße des Bogens (mm): Rand oben/links, Abstand der Etiketten (Raster), Etikettengröße.
#let oben = 10.7mm
#let links = 4.7mm
#let raster-x = 40.6mm
#let raster-y = 21.2mm
#let breite = 38mm
#let hoehe = 21.2mm
#let spalten = 5
#let zeilen = 13
#let je-bogen = spalten * zeilen

#set document(title: "QR-Etiketten")
#set page(paper: "a4", margin: 0mm)
#set text(lang: "de", size: 6.5pt)

#let etikett(e) = box(width: breite, height: hoehe, inset: (x: 1.5mm, y: 1.2mm), clip: true)[
  #grid(
    columns: (16mm, 1fr),
    column-gutter: 1.5mm,
    align(horizon, image(bytes(e.qr), format: "svg", width: 16mm, height: 16mm)),
    align(horizon)[
      #text(size: 8pt, weight: "bold")[#e.ei] \
      #e.title
      #if e.detail != "" [ \ #text(size: 5.5pt)[#e.detail]]
    ],
  )
]

// Position 0 … je-bogen - 1 auf dem Bogen, beginnend beim Startetikett.
#let plaetze = range(data.start - 1, data.start - 1 + data.labels.len())
#let boegen = calc.max(1, calc.ceil((data.start - 1 + data.labels.len()) / je-bogen))

#for bogen in range(boegen) {
  if bogen > 0 { pagebreak() }
  for (i, e) in data.labels.enumerate() {
    let platz = plaetze.at(i)
    if calc.floor(platz / je-bogen) == bogen {
      let nr = calc.rem(platz, je-bogen)
      let x = links + calc.rem(nr, spalten) * raster-x
      let y = oben + calc.floor(nr / spalten) * raster-y
      place(top + left, dx: x, dy: y, etikett(e))
    }
  }
}
