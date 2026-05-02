# Phase 4 — Abschluss-Bericht

**Status:** Phase 4 abgeschlossen am 2026-05-02. Diese Datei dokumentiert das
gelieferte Scope, alle Lessons-Learned und die Phase-5-Backlog-Übergabe. Für
den kompakten externen Stand siehe `PHASE_4_SUMMARY.md` im Repo-Root.

---

## Übersicht: alle Wochen + Tasks

### Woche 1 — Stabilisierung & Sicherheitsnetze (8 Tasks)

| Task | Inhalt | Roadmap-Ref |
|---|---|---|
| 1.1 | Insights-Counter-Bug-Fix (`visual_analyzed`-Set korrigiert) | F1.4 |
| 1.2 | Doppel-`netlify.toml` entfernt | QW-3 |
| 1.3 | `startCommand` auf Dockerfile-CMD konsolidiert | QW-4 |
| 1.4 | `.env.example` synchron zur `Settings`-Klasse (30 Felder) | F2.17 |
| 1.5 | Tote Pfade entfernt (`app/jobs/`, `app/data/`, `app/prompts/`) | F2.14 |
| 1.6 | Issues #3/#7/#10/#12 closed + Branch-Hygiene | F2.15 |
| 1.7 | Frontend-Deps gepinnt + `package-lock.json` | F1.12 |
| 1.8 | Alembic-Baseline `cf842bbfaeb5` vorbereitet (nicht aktiviert) | F1.14 |

### R2-Storage-Adapter-Mini-Run (vor W2)

Boto3-Setup, `services/storage.py` mit `LocalFileStorage` + `S3Storage` + `resolve_url()` + `get_storage()`-Factory + Smoke-Test-Skript. Adapter-Pattern, kein produktiver Schreib-Pfad in diesem Run.

### Woche 2 — Asset-Capture-Persistenz (R2)

| Task | Inhalt | Roadmap-Ref |
|---|---|---|
| 2.1 | Storage-Adapter in capture-Pipeline verdrahtet (Object-Key-Design) | F0.1 |
| 2.2 | Idempotentes Backfill-Skript für Bestandsassets | F0.1 |
| 2.3 | Performance-Indizes als Alembic-Revision (pending) | F2.18 |
| 2.4 | Sprint-8.2-Pfad-Verifikation für Object-Keys | – |
| 2.5 | Sprint-8.2-Puffer (passiv) | – |

### Woche 3 — Visual-Pipeline ehrlich machen (5 Tasks)

| Task | Inhalt | Roadmap-Ref |
|---|---|---|
| 3.1 | Status-Übergangs-Diagnose → `docs/W3_STATUS_DIAGNOSE.md` | F0.4 prep |
| 3.2 | Honest-Status-Klassifikation (4 neue Status, 3 Logik-Fixes) | F0.4 |
| 3.3 | Selector-Update für Object-Keys (parallel zu Legacy) | F0.4 |
| 3.4 | Vision-Output-Quality-Diagnose → `docs/W3_VISION_QUALITY.md` | F0.4 |
| 3.5 | 3 Hebel: Language-Whitelist + No-Source-Phrase + Counter-Fix | F0.4 |

### Woche 4 — DB-Trennung, Auth, Cost-Logging (5 Tasks + 4 Hotfixes)

| Task | Inhalt | Roadmap-Ref |
|---|---|---|
| 4.1 | DB-Trennung in `creative_radar`-Schema (4 Sub-Tasks) | F0.2 |
| Mini-Run | Status-Konvergenz Variante (a): `done` → `analyzed` | F0.4 closing |
| 4.2 | Performance-Indizes via Alembic angewendet (CONCURRENTLY) | F2.18 |
| 4.3 | Bearer-Token-Auth mit Feature-Flag-Rollout | F0.3 |
| 4.4 | Cost-Logging für Apify + OpenAI | F0.6 |
| 4.5 | Phase-Abschluss + Throwaway-Cleanup | – |

**Vier W4-Hotfixes** waren zwischendurch nötig: scripts-Dir im Container, FK-Schema-Resolution, alembic.ini-Layout, Doppel-Auth auf Migrations-Endpoints. Alle in Lessons-Learned dokumentiert.

### Erledigte Roadmap-Punkte

- **F0.1** Asset-Capture-Persistenz (R2-Adapter + Backfill)
- **F0.2** DB-Trennung (`creative_radar`-Schema, 8 Tabellen migriert)
- **F0.3** Bearer-Token-Auth (Feature-Flag, Public-Whitelist, Frontend-Header-Injection)
- **F0.4** Visual-Pipeline ehrlich (Object-Key-Selector + Status-Konvergenz auf `analyzed`)
- **F0.6** Cost-Logging (cost_log-Tabelle, Hooks an drei Stellen, Read-Endpoint)
- **F2.18** Performance-Indizes (5 + 3 für cost_log = 8 Indizes total via Alembic)
- **F1.4** Insights-Counter-Bug
- **F1.12** Frontend-Deps-Pinning
- **F1.14** Alembic produktiv
- **F2.14** Tote Pfade
- **F2.15** Branch-Hygiene + Issues
- **F2.17** `.env.example` Sync

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

