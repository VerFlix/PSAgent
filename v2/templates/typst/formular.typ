// Gemeinsame Bausteine der Formulare je Teil (Parität 5), nach `Typst_Vorlagen/` im Altsystem.
// Alle Werte kommen als JSON und werden nur als Text gesetzt, nie als Typst-Code ausgewertet.
#let data = json(bytes(sys.inputs.data))

// Seite wie im Altsystem: A4, Rand 1,5 cm; dazu eine Fußzeile mit Teil und Seitenzahl.
#let seite(titel, body) = {
  set document(title: titel + " – " + data.part)
  set page(paper: "a4", margin: 1.5cm, footer: context [
    #set text(size: 7pt)
    PSAgent · #data.part · erzeugt am #data.stand #h(1fr) Seite #counter(page).display() von #counter(page).final().first()
  ])
  set text(lang: "de", size: 9pt)
  body
}

#let linie(width: 100%) = box(height: 0.6pt, width: width, stroke: 0.6pt)
#let kaestchen = box(stroke: 0.6pt, width: 7pt, height: 7pt, baseline: 1pt)
#let ueberschrift(text-inhalt) = text(size: 14pt, weight: "bold", text-inhalt)
#let hinweis(body) = block(stroke: 0.8pt, inset: 6pt, width: 100%, body)

// Briefkopf/Stempel: Überschrift, Linie, darunter die Zeilen aus dem Konto bzw. der Verein.
// Ohne Zeilen bleibt Platz für einen Stempel von Hand.
#let stempel(titel, zeilen) = block(stroke: 1pt, inset: 6pt, width: 100%)[
  #strong(titel) \
  #linie() \
  #if zeilen.len() == 0 { v(24pt) } else { zeilen.join(linebreak()) }
]

// Alle Bauteile (das Altsystem setzte höchstens drei): wie dort die Felder als Zeilen, je
// Bauteil eine Spalte; mehr als vier Bauteile laufen in weiteren Tabellen darunter weiter.
#let bauteile() = block(stroke: 0.8pt, inset: 6pt, width: 100%)[
  #strong(if data.system { "System: " } else { "Produkt: " })#data.part
  #set text(size: 8pt)
  #for gruppe in data.rows.chunks(4) {
    let leer = range(4 - gruppe.len()).map(_ => "")
    let breite = if data.system { 4 } else { 1 }
    table(
      columns: (auto,) + (1fr,) * breite,
      stroke: 0.4pt + gray,
      inset: 3pt,
      ..if data.system {
        ([],) + (gruppe.map(r => strong("Bauteil " + r.at(0))) + leer).map(c => [#c])
      },
      ..range(1, data.columns.len())
        .map(i => (
          (data.columns.at(i),)
            + (gruppe.map(r => r.at(i)) + if data.system { leer } else { () })
        ).map(v => [#v]))
        .flatten(),
    )
  }
]
