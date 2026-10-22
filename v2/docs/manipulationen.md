# Manipulations-Katalog

Abnahme Phase 3 (Konzept): „Ein Katalog gezielter Manipulationen wird vollständig erkannt (Feld
geändert, Eintrag gelöscht, Datenbank zurückgesetzt).“ Jede Zeile nennt, wer die Manipulation
findet, und den Test, der das festschreibt. Angreifer ist, wer die Datenbank an der App vorbei
ändern kann: Er kann auch Prüfsummen und die ganze Hash-Kette passend neu berechnen.

Werkzeuge:

- **Prüfknopf** „Unverändert?“ an jedem Prüfbericht (#39)
- **`kette_pruefen`** (#15, #19): ganze Kette, festgehaltene Köpfe, alle Prüfberichte, mit
  `--zeugen` die Zeugen-Mails bzw. Kopf-Dateien, mit `--fingerabdruecke` die Papierliste.
  Ablauf: `docs/runbooks/pruefwerkzeug.md`
- **Hochladen** des signierten Protokolls (#38): lehnt ab, bevor etwas gespeichert wird
- **`signieren pruefen`** (#19): ein heruntergeladenes signiertes PDF auf einem anderen Rechner,
  ohne Datenbank (Signatur, Zeitstempel, Fingerabdruck gegen die Papierliste)

Tests in `tests/inspections/test_manipulations.py` (M1–M8) laufen über `kette_pruefen` als Ganzes.

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
| M9 | Signiertes PDF verändert (in der Datenbank oder als Datei) | Bericht: „Das signierte PDF wurde in der Datenbank verändert“; `signieren pruefen`: Signatur passt nicht bzw. Nachtrag nach dem Zeitstempel | `tests/inspections/test_reports.py::test_changed_pdf_in_database_is_found`, `tests/inspections/test_audit.py::test_changed_report_is_found`, `tests/signiertool/test_pruefen.py::test_changed_byte_is_found`, `test_later_update_is_found` |
| M10 | Kettenglieder vertauscht oder gelöscht | Kette | `test_swapped_links_are_detected`, `test_deleted_link_is_detected` |
| M11 | Letzte Vorgänge samt Gliedern gelöscht (Kette gekürzt) | Kette gegen festgehaltene Köpfe | `test_truncation_needs_a_known_head` |
| M12 | Festgehaltener Kopf geändert oder fremder Zeitstempel untergeschoben | Kette bzw. Zeitstempel der Köpfe | `tests/core/test_witness.py::test_recomputed_chain_misses_its_witness`, `test_swapped_token_is_detected` |
| M13 | Zeugen-Datei gefälscht (anderer Hash oder andere Zeit) | `--zeugen`: Zeitstempel passt nicht zum Hash bzw. zur Zeit | `tests/inspections/test_audit.py::test_forged_hash_in_witness_file`, `test_wrong_time_in_witness_file` |
| M14 | Zeitpunkt des Zeitstempels eines Berichts geändert | Bericht: „weicht vom Zeitstempel ab“ | `test_changed_stamp_time_is_found` |
| M15 | Eigenes Zertifikat in die Zertifikatsliste geschmuggelt | `--fingerabdruecke` (Server und Signier-Tool): „steht nicht auf der Liste“ | `test_certificate_missing_from_list`, `test_signer_missing_from_list` |
| M16 | Kettenkopf im Protokoll neu berechnet | Bericht und Kette: mitsignierter Kopf weicht ab | `test_recomputed_chain_is_found_by_protocol_head`, `tests/inspections/test_signature.py::test_chain_check_uses_heads_from_signed_protocols` |
| M17 | Protokoll vor dem Hochladen verändert, anders signiert oder mit fremdem Zertifikat | Hochladen lehnt ab | `tests/inspections/test_signature.py` (`test_change_after_signing_is_refused`, `test_change_of_content_with_the_signature_is_refused`, `test_signature_in_other_field_is_refused`, `test_unlisted_certificate_is_refused`, `test_certificate_of_another_inspector_is_refused`, …) |
| M18 | Protokoll zuerst gestempelt, später signiert (spätere Signatur mit früherer Zeit ausgeben) | Bericht und `signieren pruefen`: „Der Zeitstempel liegt vor der Signatur“ | `tests/inspections/test_reports.py::test_timestamp_before_signature_is_found`, `tests/signiertool/test_pruefen.py::test_timestamp_before_signature_is_found` |

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