### Lesson 6: Auth-Schicht-Drift zwischen Tasks

**Symptom:** `/api/admin/run-alembic-upgrade` antwortet nach Aktivierung der globalen Bearer-Auth (Task 4.3) mit `Invalid token` für jeden Request — sowohl mit `API_TOKEN` als auch mit `ADMIN_MIGRATION_TOKEN`. Der Endpoint ist erreichbar (kein 5xx), aber kein Token-Wert passiert beide Auth-Schichten gleichzeitig.

**Ursache:** Task 4.1d (Schema-Migration-Endpoint) führte einen lokalen `_verify_token`-Check ein, der `settings.admin_migration_token` (= `ADMIN_MIGRATION_TOKEN`) prüfte. Damals existierte keine globale Auth-Middleware; das war die einzige Schicht. Task 4.3 ergänzte später die globale Bearer-Middleware, die `settings.api_token` (= `API_TOKEN`) prüft. Niemand passte die Migrations-Endpoints an. Resultat: zwei sequenzielle Auth-Schichten mit unterschiedlichen Erwartungs-Werten. Wolfs ADMIN_MIGRATION_TOKEN ≠ API_TOKEN → keine Variante passiert beide.

**Hotfix (W4-Hotfix-4):** `_verify_token` aus `app/api/admin.py` entfernt. Migrations-Endpoints stützen sich nur noch auf die globale Bearer-Middleware mit `API_TOKEN`. `admin_migration_token`-Setting + `ADMIN_MIGRATION_TOKEN`-ENV-Variable obsolet, in `.env.example` entfernt.

Plus: Regression-Guard-Test `test_endpoints_have_no_local_token_check` in `test_admin_migration.py` — mit `auth_enabled=False` müssen alle Endpoints ohne `Authorization`-Header durchkommen. Wenn jemand wieder einen lokalen Token-Check einführt, bricht der Test.

