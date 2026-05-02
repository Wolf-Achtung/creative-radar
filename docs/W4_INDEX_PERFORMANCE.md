# W4 Task 4.2: Performance-Indizes anwenden

**Erstellt:** 2026-05-02 / Phase 4 W4 Task 4.2
**Vorbedingung:** F0.2 Schema-Migration ist durch (PR #37 + Wolf-Curl, 2026-05-02), 8 CR-Tabellen sind in `creative_radar`-Schema, UI lädt mit Bestandsdaten.
**Auftrag:** Alembic produktiv aktivieren + die in W2 angelegte Revision `857d9777a8d0` (5 Performance-Indizes) anwenden.

## 1. Code-Anpassungen

### Alembic-Setup für `creative_radar`-Schema

`backend/migrations/env.py` erweitert:

- `version_table_schema=_migration_schema()` — bei Postgres landet `alembic_version` im `creative_radar`-Schema, sodass die Migrations-Tracking-Tabelle mit den CR-Daten reist
- `include_schemas=True` — Alembic walkt das ORM-Metadata mit Schema-Awareness

`_migration_schema()` ist ein lokaler Helper, der die Schema-Entscheidung anhand der DB-URL trifft (Postgres → `creative_radar`, SQLite → None). Spiegelbild zur ORM-Logik in `app/models/entities.py:_resolve_table_schema`.

### Revision `857d9777a8d0` für `creative_radar`-Schema mit CONCURRENTLY

Indizes werden mit `schema="creative_radar"` UND `postgresql_concurrently=True` erstellt — letzteres verhindert eine ACCESS EXCLUSIVE-Sperre auf der Tabelle während des Index-Builds. Bei aktuell kleinen Tabellen (~50 Posts) wäre der Lock nur Millisekunden lang, aber best practice ist concurrent für jede Production-DDL auf live-Systemen:

```python
SCHEMA = "creative_radar"

def upgrade() -> None:
    # CONCURRENTLY kann nicht in einer Transaction laufen → autocommit_block
    with op.get_context().autocommit_block():
        for name, table, columns in _INDEXES:
            op.create_index(
                name, table, columns,
                unique=False, if_not_exists=True, schema=SCHEMA,
                postgresql_concurrently=True,
            )

def downgrade() -> None:
    with op.get_context().autocommit_block():
        for name, table, _columns in reversed(_INDEXES):
            op.drop_index(
                name, table_name=table, if_exists=True, schema=SCHEMA,
                postgresql_concurrently=True,
            )
```

`if_not_exists=True` bleibt idempotent — Re-Run nach Erfolg ist No-Op.

Fünf Indizes wie in W2 spezifiziert:

| Index-Name | Tabelle | Spalten | Zweck |
|---|---|---|---|
| `ix_post_detected_at` | `creative_radar.post` | `(detected_at)` | Time-Range-Queries in `report_selector.select_assets_for_report` |
| `ix_post_channel_id` | `creative_radar.post` | `(channel_id)` | Channel-Filter |
| `ix_asset_title_id` | `creative_radar.asset` | `(title_id)` | Title-Joins in `insights` und Assets-API |
| `ix_asset_review_status` | `creative_radar.asset` | `(review_status)` | `/api/assets?review_status=...` |
| `ix_asset_visual_analysis_status` | `creative_radar.asset` | `(visual_analysis_status)` | Counter in `insights`, batch-analyze-Query |

## 2. Apply-Script

`backend/scripts/apply_alembic_upgrade.py` orchestriert drei Schritte idempotent:

1. `CREATE SCHEMA IF NOT EXISTS creative_radar` (defensive — sollte schon da sein, falls F0.2 lief)
2. Wenn `creative_radar.alembic_version` leer ist: `alembic stamp cf842bbfaeb5` (Baseline-Revision). Damit weiß Alembic, dass die Baseline-Tabellen schon existieren — verhindert ein erneutes `CREATE TABLE`.
3. `alembic upgrade head` — wendet alle ausstehenden Revisions an. Aktuell: `857d9777a8d0` (5 Indizes). Re-Run nach Erfolg ist No-Op ("already at head").

## 3. Throwaway-Endpoint

`POST /api/admin/run-alembic-upgrade` ruft das Apply-Script auf, geschützt durch denselben `ADMIN_MIGRATION_TOKEN` wie `/api/admin/run-schema-migration`.

Response-JSON enthält `before_revision`, `after_revision`, `baseline_stamped` (bool), `actions` (Liste), `errors`, `summary`.

## 4. Wolf-Aktion

```bash
TOKEN=$(openssl rand -hex 16)
# Railway-ENV: ADMIN_MIGRATION_TOKEN=$TOKEN setzen, Auto-Redeploy abwarten

# Forward
curl -X POST -H "Authorization: Bearer $TOKEN" \
  https://api.creative-radar.de/api/admin/run-alembic-upgrade
# Erwartet:
# {"before_revision": null, "after_revision": "857d9777a8d0",
#  "baseline_stamped": true,
#  "actions": ["stamped baseline cf842bbfaeb5", "upgraded to head"],
#  "errors": {}, "summary": "Alembic: none -> 857d9777a8d0. ..."}

# Idempotenz-Check
curl -X POST -H "Authorization: Bearer $TOKEN" \
  https://api.creative-radar.de/api/admin/run-alembic-upgrade
# Erwartet:
# {"before_revision": "857d9777a8d0", "after_revision": "857d9777a8d0",
#  "baseline_stamped": false,
#  "actions": ["upgraded to head"],
#  "errors": {}, "summary": "Alembic: 857d9777a8d0 -> 857d9777a8d0. ..."}

# Verifikation in der Postgres-Konsole (optional):
# SELECT indexname, tablename, schemaname FROM pg_indexes WHERE schemaname = 'creative_radar';
# Erwartet: 5 ix_*-Indizes plus die SQLAlchemy-Auto-Indizes auf primary keys.

# Token aus Railway entfernen (Throwaway-Cleanup-Hygiene; finaler Cleanup
# aller Admin-Endpoints kommt in Task 4.5).
```

## 5. Performance-Test (Wolf-eigene Aktion, falls gewünscht)

Pre/Post-Vergleich von zwei typischen Queries via `EXPLAIN ANALYZE`:

```sql
-- Time-range filter (sollte ix_post_detected_at nutzen)
EXPLAIN ANALYZE
SELECT * FROM creative_radar.post
WHERE detected_at BETWEEN '2026-04-25' AND '2026-05-02';

-- Visual-status counter (sollte ix_asset_visual_analysis_status nutzen)
EXPLAIN ANALYZE
SELECT visual_analysis_status, COUNT(*)
FROM creative_radar.asset
GROUP BY visual_analysis_status;
```

Bei aktueller Datenmenge (20 Assets, ~50 Posts) ist der Index-Effekt nicht messbar — Postgres macht Sequential Scans, weil die Tabellen zu klein sind. Index-Wirkung wird erst bei wachsendem Volumen sichtbar (~1000+ Rows). Das ist erwartet und kein Bug.

## 6. Tests

- 5 neue Tests in `app/tests/test_alembic_apply.py` (Stamp-vs-Skip-Logik, Fehler-Isolation pro Schritt, Summary-Format)
- 2 neue Tests in `app/tests/test_admin_migration.py` (Auth-Gate für den neuen Endpoint, Pass-Through der Stats)

`pytest -q` 137/137 grün.

## 7. Backlog

- Phase 5: Index-Effekt messen, sobald Datenmenge >1000 Posts/Assets erreicht
- Phase 5: weitere Indizes ggf. nachziehen (z.B. `post.platform`, `asset.de_us_match_key` für Trend-Queries) — aktuell nicht im W2/W4-Scope
