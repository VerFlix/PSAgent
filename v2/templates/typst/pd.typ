// PSA-Produktdatenblatt (Parität 5): leere Prüfblöcke, Stempel des Sachkundigen aus dem Konto.
#import "formular.typ": *
#show: seite.with("PSA-Produktdatenblatt (PD)")

#ueberschrift[PSA-Produktdatenblatt (PD) – Sachkundigenprüfung]

#grid(
  columns: (1.5fr, 0.5fr),
  gutter: 6pt,
  hinweis[
    *Dieses Produktdatenblatt ist dem beschriebenen Produkt zugeordnet und ist bei der nächsten
    PSA-Überprüfung zusammen mit dem Produkt einzureichen (Historie der Überprüfung). Das
    Produktdatenblatt (PD) und die Gebrauchsanleitung (GAL) sind so aufzubewahren, dass sie
    eindeutig dem Produkt zuzuordnen sind.* \
    Der PSA-Sachkundige übernimmt keine Haftung für personelle oder materielle Folgeschäden
    aufgrund von nicht sichtbaren Schäden an textilen Elementen durch Kontakt mit Chemikalien,
    Säuren und Basen oder anderen schädlichen Substanzen.
  ],
  stempel("Briefkopf/Stempel PSA-Sachkundige*r", data.stamp),
)

#bauteile()

#text(size: 12pt, weight: "bold")[Überprüfungsnachweis]

#let pruefblock = block(stroke: 0.8pt, inset: 8pt, width: 100%, breakable: false)[
  *Überprüfung*
  #grid(
    columns: (0.3fr, 1fr, 1fr, 0.4fr),
    gutter: 6pt,
    [
      Datum: \ #v(8pt) #linie() \
      Nächste Prüfung: \ #v(8pt) #linie()
    ],
    [
      Grund der Überprüfung: \ #linie()
      \ #v(8pt) #linie()
      \ #v(8pt) #linie()
      \ #v(8pt) #linie()
    ],
    [
      #kaestchen Einwandfrei und einsatzfähig \
      #kaestchen Kommentare, Reparaturen, Beobachtungen:
      #v(8pt) #linie()
      #v(8pt) #linie()
      #kaestchen Ausgesondert, nicht freigegeben; Grund:
      \ #v(8pt) #linie()
    ],
    [Unterschrift Sachkundige\*r:],
  )
]

#for _ in range(8) { pruefblock }
