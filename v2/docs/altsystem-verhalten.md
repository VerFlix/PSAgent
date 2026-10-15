# Altsystem: festgeschriebenes Verhalten

Die Tests in `v2/tests/legacy/behaviour/` halten fest, wie sich `psa_gui.py`, `verleih_gui.py`
und `generate_PDF.py` heute verhalten (Charakterisierungstests, #9). Sie laufen gegen den
unveränderten Code des Altsystems, ohne Bildschirm: tkinter wird durch ein leeres Ersatzmodul
ersetzt (`tests/legacy/reference.py`). Jeder Test bekommt frische, künstliche Datenbanken, so
angelegt, wie beide Programme es beim Start tun.

Angesprochen werden nur die Funktionen ohne Oberfläche. Regeln, die nur in Dialogen oder
Fenstern stehen, sind unten mit Fundstelle aufgeführt; PSAgent 2.0 prüft sie im Server.

`tests/legacy/behaviour/test_overview.py` prüft, dass jede Paritätszeile 1 bis 21 hier steht und
jeder genannte Test existiert.

## Paritätszeile → festschreibende Tests

| Zeile | Thema | Tests |
| --- | --- | --- |
| 1 | Produkt anlegen, EI | `test_insert_product_stores_nine_fields_next_check_gal_and_no_ppe`, `test_database_accepts_duplicate_and_empty_ei`, `test_ei_conflict_ignores_case_and_outer_spaces_only`, `test_ei_conflict_finds_stored_ei_with_outer_spaces`, `test_product_list_marks_locked_items` |
| 2 | System anlegen | `test_system_always_has_three_part_rows`, `test_update_system_adds_missing_part_rows` |
| 3 | Bearbeiten, Schnellwahl | `test_update_product_overwrites_every_field`, `test_quick_pick_adds_months_and_clamps_to_month_end`, `test_relabel_updates_loan_labels` |
| 4 | GAL für mehrere Teile | `test_gal_link_can_be_set_on_several_items` (Datenbankteil; Kopieren der Datei je Teil siehe unten) |
| 5 | PDF-Dokumente | `test_product_pdf_uses_template_inputs_and_ei_as_file_prefix`, `test_system_pdf_uses_system_name_and_three_parts`, `test_pdf_fails_without_typst` |
| 6 | Nächste Prüfungen, Vorgänge | `test_due_list_puts_locked_first_then_by_date_and_empty_last`, `test_due_list_sorts_dates_as_text`, `test_due_list_labels`, `test_item_history_lists_locks_reports_and_failures_newest_first` |
| 7 | Prüfungsrunde: sperren, entsperren, löschen = archivieren | `test_lock_and_unlock_keep_only_the_latest_state`, `test_unlock_records_source`, `test_lock_accepts_empty_comment`, `test_active_loans_block_deletion_check` |
| 8 | Abschluss mit Name, Bestätigung, Unterschrift | `test_report_stores_signer_confirmation_signature_and_items` |
| 9 | Prüfberichte | `test_report_stores_signer_confirmation_signature_and_items`, `test_report_list_newest_first_with_item_count` |
| 10 | Archiv | `test_archive_copies_item_with_history_and_delete_keeps_loans`, `test_archive_of_system_copies_its_parts`, `test_archive_of_missing_item_fails`, `test_ids_are_not_reused_after_delete` |
| 11 | Export | `test_snapshot_of_all_items_with_archive`, `test_snapshot_of_selection_without_archive`, `test_csv_has_one_row_per_record_with_json_data` |
| 12 | Reservieren, ausleihen, Filter | `test_loan_stores_all_checkout_fields`, `test_designation_filter_lists_only_lendable_designations`, `test_database_accepts_overlapping_loans` |
| 13 | Verfügbarkeit | `test_availability_needs_current_ppe_or_no_ppe`, `test_return_after_next_check_is_not_checked_by_database`, `test_german_dates_compare_as_text`, `test_locked_item_is_not_available`, `test_overlap_includes_both_end_days`, `test_only_reserved_and_lent_block`, `test_overdue_loan_blocks_only_its_own_period`, `test_database_accepts_overlapping_loans`, `test_systems_follow_the_same_rules` |
| 14 | Ausleihe erfassen | `test_loan_stores_all_checkout_fields`, `test_database_accepts_checkout_without_confirmations` |
| 15 | Reservierung umwandeln | `test_reservation_is_converted_once` |
| 16 | Kontaktdaten nachtragen | `test_contact_can_be_added_while_active` |
| 17 | Rückgabe | `test_return_only_from_lent`, `test_lock_on_return_overwrites_previous_lock` |
| 18 | Anstehende Ausleihen | `test_pending_list_hides_past_reservations_but_keeps_overdue_loans`, `test_overdue_loan_blocks_only_its_own_period` |
| 19 | Detailansicht | `test_loan_stores_all_checkout_fields`, `test_return_only_from_lent` |
| 20 | Suche und Kalender | `test_search_finds_products_by_designation_name_or_serial`, `test_search_finds_systems_by_name_only`, `test_search_applies_availability_rules`, `test_calendar_lists_active_loans_touching_the_range` |
| 21 | GAL öffnen | nicht festgeschrieben: `open_reference` öffnet Datei oder Browser des Rechners (`os.startfile`, nur Windows) |
| 22 | Notbetrieb | entfällt: im Altsystem nur Papier laut README, kein Code |

Dazu `test_startup_fills_empty_status_by_signature` (Start des Verleih-Programms).

## Auffälligkeiten, die PSAgent 2.0 bewusst anders macht

| Altsystem | Test | PSAgent 2.0 |
| --- | --- | --- |
| Die Datenbank nimmt doppelte und leere EI an; nur die Oberfläche prüft. | `test_database_accepts_duplicate_and_empty_ei` | Unique-Index in der Datenbank |
| Die EI-Prüfung vergleicht mit `LOWER(TRIM(...))`: `TST 0902` und `TST0902` gelten als verschieden. | `test_ei_conflict_ignores_case_and_outer_spaces_only` | alle Leerzeichen und Groß-/Kleinschreibung ignoriert |
| „Nächste Prüfung“ ist ein frei bearbeitbares Feld; Bearbeiten überschreibt alle Felder, fehlende werden leer. | `test_update_product_overwrites_every_field` | nur über eine Prüfung änderbar |
| Datum wird als Text verglichen: `TT.MM.JJJJ` gilt ab Tag 21 immer, bis Tag 20 nie als aktuell, und sortiert falsch. | `test_german_dates_compare_as_text`, `test_due_list_sorts_dates_as_text` | echte Datumsfelder; der Import wandelt um |
| `DATE('now')` rechnet in UTC; kurz nach Mitternacht gilt noch der Vortag. | `test_today_is_the_utc_date` | deutsche Zeit |
| Ein leerer Status blockiert nicht (`COALESCE` greift nur bei NULL); beim Start setzt das Verleih-Programm ihn auf „lent“ (mit Unterschrift) oder „reserved“. | `test_only_reserved_and_lent_block`, `test_startup_fills_empty_status_by_signature` | Status Pflicht, Wechsel nur vorwärts |
| Überschneidende Ausleihen verhindert nur die Prüfung vor dem Speichern. | `test_database_accepts_overlapping_loans` | Ausschlussregel in der Datenbank |
| Ein überfälliges, nicht zurückgegebenes Teil gilt nach seinem Zeitraum wieder als frei. | `test_overdue_loan_blocks_only_its_own_period` | bleibt gesperrt, bis es zurück ist |
| Eine neue Sperre überschreibt die vorige samt Entsperrung; es gibt nur den letzten Stand. | `test_lock_and_unlock_keep_only_the_latest_state`, `test_lock_on_return_overwrites_previous_lock` | Vorgänge werden nur angehängt |
| Leere EI erscheint in Listen als `EI:  \|` statt `EI: -`, weil sie `''` und nicht NULL ist. | `test_due_list_labels` | – |
| Archivieren kopiert in eine zweite Datei und löscht Teil, Bauteile und Sperre; Ausleihen und Berichte bleiben in der Hauptdatenbank. | `test_archive_copies_item_with_history_and_delete_keeps_loans` | Status „ausgesondert“, nichts wird gelöscht |
| Neue Teile sind sofort verleihbar, wenn ein Prüfdatum eingetragen ist. | `test_availability_needs_current_ppe_or_no_ppe` | erst nach der ersten PSA-Prüfung |

## Regeln nur in der Oberfläche (nicht festgeschrieben)

| Regel | Fundstelle | In PSAgent 2.0 |
| --- | --- | --- |
| EI ist bei Produkten Pflicht und eindeutig | `psa_gui.py`, `save_product` | Datenbank und Dienst (#6) |
| System braucht mindestens eine EI; jede EI eindeutig | `psa_gui.py`, `save_system` | EI je Bauteil Pflicht (#6) |
| „Nächste Prüfung“ im Format `JJJJ-MM-TT` | `psa_gui.py`, `save_product`, `save_system`, `ItemPruefungDialog._save` | Datumsfeld |
| Sperren und Entsperren nicht gleichzeitig; beide mit Pflichtkommentar | `psa_gui.py`, `ItemPruefungDialog._save` | Dienst und Datenbank (#6, #8) |
| Löschen nur ohne aktive Reservierung/Ausleihe, mit Rückfrage | `psa_gui.py`, `_open_due_item` | Aussondern mit Pflichtkommentar (#6) |
| Prüfungsrunde sammeln und auf einmal anwenden | `psa_gui.py`, `commit_pending_pruefungen` | eine Transaktion beim Signieren (#8) |
| GAL-Datei je Teil in `GAL/` kopieren | `psa_gui.py`, `_copy_gal_file`, `apply_gal_to_selection` | Datei einmal gespeichert (#6) |
| Rückgabe spätestens am Tag der nächsten Prüfung | `verleih_gui.py`, `save_verleih`, `checkout_selected_reservation` | Dienst (#8) |
| Ausgabe: Kurzkontrolle, GAL bereitgestellt, Unterschrift | `verleih_gui.py`, `CheckoutDialog.save` | Dienst (#7) |
| Ausgabe: Name, Adresse, E-Mail oder Telefon | `verleih_gui.py`, `_validate_contact_for_lending` | Dienst (#7) |
| Hinweis „überfällig“, „heute fällig“ | `verleih_gui.py`, `_refresh_plan_list` | abgeleitet am Ausleihe-Datensatz (#7) |
| GAL öffnen | `verleih_gui.py`, `open_reference` | im Browser (#51) |
