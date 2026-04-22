#let t1_Produktbezeichnung = if "t1_Produktbezeichnung" in sys.inputs { sys.inputs.t1_Produktbezeichnung } else { "" }
#let t1_gem_EN = if "t1_gem_EN" in sys.inputs { sys.inputs.t1_gem_EN } else { "" }
#let t1_Produktname = if "t1_Produktname" in sys.inputs { sys.inputs.t1_Produktname } else { "" }
#let t1_Hersteller = if "t1_Hersteller" in sys.inputs { sys.inputs.t1_Hersteller } else { "" }
#let t1_Herstellungsjahr = if "t1_Herstellungsjahr" in sys.inputs { sys.inputs.t1_Herstellungsjahr } else { "" }
#let t1_Kaufdatum = if "t1_Kaufdatum" in sys.inputs { sys.inputs.t1_Kaufdatum } else { "" }
#let t1_Datum_Einsatz = if "t1_Datum_Einsatz" in sys.inputs { sys.inputs.t1_Datum_Einsatz } else { "" }
#let t1_Einzelidentifikation = if "t1_Einzelidentifikation" in sys.inputs { sys.inputs.t1_Einzelidentifikation } else { "" }
#let t1_Seriennummer = if "t1_Seriennummer" in sys.inputs { sys.inputs.t1_Seriennummer } else { "" }

#let t2_Produktbezeichnung = if "t2_Produktbezeichnung" in sys.inputs { sys.inputs.t2_Produktbezeichnung } else { "--" }
#let t2_gem_EN = if "t2_gem_EN" in sys.inputs { sys.inputs.t2_gem_EN } else { "--" }
#let t2_Produktname = if "t2_Produktname" in sys.inputs { sys.inputs.t2_Produktname } else { "--" }
#let t2_Hersteller = if "t2_Hersteller" in sys.inputs { sys.inputs.t2_Hersteller } else { "--" }
#let t2_Herstellungsjahr = if "t2_Herstellungsjahr" in sys.inputs { sys.inputs.t2_Herstellungsjahr } else { "--" }
#let t2_Kaufdatum = if "t2_Kaufdatum" in sys.inputs { sys.inputs.t2_Kaufdatum } else { "--" }
#let t2_Datum_Einsatz = if "t2_Datum_Einsatz" in sys.inputs { sys.inputs.t2_Datum_Einsatz } else { "--" }
#let t2_Einzelidentifikation = if "t2_Einzelidentifikation" in sys.inputs { sys.inputs.t2_Einzelidentifikation } else { "--" }
#let t2_Seriennummer = if "t2_Seriennummer" in sys.inputs { sys.inputs.t2_Seriennummer } else { "--" }

#let t3_Produktbezeichnung = if "t3_Produktbezeichnung" in sys.inputs { sys.inputs.t3_Produktbezeichnung } else { "--" }
#let t3_gem_EN = if "t3_gem_EN" in sys.inputs { sys.inputs.t3_gem_EN } else { "--" }
#let t3_Produktname = if "t3_Produktname" in sys.inputs { sys.inputs.t3_Produktname } else { "--" }
#let t3_Hersteller = if "t3_Hersteller" in sys.inputs { sys.inputs.t3_Hersteller } else { "--" }
#let t3_Herstellungsjahr = if "t3_Herstellungsjahr" in sys.inputs { sys.inputs.t3_Herstellungsjahr } else { "--" }
#let t3_Kaufdatum = if "t3_Kaufdatum" in sys.inputs { sys.inputs.t3_Kaufdatum } else { "--" }
#let t3_Datum_Einsatz = if "t3_Datum_Einsatz" in sys.inputs { sys.inputs.t3_Datum_Einsatz } else { "--" }
#let t3_Einzelidentifikation = if "t3_Einzelidentifikation" in sys.inputs { sys.inputs.t3_Einzelidentifikation } else { "--" }
#let t3_Seriennummer = if "t3_Seriennummer" in sys.inputs { sys.inputs.t3_Seriennummer } else { "--" }

#let systemname = if "systemname" in sys.inputs { sys.inputs.systemname } else { "" }

// t1_Produktbezeichnung
// t1_gem_EN
// t1_Produktname
// t1_Hersteller
// t1_Herstellungsjahr
// t1_Kaufdatum
// t1_Datum_Einsatz
// t1_Einzelidentifikation
// t1_Seriennummer

#set page(paper: "a4")

#let line(width: 100%) = box(height: 0.6pt, width: width, stroke: 0.6pt)

#set page(margin: 1.5cm)
#set text(12pt)
= Einsatzdokumentation


#grid(
  columns: (1.5fr, 0.5fr),
  gutter: 1pt,
  [
    #box(stroke: 0.8pt, inset: 6pt)[
      #set text(9pt)
  Diese Einsatzdokumentation ist dem beschriebenen Produkt zugeordnet und sollte bei der nächsten PSA-Überprüfung zusammen mit dem Produkt eingereicht werden (Einsatzhistorie).
]
    #box(stroke: 0.8pt, inset: 6pt)[
      #set text(9pt)
  Mit seiner Unterschrift bestätigt die Zurückgebende Person für den angegebenen Ausleih-Zeitraum dir Richtigkeit der Angaben zu "Besonderen Vorkommnissen" und "Kontakt mit Chemikalien".
]
    // Platz für Briefkopf / Stempel
  ], [
    // Briefkopf / Stempel PSA-Sachkundiger
    #box(stroke: 1pt, inset: 6pt)[
    *Briefkopf/Stempel Besitzer*in* \
  #line()\
  DAV Lübeck
  ]
  ],
)

