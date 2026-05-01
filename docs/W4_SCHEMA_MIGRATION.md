# W4 Schema-Migration: Diagnose

**Erstellt:** 2026-05-01 / Phase 4 W4 Task 4.1a
**Zweck:** Klassifikation aller Tabellen in `railway.public` für die F0.2 Schema-Trennung. Welche Tabellen ziehen ins neue `creative_radar`-Schema, welche bleiben.
**Charakter:** Snapshot-Diagnose, Vorbereitung für 4.1b (Migrations-Skript) und 4.1d (Migration-Endpoint-Aufruf).
**Vorbedingung erfüllt:** Railway On-Demand-Backup vom 2026-05-01 19:06 UTC, 1.53 GB, 7 Tage Retention.

## 1. Methodik

CR-Tabellen werden aus den ORM-Modellen in `backend/app/models/entities.py` abgeleitet. SQLModel ohne explizites `__tablename__` derived den Namen aus dem Klassennamen (lower-case, kein Plural). Fremd-Projekt-Tabellen werden anhand der Hinweise aus dem W4-Briefing (`analyses`, `appetizer_*`, `auth_audit`) plus der Konvention klassifiziert, dass alle CR-Tabellen aus den 8 ORM-Modellen stammen.

## 2. CR-Tabellen (definitiv ins `creative_radar`-Schema)

Abgeleitet aus `backend/app/models/entities.py`. Acht Tabellen:

| # | Tabelle | ORM-Klasse | Zeile | Inhalt |
|---|---|---|---|---|
| 1 | `channel` | `Channel` | 77 | Social-Media-Kanäle (Instagram, TikTok, etc.) inkl. Handle, Markt, Aktiv-Flag |
| 2 | `title` | `Title` | 95 | Whitelist-Titel aus TMDb-Sync, Franchise-Aliase |
| 3 | `titlekeyword` | `TitleKeyword` | 117 | Keywords pro Title (Aliase, alternative Schreibweisen) |
| 4 | `post` | `Post` | 127 | Apify-Crawl-Posts mit Metriken, post_url, raw_payload |
| 5 | `asset` | `Asset` | 152 | Creative-Assets (Posts mit Title-Match), Vision-Outputs, Status |
| 6 | `titlesyncrun` | `TitleSyncRun` | 202 | TMDb-Sync-Lauf-Historie |
| 7 | `titlecandidate` | `TitleCandidate` | 216 | Title-Vorschläge aus Visual-Analyse |
| 8 | `weeklyreport` | `WeeklyReport` | 228 | Wöchentliche Report-Snapshots |

## 3. Fremd-Projekt-Tabellen (bleiben in `public`)

Aus W4-Briefing erwähnt, **keine ORM-Repräsentation in CR-Codebasis**:

| Tabelle | Vermutete Zugehörigkeit |
|---|---|
| `analyses` | KI-Sicherheit-Analyse-Tool oder anderes Projekt — kein CR-ORM-Modell mit dem Namen |
| `appetizer_analytics` | Appetizer-Projekt (separate App in derselben Postgres-Instanz) |
| `appetizer_leads` | dito |

Werden vom Migrations-Skript NICHT angefasst.

## 4. Unklare Tabelle — Wolf-Entscheidung nötig

| Tabelle | Frage | Optionen |
|---|---|---|
| `auth_audit` | Welchem Projekt gehört diese Tabelle? Im W4-Briefing als „könnte `ki-sicherheit.jetzt`-Audit sein oder shared" markiert. Kein CR-ORM-Modell mit dem Namen. | (a) CR-spezifisch → ins `creative_radar`-Schema mitziehen<br>(b) Anderes Projekt → bleibt in `public`<br>(c) Shared / Cross-Projekt → bleibt in `public`, alle Projekte greifen darauf zu |

**Empfehlung:** **Option (b) oder (c)** — bleibt in `public`. Begründung: kein CR-Code referenziert `auth_audit`, und der Tabellenname klingt nach generischem Audit-Trail (vermutlich `ki-sicherheit.jetzt`-Auth-Logging). Migration ins CR-Schema würde die Fremd-App brechen, falls sie aktiv liest/schreibt.

**Wolf-Aktion vor Task 4.1b:** entscheide (a/b/c) für `auth_audit`.

## 5. Postgres-Standard-Tabellen

| Tabelle | Behandlung |
|---|---|
| `alembic_version` | **Falls bereits in `public` existiert:** ins `creative_radar`-Schema verschieben, weil sie zum CR-Migrations-Tracking gehört. **Falls nicht existiert:** wird beim ersten `alembic upgrade` (Task 4.2) automatisch im Default-Schema angelegt — wir konfigurieren Alembic so, dass es im `creative_radar`-Schema landet (`version_table_schema="creative_radar"` in `migrations/env.py`). |

