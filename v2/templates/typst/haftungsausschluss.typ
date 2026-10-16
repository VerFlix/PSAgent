// Haftungsausschluss für ausgesonderte Produkte (Parität 5): Name und Stempel aus dem Konto.
#import "formular.typ": *
#show: seite.with("Haftungsausschluss")

#ueberschrift[Haftungsausschluss für ausgesonderte Produkte seitens des PSA-Sachkundigen]
#v(12pt)

#grid(
  columns: (1.5fr, 0.5fr),
  gutter: 6pt,
  [],
  stempel("Briefkopf/Stempel PSA-Sachkundige*r", data.stamp),
)

#set text(size: 12pt)
*Die sachkundige Person:* #data.expert

*überlässt:*
#set text(size: 9pt)
#bauteile()
#v(8pt)

#set text(size: 12pt)
#grid(
  columns: (auto, 1fr),
  gutter: 6pt,
  [*an:*],
  [#v(12pt) #linie() #text(size: 8pt)[Name Empfänger\*in]],
)

#block(stroke: 1pt, inset: 6pt, width: 100%)[
  Das Produkt ist, nach der Beurteilung durch den oben genannten Sachkundigen für Persönliche
  Schutzausrüstung gegen Absturz, dem Verkehr zu entziehen.
]
#block(stroke: 1pt, inset: 6pt, width: 100%)[
  Das Produkt weist nicht die Beschaffenheit auf, die bei Objekten der gleichen Art üblich ist,
  und entspricht nicht der PSA-Verordnung (Persönliche Schutzausrüstung). Die empfangende Person
  hat hiervon Kenntnis genommen. Soweit die Nutzung der Ware durch den Empfänger erfolgt,
  geschieht dies auf eigene Gefahr und eigenes Risiko. Der oben genannte Sachkundige für
  Persönliche Schutzausrüstung gegen Absturz wird von jeglicher Haftung aus der Überlassung der
  Nicht-PSA entbunden.
]

#v(60pt)
#grid(
  columns: (1fr, 1fr),
  gutter: 6pt,
  [*ggf. Stempel, Datum, Unterschrift*],
  [#v(12pt) #linie()],
)
#v(40pt)
#grid(
  columns: (1fr, 1fr),
  gutter: 6pt,
  [*Unterschrift Empfänger\*in*],
  [#v(12pt) #linie()],
)