#box(stroke: 0.8pt, inset: 6pt)[
  #grid(
  columns: (1fr, 1fr),
  gutter: 6pt,
  [
    *Für Einzelteile*
    #set text(9pt)


    #grid(
    columns: (1fr, 1fr),
    gutter: -70pt,
  [
    _Teil 1_ \
    #v(3pt)
    Produktbezeichnung: \
    gem. EN: \
    Produktname:\
    Hersteller: \
    Herstellungsjahr:\
    Kaufdatum: \
    Datum 1. Einsatz:\
    Einzelidentifikation:\
    Seriennummer: \
  ],[
    // t1_Produktbezeichnung
    // t1_gem_EN
    // t1_Produktname
    // t1_Hersteller
    // t1_Herstellungsjahr
    // t1_Kaufdatum
    // t1_Datum_Einsatz
    // t1_Einzelidentifikation
    // t1_Seriennummer
     \
     #v(3pt)
      #t1_Produktbezeichnung \
      #t1_gem_EN \
      #t1_Produktname \
      #t1_Hersteller \
      #t1_Herstellungsjahr \
      #t1_Kaufdatum \
      #t1_Datum_Einsatz \
      #t1_Einzelidentifikation \
      #t1_Seriennummer
  ]
)
  ], [
    *Für Systeme*~~~~~~~~~~~~~~~~~~~#systemname

    #grid(
      columns: (1fr, 1fr),
      gutter: 6pt,
       [
         #set text(9pt)
        _Teil 2_ \
        #v(3pt)
        #t2_Produktbezeichnung \
        #t2_gem_EN \
        #t2_Produktname \
        #t2_Hersteller \
        #t2_Herstellungsjahr \
        #t2_Kaufdatum \
        #t2_Datum_Einsatz \
        #t2_Einzelidentifikation \
        #t2_Seriennummer
      ], [
        #set text(9pt)
        _Teil 3_ \
        #v(3pt)
        #t3_Produktbezeichnung \
        #t3_gem_EN \
        #t3_Produktname \
        #t3_Hersteller \
        #t3_Herstellungsjahr \
        #t3_Kaufdatum \
        #t3_Datum_Einsatz \
        #t3_Einzelidentifikation \
        #t3_Seriennummer
      ],
    )
  ],
)
]

#set text(8pt)

#let rows = 17

#let row-block() = [[
      asd// Datum Ausgabe
    ],
    [
      asd// Datum Rücknahme
    ],
    [
      asd// Kurzprüfung
    ],
    [
      ads// Beobachtungen / Kommentar
    ],
    [
      asd// Unterschrift Produktverwaltung
    ],
    [
      asd// Besondere Vorkommnisse
    ],
    [
      asd// Unterschrift Zurückgebender
    ]]

#table(
  columns: (0.5fr, 0.6fr, 0.6fr, 1fr, 0.6fr, 1fr, 0.7fr),
  align: left,
  stroke: 0.5pt,
  inset: (x: 4pt, y: 2pt),
  column-gutter: (0pt,0pt,0pt,0pt,6pt,0pt),
  rows: (auto,) + (25pt,) * rows,
  // Kopfzeile
  [
    *Datum Ausgabe*
  ], [
    *Datum Rücknahme*
  ], [
    *Kurzprüfung durchgeführt*
  ], [
    *Beobachtungen / Kommentar*
  ], [
    *Unterschrift AG Mitglied*
  ], [
    *Besondere Vorkommnisse (harter Sturz, Kontakt mit Chemikalien, ...)*
  ], [
    *Unterschrift des Zurückgebende*r*
  ],


  // Leerzeilen als Vorlage
    [
       ~// Datum Ausgabe
    ],
    [
      // Datum Rücknahme
    ],
    [
      // Kurzprüfung
    ],
    [
      // Beobachtungen / Kommentar
    ],
    [
      // Unterschrift Produktverwaltung
    ],
    [
      // Besondere Vorkommnisse
    ],
    [
      // Unterschrift Zurückgebender
    ],
)

#let rows = 28

#table(
  columns: (0.5fr, 0.6fr, 0.6fr, 1fr, 0.6fr, 1fr, 0.7fr),
  align: left,
  stroke: 0.5pt,
  inset: (x: 4pt, y: 2pt),
  column-gutter: (0pt,0pt,0pt,0pt,6pt,0pt),
  rows: (auto,) + (25pt,) * rows,
  // Kopfzeile
  [
    *Datum Ausgabe*
  ], [
    *Datum Rücknahme*
  ], [
    *Kurzprüfung durchgeführt*
  ], [
    *Beobachtungen / Kommentar*
  ], [
    *Unterschrift AG Mitglied*
  ], [
    *Besondere Vorkommnisse (harter Sturz, Kontakt mit Chemikalien, ...)*
  ], [
    *Unterschrift des Zurückgebende*r*
  ],


  // Leerzeilen als Vorlage
    [
       ~// Datum Ausgabe
    ],
    [
      // Datum Rücknahme
    ],
    [
      // Kurzprüfung
    ],
    [
      // Beobachtungen / Kommentar
    ],
    [
      // Unterschrift Produktverwaltung
    ],
    [
      // Besondere Vorkommnisse
    ],
    [
      // Unterschrift Zurückgebender
    ],
)