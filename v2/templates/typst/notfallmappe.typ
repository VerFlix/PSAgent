// Notfallmappe (Parität 22, Konzept Abschnitt 7): Stand aller Teile, anstehende Ausleihen und
// leere Formulare mit fortlaufender Nummer. Alle Werte kommen als JSON und werden nur als Text
// gesetzt, nie als Typst-Code ausgewertet.
#import "formular.typ": linie, kaestchen
#let data = json(bytes(sys.inputs.data))

#set document(title: "Notfallmappe – Stand " + data.stand)
#set page(paper: "a4", margin: (x: 1.5cm, top: 1.5cm, bottom: 1.8cm), footer: context [
  #set text(size: 7pt)
  PSAgent · Notfallmappe · Stand #data.stand #h(1fr) Seite #counter(page).display() von #counter(page).final().first()
])
#set text(lang: "de", size: 9pt)
#set table(stroke: 0.4pt + gray, inset: 3pt)

#let titel(t) = text(size: 14pt, weight: "bold", t)
#let feld(beschriftung, breite: 100%) = [#beschriftung #v(10pt) #linie(width: breite)]
#let unterschrift(wer) = block(width: 100%)[#v(18pt) #linie() #text(size: 7.5pt, wer)]

// --- Deckblatt ---
#titel[Notfallmappe]
#if data.club != "" [ · #data.club]

Stand: *#data.stand*. Diese Mappe gilt, solange PSAgent nicht erreichbar ist. Danach wird alles
mit Formularnummer und Scan nacherfasst.

#block(stroke: 0.8pt, inset: 8pt, width: 100%)[
  *Regeln im Notbetrieb*
  - Ausgegeben wird nur, was laut dieser Mappe nicht gesperrt ist und dessen nächste Prüfung
    nach dem Rückgabedatum liegt. Teile mit „keine PSA“ brauchen keine Prüfung.
  - Neue Teile („neu, nicht freigegeben“) werden nicht ausgegeben.
  - Sperren darf jede Person ab Verleiher, mit Grund, auf dem Formular „Sperrung“.
  - Entsperren und Aussondern macht nur der PSA-Prüfer, auf dem Prüfprotokoll mit Unterschrift.
  - Neue Reservierungen werden nur vorgemerkt und nach der Wiederherstellung geprüft.
  - Für jede Ausgabe ein eigenes Formular „Ausgabe und Rückgabe“; kein Blatt wegwerfen,
    verschriebene Blätter durchstreichen und behalten.
  - Ausgefüllte Formulare kommen in das feste Fach im DAV.
]

*Formularnummern in dieser Mappe*
#table(
  columns: (auto, auto),
  ..data.ranges.flatten().map(v => [#v]),
)

Entleiher stehen hier nur mit Namen. Adresse und Kontakt stehen auf dem ausgefüllten Formular.

// --- Teile ---
#pagebreak()
#titel[Teile]
#v(2pt)
Je Bauteil eine Zeile, sortiert nach Teil. Ausgesonderte Teile fehlen.
#table(
  columns: (auto, 1.2fr, 1fr, auto, auto, 1.4fr),
  table.header(..("EI", "Teil", "Bezeichnung", "Status", "nächste Prüfung", "GAL").map(c => strong(c))),
  ..data.parts.flatten().map(v => [#v]),
)

// --- Ausleihen und Reservierungen ---
#pagebreak()
#titel[Ausleihen und Reservierungen]
#v(2pt)
Laufende Ausleihen und Reservierungen, die in den nächsten vier Wochen beginnen.
#if data.loans.len() == 0 [

  Keine.
] else {
  table(
    columns: (1.2fr, 1fr, auto, auto, auto, 1.2fr),
    table.header(..("Teil", "EI", "von", "bis", "Status", "Entleiher").map(c => strong(c))),
    ..data.loans.flatten().map(v => [#v]),
  )
}

// --- Formulare ---
#let kopf(blatt) = grid(
  columns: (1fr, auto),
  titel(blatt.title),
  align(right, text(size: 13pt, weight: "bold")[Formular #blatt.label]),
)

#let ausgabe(blatt) = [
  #kopf(blatt)
  #v(4pt)
  *Ausgabe*
  #grid(
    columns: (1fr, 1fr),
    gutter: 10pt,
    feld[Datum Ausgabe], feld[Rückgabe geplant am],
  )
  #table(
    columns: (1.2fr, 1.6fr, auto, auto),
    inset: 5pt,
    table.header(..("Teil (EI)", "Bezeichnung", "Kurzkontrolle", "GAL bereitgestellt").map(c => strong(c))),
    ..range(6).map(_ => ([#v(8pt)], [], [#kaestchen], [#kaestchen])).flatten(),
  )
  *Entleiher*
  #grid(
    columns: (1fr, 1fr),
    gutter: 10pt,
    feld[Name], feld[Telefon],
    feld[Adresse], feld[E-Mail],
  )
  #grid(
    columns: (1fr, 1fr),
    gutter: 16pt,
    unterschrift[Unterschrift Entleiher],
    unterschrift[Name und Unterschrift ausgebende Person],
  )
  #text(size: 7pt, data.notice)
  #v(6pt)
  #line(length: 100%, stroke: (dash: "dashed"))
  *Rückgabe*
  #grid(
    columns: (1fr, 1fr),
    gutter: 10pt,
    feld[Datum Rückgabe], feld[Kurzkontrolle bei Rückgabe #kaestchen],
  )
  #feld[Rückgabe-Kommentar (besondere Vorkommnisse, harter Sturz, Kontakt mit Chemikalien)]
  #feld[#kaestchen Sperrung nötig, Teil (EI) und Grund:]
  #grid(
    columns: (1fr, 1fr),
    gutter: 16pt,
    unterschrift[Unterschrift zurückgebende Person],
    unterschrift[Name und Unterschrift rücknehmende Person],
  )
]

#let sperrung(blatt) = [
  #kopf(blatt)
  #v(4pt)
  Je Zeile eine Sperrung. Das gesperrte Teil wird nicht mehr ausgegeben.
  #table(
    columns: (1fr, 2.2fr, auto, 1fr, 1fr),
    inset: 5pt,
    table.header(..("Teil (EI)", "Grund", "Datum", "Name sperrende Person", "Unterschrift").map(c => strong(c))),
    ..range(10).map(_ => ([#v(22pt)], [], [], [], [])).flatten(),
  )
]

#let pruefprotokoll(blatt) = [
  #kopf(blatt)
  #v(4pt)
  Nur für PSA-Prüfer mit gültiger Sachkunde. Ergebnis je Teil ankreuzen.
  #table(
    columns: (1fr, auto, auto, auto, 1.6fr, auto, auto),
    inset: 5pt,
    table.header(..("Teil (EI)", "in Ordnung", "Mangel", "aussondern", "Kommentar", "nächste Prüfung", "entsperren").map(c => strong(c))),
    ..range(12).map(_ => ([#v(14pt)], [#kaestchen], [#kaestchen], [#kaestchen], [], [], [#kaestchen])).flatten(),
  )
  #grid(
    columns: (1fr, 1fr, 1fr),
    gutter: 12pt,
    feld[Datum der Prüfung],
    feld[Name PSA-Prüfer],
    unterschrift[Unterschrift PSA-Prüfer],
  )
]

#for blatt in data.sheets {
  pagebreak()
  if blatt.kind == "A" { ausgabe(blatt) } else if blatt.kind == "S" { sperrung(blatt) } else { pruefprotokoll(blatt) }
}
