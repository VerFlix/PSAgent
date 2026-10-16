// Teileliste (Parität 11). Alle Werte kommen als JSON und werden nur als Text gesetzt.
#let data = json(bytes(sys.inputs.data))
#set document(title: data.title)
#set page(paper: "a4", flipped: true, margin: (x: 1cm, y: 1.2cm), footer: context [
  #set text(size: 7pt)
  PSAgent · #data.title · Stand #data.stand #h(1fr) Seite #counter(page).display() von #counter(page).final().first()
])
#set text(lang: "de", size: 6.5pt)
#text(size: 12pt, weight: "bold")[#data.title]
#v(0.3em)
#table(
  columns: data.columns.len(),
  stroke: 0.4pt + gray,
  inset: 3pt,
  table.header(..data.columns.map(c => text(weight: "bold")[#c])),
  ..data.rows.flatten().map(v => [#v]),
)
