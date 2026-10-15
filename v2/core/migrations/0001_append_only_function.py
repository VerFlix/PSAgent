"""Trigger-Funktion für Tabellen, die nur angehängt werden (siehe core/append_only.py)."""

from django.db import migrations


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.RunSQL(
            sql="""
                CREATE FUNCTION psagent_append_only() RETURNS trigger
                LANGUAGE plpgsql AS $$
                BEGIN
                    RAISE EXCEPTION 'Tabelle % ist nur zum Anhängen: % ist nicht erlaubt.',
                        TG_TABLE_NAME, TG_OP
                        USING ERRCODE = 'restrict_violation';
                END;
                $$;
            """,
            reverse_sql="DROP FUNCTION psagent_append_only();",
        ),
    ]
