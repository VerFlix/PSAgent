// Prüfprotokoll einer Prüfungsrunde (Parität 8, #38). Alle Werte kommen als JSON und werden nur
// als Text gesetzt, nie als Typst-Code ausgewertet. Das Signaturfeld legt pyHanko danach genau
// über den Rahmen unten auf der letzten Seite (Lage aus `data.field`, in cm).
#let data = json(bytes(sys.inputs.data))
#let feld = data.field

#set document(title: data.title, keywords: data.keywords)
#set text(lang: "de", size: 9pt)
#set page(
  paper: "a4",
  margin: (x: 1.5cm, top: 1.5cm, bottom: (29.7 - feld.top + 0.6) * 1cm),
  // Fußzeile und Signaturrahmen liegen fest auf der Seite, die Fußzeile unter dem Rahmen.
  background: context {
    let letzte = counter(page).final().first()
    if here().page() == letzte {
      place(top + left, dx: feld.left * 1cm, dy: (feld.top - 0.45) * 1cm,
        text(size: 8pt, weight: "bold")[Signatur PSA-Sachkundige\*r])
      place(top + left, dx: feld.left * 1cm, dy: feld.top * 1cm,
        rect(width: feld.width * 1cm, height: feld.height * 1cm, stroke: 0.6pt + gray))
    }
    place(top + left, dx: 1.5cm, dy: 28.6cm, box(width: 18cm, text(size: 7pt)[
      PSAgent · #data.title · abgeschlossen am #data.closed #h(1fr) Seite #here().page() von #letzte
    ]))
  },
)

#text(size: 14pt, weight: "bold")[Prüfprotokoll PSA-Sachkundigenprüfung]

#grid(
  columns: (1fr, 1fr),
  gutter: 8pt,
  block(stroke: 0.8pt, inset: 6pt, width: 100%)[
    #table(
      columns: (auto, 1fr),
      stroke: none,
      inset: 2pt,
      [Verein:], [#data.club],
      [Prüfungsrunde:], [#data.round],
      [PSA-Sachkundige\*r:], [#data.expert],
      [Sachkunde gültig bis:], [#data.qualification_until],
      [Begonnen:], [#data.started],
      [Abgeschlossen:], [#data.closed],
    )
  ],
  block(stroke: 1pt, inset: 6pt, width: 100%)[
    #strong[Briefkopf/Stempel PSA-Sachkundige\*r] \
    #line(length: 100%, stroke: 0.6pt) \
    #if data.stamp.len() == 0 { [#data.expert] } else { data.stamp.join(linebreak()) }
  ],
)

#v(6pt)
#text(size: 11pt, weight: "bold")[Geprüfte Teile (#data.parts.len())]

#for teil in data.parts {
  block(stroke: 0.8pt, inset: 6pt, width: 100%, breakable: false)[
    #strong(if teil.system { "System: " } else { "Produkt: " })#teil.name
    #set text(size: 8pt)
    #table(
      columns: (auto, 1.2fr, 1fr, 1fr, auto, 1fr),
      stroke: 0.4pt + gray,
      inset: 3pt,
      [*Nr.*], [*EI*], [*Hersteller*], [*Produktname*], [*Herst.-Jahr*], [*Seriennummer*],
      ..teil.components.flatten().map(v => [#v]),
    )
    #table(
      columns: (auto, 1fr),
      stroke: none,
      inset: 2pt,
      [*Ergebnis:*], [#teil.outcome],
      [*Kommentar:*], [#teil.comment],
      [*Nächste Prüfung:*], [#teil.next_due],
    )
  ]
}

#v(6pt)
#block(stroke: 0.8pt, inset: 6pt, width: 100%, breakable: false)[
  #data.statement

  #set text(size: 7.5pt)
  #table(
    columns: (auto, 1fr),
    stroke: none,
    inset: 2pt,
    [Inhalt der Runde (SHA-256):], [#raw(data.content_sha256)],
    [Kettenkopf Nummer:], [#data.chain_seq],
    [Kettenkopf Hash (SHA-256):], [#raw(data.chain_hash)],
  )
]
