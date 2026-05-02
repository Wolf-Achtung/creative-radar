# Phase 4 — Lessons-Learned (Work-in-Progress)

**Status:** Phase 4 läuft noch (W4 in Bearbeitung). Diese Datei sammelt
Lessons-Learned, sobald sie aufkommen, damit der Phase-Abschluss-Bericht in
Task 4.5b nicht aus dem Gedächtnis rekonstruiert werden muss.

---

## ORM + Schema-Trennung (W4 / F0.2)

### Lesson 1: `create_all()` im on_startup-Hook ist mit Schema-Trennung inkompatibel

**Symptom:** Production-Crash-Loop nach PR #35-Merge.
`SQLModel.metadata.create_all(engine)` versuchte beim Boot alle Tabellen im
`creative_radar`-Schema anzulegen. Schema existierte noch nicht
(Migrations-Endpoint nicht curlt) → DDL-Fehler → Container-Restart-Loop.

Selbst nach `CREATE SCHEMA IF NOT EXISTS` hätte ein zweites Folge-Problem
gedroht: `create_all` legt leere Tabellen mit den ORM-Spalten-Definitionen
an. Die spätere `ALTER TABLE public.<t> SET SCHEMA creative_radar`-Migration
kollidiert mit den leeren Tabellen → Bestandsdaten bleiben unsichtbar in
`public`.

**Lesson:** mit Schema-Trennung UND aktivem Alembic ist der `create_all`-
Boot-Hook redundant und schädlich. **Alembic + Migrations-Skripte sind die
alleinige DDL-Quelle.** SQLite-Tests laufen weiter mit `create_all`, weil
SQLite Schemas anders behandelt und keine Migration kennt.

Implementiert in `backend/app/database.py:create_db_and_tables` —
`metadata.create_all(engine)` läuft nur bei SQLite.

### Lesson 2: FK-Strings müssen vollqualifiziert sein, wenn das Modell ein non-default-Schema nutzt

**Symptom:** `sqlalchemy.exc.NoReferencedTableError: Foreign key associated
with column 'titlekeyword.title_id' could not find table 'title' with which
to generate a foreign key`.

`SQLModel` registriert Tabellen mit Schema-Klausel als
`"creative_radar.title"` im Metadata-Registry. Ein FK-String `"title.id"`
sucht den Registry-Key `"title"` — findet nichts → Resolution-Fehler beim
ersten Use.

**Lesson:** wenn `__table_args__ = {"schema": "creative_radar"}` aktiv ist,
müssen alle FKs den vollqualifizierten Namen verwenden:
`"creative_radar.title.id"`. Lösung: `_fk(target)`-Helper im selben Modul,
der das Schema-Prefix nur dann hinzufügt, wenn `_resolve_table_schema()`
ein Schema liefert (Postgres-Mode). SQLite-Tests bleiben grün, weil dort
das Helper-Output `"title.id"` ohne Prefix bleibt.

Implementiert in `backend/app/models/entities.py:_fk`. Fünf FK-Stellen
gepatcht: `TitleKeyword.title_id`, `Post.channel_id`, `Asset.title_id`,
`Asset.post_id`, `TitleCandidate.asset_id`.

### Lesson 3: SQLite-Tests können Postgres-spezifische Schema-Probleme nicht abdecken

**Symptom:** CI war 128/128 grün, Production crashte beim Boot.

SQLite ignoriert die Schema-Klausel beim Tabellen-Mapping. `_CR_TABLE_ARGS`
returnt `None` für SQLite-URLs → Tabellen werden mit bare-name registriert
→ FK-String `"title.id"` resolved fine. Postgres-Production hingegen hat
Schema-Mode aktiv → bare-name FK findet nichts → Crash.

**Lesson:** für jede ORM-Konfigurations-Entscheidung, die Postgres-spezifisch
ist (Schema-Setting, ENUM-Casting, JSON-Columns mit `JSONB`), braucht es
einen separaten Test-Typ, der den Postgres-Codepath simuliert ohne echten
Postgres-Server.

Konkret: `backend/app/tests/test_orm_fk_resolution.py` startet ein
**Subprocess** mit `DATABASE_URL=postgresql://...`, importiert
`app.models.entities` clean (kein In-Process-Reload-Konflikt mit der
SQLAlchemy-Class-Registry), forciert FK-Resolution via `fk.column`-Access
und meldet alle Failures. Subprocess-basiert, weil `importlib.reload` mit
SQLModel/SQLAlchemy-Declarative-Registry kollidiert.

Dieser Test-Typ hätte uns die Crash-Stunde erspart. Für jeden zukünftigen
Postgres-spezifischen Codepath: **Postgres-Mode-Subprocess-Test als
Pflicht.**

---

## Backlog für Phase 5+

(Sammelliste; wird in Task 4.5b zusammen mit dem Phase-Abschluss-Bericht
finalisiert.)

* `auth_audit`-Eigentümer-Klärung mit ki-sicherheit.jetzt-Code-Review (W4
  4.1a Default-Annahme: bleibt in `public`, falls Creative Radar später
  Auth-Audit braucht, eigene Tabelle anlegen)
* Bootstrap-Skript für Fresh-Install einer leeren Postgres-DB (heute
  manuelles Migrations-Curl nötig, weil `create_all` für Postgres deaktiviert
  ist)
* Hebel C aus W3 (Prompt-Anti-Halluzination, braucht größere Stichprobe)
* Hebel D aus W3 (`error`-Status-Diagnose) — falls in W4 nicht abschließend
  gelöst
* Befund 4 aus W3 (`error`-Status mit Vision-Output)
* Status-Naming-Migration für Bestandsdaten (`done` → `analyzed`-
  Bestandskorrektur), nach 14-Tage-Toleranz-Fenster
* Image-Proxy via R2 (Performance-Hebel aus W3-Bericht)
* Image-Proxy + Bearer-Token-Kompatibilität (W4 4.3c-Diagnose ergibt ggf.
  Backlog-Eintrag)
* Hard-Cost-Cap (Erweiterung von F0.6 in Phase 5+)
* DSGVO-Sprint (F0.7) — laut Wolf-Roadmap-Entscheidung aktuell keine
  Priorität
* Living-Doc-Konsolidierung (`docs/visual_pipeline.md` aus W3-Snapshots)
