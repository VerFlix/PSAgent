// Persönliche QR-Karte für die Theke (#126): Scheckkarte 85,6 x 54 mm auf A4, zum Ausschneiden.
// Werte kommen als JSON und werden nur als Text gesetzt; der QR-Code als SVG-Bild.
#let data = json(bytes(sys.inputs.data))

#set document(title: "Theke-Karte")
#set page(paper: "a4", margin: 20mm)
#set text(lang: "de", size: 8pt)

#box(width: 85.6mm, height: 54mm, stroke: (paint: luma(150), dash: "dashed", thickness: 0.5pt), inset: 4mm)[
  #grid(
    columns: (38mm, 1fr),
    column-gutter: 3mm,
    align(horizon, image(bytes(data.qr), format: "svg", width: 38mm, height: 38mm)),
    align(horizon)[
      #text(size: 10pt, weight: "bold")[PSAgent · Theke] \
      #if data.club != "" [#data.club \ ]
      #v(2mm)
      #text(size: 9pt)[#data.name]
      #v(3mm)
      #text(size: 6.5pt)[Gilt nur zusammen mit deiner PIN und nur an der Theke. Verloren? Dem
        Admin Bescheid geben und unter „Mein Konto“ einen neuen QR-Code drucken; der alte gilt
        dann nicht mehr.]
    ],
  )
]

#v(6mm)
#text(size: 9pt)[Entlang der gestrichelten Linie ausschneiden. Diese Seite nicht aufbewahren,
  sobald die Karte ausgeschnitten ist.]
