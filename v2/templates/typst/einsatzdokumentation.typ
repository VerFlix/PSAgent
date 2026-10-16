// Einsatzdokumentation (Parität 5): leeres Formular für die Einsatzhistorie eines Teils.
#import "formular.typ": *
#show: seite.with("Einsatzdokumentation")

#ueberschrift[Einsatzdokumentation]

#grid(
  columns: (1.5fr, 0.5fr),
  gutter: 6pt,
  [
    #hinweis[Diese Einsatzdokumentation ist dem beschriebenen Produkt zugeordnet und sollte bei
      der nächsten PSA-Überprüfung zusammen mit dem Produkt eingereicht werden (Einsatzhistorie).]
    #hinweis[Mit ihrer Unterschrift bestätigt die zurückgebende Person für den angegebenen
      Ausleih-Zeitraum die Richtigkeit der Angaben zu „Besondere Vorkommnisse“ und „Kontakt mit
      Chemikalien“.]
  ],
  stempel("Briefkopf/Stempel Besitzer*in", if data.club == "" { () } else { (data.club,) }),
)

#bauteile()
#v(4pt)

#set text(size: 8pt)
#let spalten = (
  "Datum Ausgabe",
  "Datum Rücknahme",
  "Kurzprüfung durchgeführt",
  "Beobachtungen / Kommentar",
  "Unterschrift AG Mitglied",
  "Besondere Vorkommnisse (harter Sturz, Kontakt mit Chemikalien, ...)",
  "Unterschrift des Zurückgebende*r",
)
// 45 Leerzeilen wie im Altsystem (17 auf der ersten, 28 auf der zweiten Seite).
#table(
  columns: (0.5fr, 0.6fr, 0.6fr, 1fr, 0.6fr, 1fr, 0.7fr),
  rows: (auto, 25pt),
  align: left,
  stroke: 0.5pt,
  inset: (x: 4pt, y: 2pt),
  table.header(..spalten.map(s => text(weight: "bold", s))),
  ..range(45 * spalten.len()).map(_ => []),
)
