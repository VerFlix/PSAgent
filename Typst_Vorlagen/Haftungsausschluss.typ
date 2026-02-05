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
#set text(10pt)
= Haftungsausschluss für ausgesonderte Produkte seitens des PSA-Sachkundigen

#v(20pt)
#grid(
  columns: (1.5fr, 0.5fr),
  gutter: 1pt,
  [
    // Platz für Briefkopf / Stempel
  ], [
    // Briefkopf / Stempel PSA-Sachkundige*r
    #box(stroke: 1pt, inset: 6pt)[
    *Briefkopf/Stempel PSA-Sachkundige*r* \
  #line()\
  Felix Gottschalk \
  i.A. von BoulderING
  ]
  ],
)
#set text(12pt)
#grid(
  columns: (1fr, 1.9fr),
  gutter: 6pt,
  [
    *Die sachkundige Person:*  \
    *überlässt:*
  ], [
    Felix Gottschalk
])


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

#v(8pt)

#set text(12pt)
#grid(
  columns: (1fr, 1.9fr,1fr),
  gutter: 6pt,
  [
    *an:*  \
  ], [
    #v(12pt)
    #line()
],
[
  #set text(8pt)
  name Empfänger*in
])

#box(stroke: 1pt, inset: 6pt)[
  Das Produkt ist, nach der Beurteilung durch den oben genannten Sachkundigen für Persönliche Schutzausrüstung gegen Absturz, dem Verkehr zu entziehen.
  ]

#box(stroke: 1pt, inset: 6pt)[
  Das Produkt weist nicht die Beschaffenheit auf, die bei Objekten der gleichen Art üblich ist, und entspricht nicht der PSA-Verordnung (Persönliche Schutzausrüstung). Die empfangende Person hat hiervon Kentnis genommen. Soweit die Nutzung der Ware durch den Empfänger erfolgt, geschieht dies auf eigene Gefahr und eigenes Risiko. Der oben genannte Sachkundige für Persönliche Schutzausrüstung gegen Absturz wird von jeglicher Haftung aus der Überlassung der Nicht-PSA entbunden.
  ]

#v(100pt)
  #grid(
  columns: (1.8fr, 1.9fr),
  gutter: 6pt,
  [
    *ggf. Stempel, Datum, Unterschrift*  \
  ], [
    #v(12pt)
    #line()
],
)
#v(50pt)
  #grid(
  columns: (1.8fr, 1.9fr),
  gutter: 6pt,
  [
    *Unterschrift Empfänger*in*  \
  ], [
    #v(12pt)
    #line()
],
)