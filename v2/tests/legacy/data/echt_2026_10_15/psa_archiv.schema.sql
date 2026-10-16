CREATE TABLE products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                produktbezeichnung TEXT,
                gem_en TEXT,
                produktname TEXT,
                hersteller TEXT,
                herstellungsjahr TEXT,
                kaufdatum TEXT,
                datum_einsatz TEXT,
                einzelidentifikation TEXT,
                seriennummer TEXT,
                gal_datei TEXT,
                gal_link TEXT
            , naechste_pruefung_am TEXT, last_psa_pruefung_am TEXT, last_psa_pruefung_kommentar TEXT, last_psa_durchgefallen_am TEXT, keine_psa INTEGER NOT NULL DEFAULT 0);
CREATE TABLE sqlite_sequence(name,seq);
CREATE TABLE systems (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                gal_datei TEXT,
                gal_link TEXT
            , naechste_pruefung_am TEXT, last_psa_pruefung_am TEXT, last_psa_pruefung_kommentar TEXT, last_psa_durchgefallen_am TEXT, keine_psa INTEGER NOT NULL DEFAULT 0);
CREATE TABLE system_parts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                system_id INTEGER NOT NULL,
                part_index INTEGER NOT NULL,
                produktbezeichnung TEXT,
                gem_en TEXT,
                produktname TEXT,
                hersteller TEXT,
                herstellungsjahr TEXT,
                kaufdatum TEXT,
                datum_einsatz TEXT,
                einzelidentifikation TEXT,
                seriennummer TEXT,
                FOREIGN KEY(system_id) REFERENCES systems(id)
            );
CREATE TABLE item_locks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_type TEXT NOT NULL,
                item_id INTEGER NOT NULL,
                is_locked INTEGER NOT NULL DEFAULT 1,
                lock_comment TEXT,
                unlock_comment TEXT,
                lock_at TEXT,
                unlock_at TEXT,
                unlock_source TEXT,
                UNIQUE(item_type, item_id)
            );
CREATE TABLE verleih_planung (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_type TEXT NOT NULL,
                item_id INTEGER NOT NULL,
                item_label TEXT,
                von_datum TEXT NOT NULL,
                rueckgabe_datum TEXT NOT NULL,
                entleiher TEXT,
                entleiher_email TEXT,
                entleiher_telefon TEXT,
                entleiher_adresse TEXT,
                signature_data TEXT,
                quick_check_out INTEGER NOT NULL DEFAULT 0,
                gal_provided_out INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'lent',
                checkout_at TEXT,
                return_comment TEXT,
                return_signature_data TEXT,
                quick_check_return INTEGER NOT NULL DEFAULT 0,
                returned_at TEXT,
                created_at TEXT NOT NULL
            , ausgebende_person TEXT, ruecknehmende_person TEXT);
CREATE TABLE psa_pruefungsberichte (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                sachkundiger_name TEXT NOT NULL,
                sachkundig_bestaetigt INTEGER NOT NULL DEFAULT 0,
                signature_data TEXT NOT NULL
            );
CREATE TABLE psa_pruefungsbericht_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_id INTEGER NOT NULL,
                item_type TEXT NOT NULL,
                item_id INTEGER,
                item_label TEXT NOT NULL,
                psa_pruefung_durchgefuehrt INTEGER NOT NULL DEFAULT 0,
                kommentar TEXT,
                naechste_pruefung_am TEXT,
                aktion TEXT,
                FOREIGN KEY(report_id) REFERENCES psa_pruefungsberichte(id)
            );
CREATE TABLE archived_items (
                archived_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_type TEXT NOT NULL,
                source_item_id INTEGER NOT NULL,
                item_label TEXT NOT NULL,
                archived_at TEXT NOT NULL,
                archived_by TEXT,
                kommentar TEXT
            );
