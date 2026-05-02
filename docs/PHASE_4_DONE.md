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

### Lesson 5: Bearer-Auth-Strategie für eine SPA

**Symptom:** keiner — saubere Vorbereitung.

Die FE-spezifische Limitation, dass `<img>` und `<a href download>` keinen `Authorization`-Header senden können, hätte fast den Image-Proxy und die Report-Downloads gebrochen, wenn Auth ohne Whitelist global aktiviert worden wäre.

**Architektur-Entscheidungen für Task 4.3:**

1. **Feature-Flag-basierter Roll-out** statt Split-PR. Ein Atomic-Diff, Rollback via ENV-Toggle, Frontend kann den Header sofort senden ohne Backend-Race.

2. **Public-Whitelist im Code, nicht via per-Router-Dependency.** Drei Klassen von Public-Pfaden:
   - **Probes** (`/api/health`, `/api/health/db`): Liveness/Readiness — Container-Orchestrierung darf nie auf Auth warten.
   - **`<img>`-getriggerte Endpoints** (`/api/img`): Browser-HTML-Tag-Limitation. Sicherheit weiterhin via Host-Whitelist + Größenlimit.
   - **`<a href download>`-getriggerte Endpoints** (`/api/reports/latest/download.*`): selbe Browser-Limitation. Alternative wäre ein Frontend-Refactor zu fetch+blob, der den Right-Click-Save-As-Workflow bricht — nicht im W4-Scope.

3. **Fail-closed bei Misconfig.** `AUTH_ENABLED=true` ohne `API_TOKEN` setzt zurück auf 503 mit lesbarer Message statt jeden Caller durchzuwinken. Wolf sieht den Misconfig sofort statt erst nach einem Sicherheits-Audit.

4. **OPTIONS passt durch.** CORS-Preflights senden bewusst keinen `Authorization`-Header, weil Browser sie aus dem Preflight strippen. Auth-Middleware skippt OPTIONS unconditional, CORS-Middleware antwortet wie immer.

5. **Layout-Probe-Tests** spiegeln das Production-Routing-Setup. Die Tests prüfen, dass jeder Eintrag in `PUBLIC_PATH_PREFIXES` und `PUBLIC_PATH_EXACT` einer real registrierten Route entspricht — fängt Drift, wenn ein Router umbenannt wird oder ein Endpoint verschwindet.

**Sicherheitsnotiz für Phase 5:**

`VITE_API_TOKEN` lebt im Frontend-Bundle und ist effektiv öffentlich (jeder Browser kann das Bundle laden und den Token extrahieren). Der Token ist **Anti-Bot-Schutz, nicht User-Auth**. Was er NICHT macht:
- User-Identifikation (es gibt keine User-Sessions)
- Authorization-Granularität (jeder authentifizierte Request hat denselben Scope)
- Schutz vor einem entschlossenen Angreifer, der das Bundle inspiziert

Phase 5+: Ersetzen durch (a) Session-Cookies mit Backend-User-Tabelle, (b) OAuth-Flow mit externem IdP, oder (c) signed-URL-basierte Per-Request-Auth.

### Lesson 4: Production-Dateilayout vs. lokales Repository-Layout

**Symptom:** Alembic-Upgrade-Curl scheiterte mit `CommandError: No 'script_location' key found in configuration`. Production hatte den Curl strukturell nicht ausführen können, weil `alembic.ini` und `migrations/` nicht im Container lagen — nur `app/`, `scripts/`, `start.sh` und `requirements.txt` wurden vom Dockerfile kopiert.

Lokal funktioniert alles: `pytest` läuft im Repository-Root mit allen Dateien verfügbar. Container hat `WORKDIR /app` und nur die im Dockerfile gelisteten `COPY`-Statements — eine kleinere Untermenge.

**Drittes Mal in W4 aufgetreten:**
- W4-Hotfix #1 (Backfill-Endpoint): `scripts/`-Verzeichnis fehlte im Container → `ModuleNotFoundError`
- W4-Hotfix #2 (FK-Schema-Resolution): SQLite-Tests deckten Postgres-Schema-Verhalten nicht ab → ähnlicher Test-Coverage-Bug, aber Code-Pfad-Drift
- W4-Hotfix #3 (Alembic-Config): `alembic.ini` + `migrations/` fehlten im Container → `script_location`-Fehler

**Lesson:** Tests, die das Production-Dateilayout nicht spiegeln, sind blind für Container-vs-Repo-Layout-Drift. Konkret implementiert in `tests/test_alembic_apply.py`:

1. **Filesystem-Probe (`test_alembic_ini_path_resolves_to_real_file_in_repo_layout`):** prüft, dass `ALEMBIC_INI` aus dem Apply-Skript auf eine real existierende Datei zeigt. Wenn die Pfad-Resolution divergiert oder die Datei verschoben wurde, schlägt der Test fehl, BEVOR Production ihn trifft.
2. **Subprocess-E2E ohne Mock (`test_apply_loads_alembic_config_against_sqlite_subprocess`):** spawnt einen Sub-Python-Prozess, lädt die Config real (kein `command.*`-Mock), prüft `script_location` ist gesetzt. Hermetic, ~1s, fängt jeden Config-Load-Fehler.

**Phase-5-Backlog:** Test-Strategie entwickeln, die Container-vs-Repo-Layout-Drift systematisch fängt. Vorschlag: Dockerfile-Parser-Test, der die `COPY`-Statements gegen die Code-Imports + Code-Pfad-Resolutionen prüft. Beispiel: wenn `scripts/apply_alembic_upgrade.py` auf `parents[1] / "alembic.ini"` zeigt, muss `COPY alembic.ini` im Dockerfile stehen.

Implementiert in: `Dockerfile` (jetzt mit `COPY migrations` + `COPY alembic.ini`), `scripts/apply_alembic_upgrade.py` (FileNotFoundError mit lesbarer Message statt Alembic-Silent-Fail), `migrations/__init__.py` (Belt-and-Braces-Marker, Alembic braucht ihn nicht aber macht künftige Imports deterministisch).

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