**Sicherheits-Equivalenz:** API_TOKEN ist Wolf-only (Anti-Bot-Schutz für SPA, gleichzeitig „Admin-Token" weil nur Wolf ihn hat). Throwaway-Endpoints werden in Task 4.5 sowieso entfernt — Defense-in-Depth via separatem Token war ohnehin nominal.

**Phase-5-Backlog:** Auth-Schicht-Audit pro Task einbauen, sodass spätere Auth-Änderungen frühere Endpoint-Auth-Logik konsistent anpassen. Konkrete Idee: Layout-Probe-Test, der für jeden Endpoint der `app.routes`-Registry prüft, dass keine zwei Auth-Mechanismen gleichzeitig greifen (entweder Dependency-basiert ODER Middleware-basiert, nicht beide).

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

## Production-Stand am Phase-Ende (2026-05-02)

**Datenbank** (`railway.public` Postgres-Instanz):
- Schema `creative_radar` mit 8 CR-Tabellen (channel, title, titlekeyword, post, asset, titlesyncrun, titlecandidate, weeklyreport) + cost_log + alembic_version
- 8 Performance-Indizes (5 in `creative_radar.post`/`asset` + 3 in `creative_radar.costlog`), alle CONCURRENTLY erstellt
- Alembic produktiv: Head-Revision `4f1c8b2d9e30`
- `auth_audit` bleibt in `public` (Wolf-Entscheidung W4 4.1a)
- Pre-W4-Backup vom 2026-05-01 19:06 UTC, 1.53 GB, 7d Retention — bleibt Sicherheitsnetz bis Phase-5

**Backend** (`api.creative-radar.de`, Railway):
- Bearer-Auth aktiv (`AUTH_ENABLED=true`), `API_TOKEN` gesetzt
- Public-Whitelist: `/api/health*`, `/api/img*`, `/docs`, `/redoc`, `/openapi.json`, `/api/reports/latest/download.{html,md}`
- 14 von 18 Bestandsassets mit Evidence-Storage in R2/Local (4 fetch_failed durch TikTok-CDN-Hotlink-Block, dokumentiert)
- F0.1 Object-Key-Capture aktiv, F0.4 Selector erkennt parallel Object-Keys + Legacy-Pfade
- Status-Konvergenz Variante (a): in-repo-Pipeline schreibt jetzt `analyzed` als kanonischen Erfolgs-Status; `done` bleibt 14d toleriert
- Cost-Logging-Hooks aktiv: Apify-Connector, Visual-Analysis, Creative-AI

**Frontend** (`app.creative-radar.de`, Netlify):
- `VITE_API_TOKEN` gesetzt, sendet Bearer-Header bei allen `api()` und `upload()`
- Pinned Versions: react/react-dom 19.2.5, vite 8.0.10, @vitejs/plugin-react 6.0.1
- Image-Proxy bleibt URL-basiert (`<img>` kann keinen Header senden)

**Tests:** 161/161 grün am Phase-Ende. 95 neue Tests in W1–W4 dazugekommen, einschließlich vier neuer Test-Pattern: Subprocess-basierte Postgres-Mode-Probes (FK-Resolution, Alembic-Config), Layout-Probes (Public-Path-Whitelist, ALEMBIC_INI-Pfad), Regression-Guards (kein zweiter Auth-Layer), und StaticPool-basierte SQLite-Test-Engines mit `dependency_overrides`.

**Permanent-Endpoints am Phase-Ende:** `GET /api/admin/cost-summary` ist der einzige neue admin-Endpoint, der bestehen bleibt. Alle Throwaway-Endpoints (run-schema-migration/-rollback/-alembic-upgrade plus W3-Backfill und Sample) wurden in Task 4.5 entfernt; die zugehörigen Skripte unter `backend/scripts/` bleiben als Wartungs-Tools für künftige manuelle Aufrufe.

---

## Backlog für Phase 5+

### Operativ
- **`done`-Bestandsdaten-Migration** nach 14d Stabilbetrieb: `UPDATE asset SET visual_analysis_status = 'analyzed' WHERE visual_analysis_status = 'done'`. Nach Migration kann `done` aus `ALLOWED_TERMINAL_STATUS_FROM_DATA`, `ANALYSIS_DONE_STATES` und `insights.py` Counter-Set raus.
- **Identifikation des externen `analyzed`-Setters**: kein Repo-Code-Pfad schreibt `analyzed`, aber Production hat 4/20 Assets damit. Vermutlich Apify-Webhook außerhalb des Trees oder manuelle DB-Updates. Wolf-eigene Diagnose der Apify-Webhook-Konfiguration.
- **`auth_audit`-Eigentümer-Klärung** mit ki-sicherheit.jetzt-Code-Review (Default-Annahme: bleibt in `public`, falls CR später Auth-Audit braucht, eigene Tabelle in `creative_radar` anlegen)
- **`error`-Status-Diagnose** (Hebel D aus W3): A24-Euphoria-Sample zeigt `error`-Status mit vollständiger Vision-Analyse — Status widerspricht Inhalt. Vermutlich derselbe externe Code-Pfad wie der `analyzed`-Setter.

### Sicherheit
- **Auth-Schicht-Audit pro Task** einbauen (W4-Hotfix-4-Lesson 6): Layout-Probe-Test der für jeden Endpoint prüft, dass keine zwei Auth-Mechanismen gleichzeitig greifen (entweder Dependency-basiert ODER Middleware-basiert, nicht beide).
- **Bearer-Auth → Session-Cookies oder OAuth**: `VITE_API_TOKEN` lebt im Frontend-Bundle und ist effektiv öffentlich. Anti-Bot-Schutz, keine User-Auth. Phase-5-Übergang sobald mehr als 5 Pilot-User.
- **Signed-URLs für Image-Proxy + Report-Downloads**: ersetzen die heutigen Public-Whitelist-Pfade durch URL-Signaturen, die ohne Header authentifizieren.
- **Image-Proxy via R2** (Performance-Hebel aus W3): direkte CDN-Zugriffe vermeiden, Cache-Hits aus R2 bedienen.
- **DSGVO-Sprint** (F0.7): aktuell keine Priorität laut Wolf-Roadmap; eigene Phase oder mit oder ohne juristische Drittprüfung.

### Test-Strategie
- **Dockerfile-Parser-Test** (W4-Hotfix-3-Lesson 4): `COPY`-Statements gegen Code-Pfad-Resolutionen cross-prüfen. Drei Vorfälle in W4 argumentieren für den Build-Time-Aufwand.
- **Bootstrap-Skript für Fresh-Install** einer leeren Postgres-DB (heute manuelles Migration-Curl nötig, weil `create_all` für Postgres deaktiviert)

### Vision-Pipeline
- **Hebel C aus W3** (Prompt-Anti-Halluzination): braucht größere Stichprobe als die 10 W3-Samples
- **Living-Doc-Konsolidierung** (`docs/visual_pipeline.md`): W3-Snapshots `W3_STATUS_DIAGNOSE.md` und `W3_VISION_QUALITY.md` zusammenziehen, sobald Pipeline-Verhalten 30+ Tage stabil

### Cost-Logging
- **Hard-Cost-Cap** (Erweiterung von F0.6): Tagesschwellen aus Master-Briefing als automatische Stop-Condition implementieren (Apify >15 €/Tag, OpenAI >5 €/Tag, Monats-Max 150 €). Aktuell nur Logging.