**Hinweis:** vor 4.1b ein read-only Check, ob `alembic_version` schon in `public` existiert. Falls ja: in die Migrations-Liste aufnehmen. Falls nein: nur Alembic-Konfig in `migrations/env.py` anpassen.

## 6. Weitere mögliche Tabellen

Wolf hat im Production-Postgres möglicherweise weitere Tabellen aus anderen Projekten. Migrations-Skript verwendet daher eine **Whitelist** der CR-Tabellen (nicht eine Blacklist), damit unbekannte Tabellen unangetastet bleiben. Das ist sicherer als ein „alles außer X verschieben"-Ansatz.

## 7. Migrations-Schritte (für 4.1b)

Sobald Wolf-Entscheidung zu `auth_audit` vorliegt:

```sql
-- Idempotent: Schema anlegen
CREATE SCHEMA IF NOT EXISTS creative_radar;

-- Pro CR-Tabelle, nur wenn sie noch in public liegt:
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables
             WHERE table_schema='public' AND table_name='channel') THEN
    ALTER TABLE public.channel SET SCHEMA creative_radar;
  END IF;
END $$;
-- ... wiederholt für post, asset, title, titlekeyword, titlesyncrun,
-- titlecandidate, weeklyreport, [optional auth_audit + alembic_version]
```

`ALTER TABLE SET SCHEMA` ist O(1), Indizes/Constraints/FKs werden automatisch mitverschoben. Foreign-Key-Referenzen zwischen CR-Tabellen bleiben gültig (Postgres rewrited sie auf das neue Schema).

## 8. Risiko-Bewertung

| Risiko | Mitigation |
|---|---|
| FK-Reference zwischen CR-Tabelle und Fremd-Tabelle | Sehr unwahrscheinlich (CR hat eigene Channels/Titles, kein Cross-Projekt-FK aus den ORM-Modellen). Verifikation in 4.1b durch FK-Listing-Query. |
| 5–10s Sichtbarkeits-Lücke pro `ALTER TABLE` | Akzeptabel im Pilot. Wolf macht UI-Smoke-Test direkt nach Migration. |
| ORM-Code referenziert `creative_radar.X` BEVOR Migration läuft | Reihenfolge in 4.1c/4.1d: ORM-Schema-Setting (4.1c) wird gepusht, Code geht live, dann Migration läuft (4.1d). Während des Zeitfensters zwischen Code-Deploy und Migration-Endpoint-Aufruf wäre das Backend kaputt — daher: 4.1c und 4.1d in **derselben Deploy-Welle** (4.1c-Push triggert Railway-Redeploy, sofort danach 4.1d-curl). |
| Migration teilweise erfolgreich | Skript ist idempotent: bei Re-Run werden nur die noch-nicht-verschobenen Tabellen migriert. |
| Rollback-Bedarf | Rollback-Skript: `ALTER TABLE creative_radar.X SET SCHEMA public` für jede CR-Tabelle. Symmetrisch idempotent. Wird in 4.1b mit ausgeliefert. |

## 9. Was 4.1b konkret produzieren muss

Sobald `auth_audit`-Entscheidung vorliegt:

1. `backend/scripts/migrate_to_creative_radar_schema.py` — idempotenter Forward-Migrator
2. `backend/scripts/rollback_creative_radar_schema.py` — symmetrischer Rollback (Tabellen zurück nach `public`)
3. `backend/app/tests/test_migration_script.py` — Mocked-Postgres-Connection-Test, prüft DDL-Output und Idempotenz-Logik

## 10. Was 4.1c konkret produzieren muss

In `backend/app/models/entities.py`, jedes `table=True`-Modell bekommt:

```python
__table_args__ = {"schema": "creative_radar"}
```

Plus Test-Setup-Adapter (`conftest.py`?), weil SQLite die Postgres-Schema-Konvention nicht versteht. Wahrscheinlich: bei SQLite-Test-Engine das Schema-Setting via `with_variant()` oder Monkeypatch unterdrücken.

## 11. Was 4.1d konkret produziert

Throwaway-Endpoint `POST /api/admin/run-schema-migration`, analog zum Backfill-Endpoint aus dem W3-Follow-up-Mini-Run:

- Bearer-Token-Auth via `ADMIN_MIGRATION_TOKEN`
- Synchrone Ausführung (DDL ist O(1) pro Tabelle, gesamt ~Sekunden)
- Response: JSON mit `tables_moved`, `tables_skipped` (idempotent), `errors`, `summary`
- Cleanup in Task 4.5 zusammen mit allen anderen W4-Throwaway-Endpoints

**In-function-import + Dockerfile-COPY-Pattern** wie nach W3-Hotfix-Lesson.

---

**Ende der 4.1a-Diagnose.** Wolf-Ping zu `auth_audit` ausstehend; sobald (a/b/c) entschieden, geht 4.1b los.
