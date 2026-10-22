# Manipulations-Katalog

Abnahme Phase 3 (Konzept): „Ein Katalog gezielter Manipulationen wird vollständig erkannt (Feld
geändert, Eintrag gelöscht, Datenbank zurückgesetzt).“ Jede Zeile nennt, wer die Manipulation
findet, und den Test, der das festschreibt. Angreifer ist, wer die Datenbank an der App vorbei
ändern kann: Er kann auch Prüfsummen und die ganze Hash-Kette passend neu berechnen.

Werkzeuge:

- **Prüfknopf** „Unverändert?“ an jedem Prüfbericht (#39)
- **`kette_pruefen`** (#15, #19, #20): ganze Kette, festgehaltene Köpfe, Stammdaten der Bauteile
  gegen ihren festgehaltenen Stand, alle Prüfberichte, mit
  `--zeugen` die Zeugen-Mails bzw. Kopf-Dateien, mit `--fingerabdruecke` die Papierliste.
  Ablauf: `docs/runbooks/pruefwerkzeug.md`
- **Hochladen** des signierten Protokolls (#38): lehnt ab, bevor etwas gespeichert wird
- **`signieren pruefen`** (#19): ein heruntergeladenes signiertes PDF auf einem anderen Rechner,
  ohne Datenbank (Signatur, Zeitstempel, Fingerabdruck gegen die Papierliste)

Je Eintrag gibt es in `tests/inspections/test_manipulations.py` einen Test mit der Katalognummer
im Namen (`test_m<Nr>_…`, #20); er läuft über das Werkzeug, das die Manipulation findet, meist
`kette_pruefen` als Ganzes. Dahinter stehen die Einzeltests der jeweiligen Bausteine.

| Nr. | Manipulation | Erkannt durch | Test |
| --- | --- | --- | --- |
| M1 | Feld geändert (z. B. nächste Prüfung eines Prüfergebnisses verlängert) | Kette: „Vorgang wurde geändert“; Bericht: Ergebnisse passen nicht zum Abschluss | `test_m1_changed_field_is_found`, `tests/core/test_chain.py::test_changed_row_is_detected` |
| M2 | Eintrag gelöscht | Kette: „Vorgang wurde gelöscht“ | `test_m2_deleted_entry_is_found`, `test_deleted_row_is_detected` |
| M3 | Eintrag an der App vorbei eingefügt | Kette: „Vorgang ohne Kettenglied“ | `test_m3_inserted_entry_is_found`, `test_inserted_row_is_detected` |
| M4 | Prüfergebnis geändert, dazu Prüfsummen in der Datenbank und die ganze Kette neu berechnet | Bericht: mitsignierte Werte im PDF (Inhalt, Kettenkopf) widersprechen der Datenbank | `test_m4_result_changed_and_everything_recomputed` |
| M5 | Vorgang geändert und die ganze Kette neu berechnet (ohne Prüfbericht dazwischen) | nur Zeugen-Mail/Kopf-Datei (`--zeugen`): „Kettenkopf … weicht ab“ | `test_m5_recomputed_chain_needs_the_witness` |
| M6 | Datenbank auf einen älteren Stand zurückgesetzt (alte Sicherung eingespielt) | nur Zeugen-Mail/Kopf-Datei (`--zeugen`): „Kettenkopf … fehlt: Die Kette wurde gekürzt“ | `test_m6_restored_older_backup_needs_the_witness` |
| M7 | Signierte Protokolle zweier Runden vertauscht (samt Original) | Bericht: „Das signierte Protokoll gehört zu Runde …“ | `test_m7_swapped_reports_are_found` |
| M8 | Nachtrag an ein signiertes PDF angehängt, Prüfsumme angepasst | Kette: „Vorgang wurde geändert“ | `test_m8_unsigned_change_after_signing_is_found` |
| M9 | Signiertes PDF verändert (in der Datenbank oder als Datei) | Bericht: „Das signierte PDF wurde in der Datenbank verändert“; `signieren pruefen`: Signatur passt nicht bzw. Nachtrag nach dem Zeitstempel | `test_m9_changed_report_pdf_is_found`, `tests/inspections/test_reports.py::test_changed_pdf_in_database_is_found`, `tests/inspections/test_audit.py::test_changed_report_is_found`, `tests/signiertool/test_pruefen.py::test_changed_byte_is_found`, `test_later_update_is_found` |
| M10 | Kettenglieder vertauscht oder gelöscht | Kette | `test_m10_deleted_link_is_found`, `test_swapped_links_are_detected`, `test_deleted_link_is_detected` |
| M11 | Letzte Vorgänge samt Gliedern gelöscht (Kette gekürzt) | Kette gegen festgehaltene Köpfe | `test_m11_truncated_chain_is_found_with_a_known_head`, `test_truncation_needs_a_known_head` |
| M12 | Festgehaltener Kopf geändert oder fremder Zeitstempel untergeschoben | Kette bzw. Zeitstempel der Köpfe | `test_m12_changed_witness_head_is_found`, `tests/core/test_witness.py::test_recomputed_chain_misses_its_witness`, `test_swapped_token_is_detected` |
| M13 | Zeugen-Datei gefälscht (anderer Hash oder andere Zeit) | `--zeugen`: Zeitstempel passt nicht zum Hash bzw. zur Zeit | `test_m13_forged_witness_file_is_found`, `tests/inspections/test_audit.py::test_forged_hash_in_witness_file`, `test_wrong_time_in_witness_file` |
| M14 | Zeitpunkt des Zeitstempels eines Berichts geändert | Bericht: „weicht vom Zeitstempel ab“ | `test_m14_changed_stamp_time_is_found`, `test_changed_stamp_time_is_found` |
| M15 | Eigenes Zertifikat in die Zertifikatsliste geschmuggelt | `--fingerabdruecke` (Server und Signier-Tool): „steht nicht auf der Liste“ | `test_m15_certificate_missing_from_paper_list`, `test_certificate_missing_from_list`, `test_signer_missing_from_list` |
| M16 | Kettenkopf im Protokoll neu berechnet | Bericht und Kette: mitsignierter Kopf weicht ab | `test_m16_protocol_head_recomputed_is_found`, `test_recomputed_chain_is_found_by_protocol_head`, `tests/inspections/test_signature.py::test_chain_check_uses_heads_from_signed_protocols` |
| M17 | Protokoll vor dem Hochladen verändert, anders signiert oder mit fremdem Zertifikat | Hochladen lehnt ab | `test_m17_changed_protocol_is_refused_at_upload`, `tests/inspections/test_signature.py` (`test_change_after_signing_is_refused`, `test_change_of_content_with_the_signature_is_refused`, `test_signature_in_other_field_is_refused`, `test_unlisted_certificate_is_refused`, `test_certificate_of_another_inspector_is_refused`, …) |
| M18 | Protokoll zuerst gestempelt, später signiert (spätere Signatur mit früherer Zeit ausgeben) | Bericht und `signieren pruefen`: „Der Zeitstempel liegt vor der Signatur“ | `test_m18_timestamp_before_signature_is_found`, `tests/inspections/test_reports.py::test_timestamp_before_signature_is_found`, `tests/signiertool/test_pruefen.py::test_timestamp_before_signature_is_found` |
| M19 | „Nächste Prüfung“ ohne Prüfung gesetzt (Korrektur mit späterem Datum an der App vorbei eingefügt) | Kette: „Vorgang ohne Kettenglied“; Bericht: Ergebnisse passen nicht zum Abschluss | `test_m19_next_due_slipped_in_without_inspection` |
| M20 | PSA-relevantes Feld in der Datenbank geändert (z. B. Herstellungsjahr „verjüngt“) | Stammdaten: Feld „weicht vom festgehaltenen Stand ab“ | `test_m20_ppe_field_changed_in_database_is_found`, `tests/inventory/test_component_records.py::test_field_changed_in_database_is_found`, `test_component_moved_to_another_part_is_found` |
| M21 | Bauteil an der App vorbei angelegt | Stammdaten: „kein festgehaltener Stand“ | `test_m21_component_inserted_in_database_is_found`, `test_component_inserted_in_database_is_found` |
| M22 | Bauteil und sein festgehaltener Stand gemeinsam geändert | Kette: „Vorgang wurde geändert“ (`ComponentRecord`) | `test_m22_component_and_its_record_changed_together`, `test_record_rewritten_in_database_is_found_by_the_chain` |
| M23 | Manipulation an einem Bauteil durch eine spätere, erlaubte Änderung in der App „verdeckt“ | App lehnt die Änderung ab („passt nicht zum festgehaltenen Stand“), der Befund bleibt; zwischen zwei Ständen ändern sich nur vermerkte Felder | `test_m23_tampering_is_not_laundered_by_a_later_edit`, `test_tampered_component_is_not_laundered_by_a_later_edit`, `test_unlisted_change_between_records_is_found`, `test_history_must_start_with_creation` |

## Grenzen

- **M5 und M6 deckt nur der Zeuge auf.** Wer die Datenbank vollständig neu berechnet oder eine
  ältere Sicherung einspielt, hinterlässt eine in sich stimmige Datenbank. Erst die Köpfe
  außerhalb des Servers zeigen es: Zeugen-Mails beim Vorstand, Kopf-Dateien auf Unraid und die
  mitsignierten Köpfe in den Prüfberichten. Deshalb im Quartal einmal mit `--zeugen` prüfen.
  Die Tests M5 und M6 schreiben diese Grenze fest (ohne `--zeugen`: unverändert).
- **Eine Person für alles.** Solange eine Person Admin, PSA-Sachkundige*r und Betreiber von
  Unraid ist, schützt die Signatur nicht gegen diese Person selbst (Konzept, „Grenzen“).
  Rückdatieren verhindert der Zeitstempel, Löschen zeigt der Vorstand als Zeuge.
- **Altberichte** aus dem Altsystem sind ungesichert; das Prüfwerkzeug nennt nur ihre Zahl.
- **Zertifikatsliste:** Ein untergeschobenes Zertifikat findet nur der Abgleich mit einer Liste,
  die nicht auf dem Server liegt (`--fingerabdruecke`, z. B. vom USB-Stick), oder der Vorstand
  beim Abgleich der Zeugen-Mail mit der Papierliste.
- **Stammdaten:** Festgehalten werden alle Felder der Bauteile und ihre Zuordnung zum Teil
  (`ComponentRecord`, #20), nicht die Bezeichnung des Teils und nicht die GAL-Verweise. Bauteile,
  die vor #20 angelegt wurden, haben keinen festgehaltenen Stand und gelten als „an der App
  vorbei angelegt“; echte Daten gibt es vor dem Import (#29) nicht.
