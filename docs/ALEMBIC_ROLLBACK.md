# Alembic-Rollback in Production — Notfall-Anleitung

**Status:** Notfall-Pfad. Reguläre Code-Strategie ist **forward-only**.

Seit Sprint 5.2.2-Hygiene wird `alembic upgrade head` automatisch beim
Railway-Deploy als `preDeployCommand` ausgeführt (`backend/railway.json`).
Ein fehlschlagender Migration-Run blockt den Deploy — der alte Container
bleibt aktiv. Das deckt ~99 % der Fälle ab.

Diese Datei beschreibt die verbleibenden ~1 %: was tun, wenn eine
Migration **erfolgreich durchlief**, aber Production danach kaputt ist?

---

## Migration-Compatibility-Klassen

`preDeployCommand` läuft **vor** dem neuen Container-Start, aber der
**alte Container ist noch live** und schreibt parallel auf die DB. Das
ist nur dann sicher, wenn die Migration **rückwärts-kompatibel** zum
alten Code ist.

### Sicher (additive Migrationen, single-PR)
- `ADD COLUMN ... NULL` (oder mit `server_default`)
- `CREATE TABLE`
- `CREATE INDEX CONCURRENTLY` (oder regulär bei kleinen Tabellen)
- `COMMENT ON ...` (siehe Smoketest-Migration 571f54840f19)
- Neue ENUM-Werte am Ende (`ALTER TYPE ... ADD VALUE`)

Der alte Container bemerkt diese Änderungen nicht und schreibt
weiterhin korrektes SQL. Single-PR-Deploy ist OK.

### Heikel — Mehrschritt-Strategie nötig (Expand/Contract)
- `DROP COLUMN`, `DROP TABLE`
- `ALTER COLUMN TYPE` (auch implizit via SQLModel-Type-Wechsel)
- `ALTER COLUMN ... SET NOT NULL` auf bestehender, ggf. teilbefüllter Spalte
- `RENAME COLUMN`, `RENAME TABLE`
- Entfernen von ENUM-Werten

Diese Änderungen würden den alten Container kaputtschreiben oder
SELECT-Queries crashen, solange beide Versionen parallel laufen.

### Mehrschritt-Pattern (Expand → Migrate → Contract)
1. **PR 1 — Expand:** Additive Migration (z. B. neue Spalte parallel
   zur alten anlegen), Code liest **beide** Schemas und schreibt in
   beide. Deploy.
2. **PR 2 — Backfill (optional):** Daten von alt nach neu kopieren
   (Migration oder Job), Code-Defaults auf den neuen Pfad umstellen.
   Deploy.
3. **PR 3 — Contract:** Alte Spalte/Tabelle entfernen, Code-Pfad zu
   altem Schema löschen. Deploy.

Zwischen den Deploys jeweils Rollout vollständig abwarten und
verifizieren — keine zwei Phasen in einem Deploy zusammenziehen.

---

## Wann braucht man Rollback?

- Migration durchläuft sauber, aber das neue Schema ist semantisch
  fehlerhaft (z. B. NOT-NULL-Constraint auf einer Spalte, die in
  Production noch leere Werte hat).
- Schema-Change wurde freigegeben, aber ein Bug im Application-Code
  korrumpiert beim ersten Schreibzugriff Daten.
- Migration ist trotz CI-Pass in Production fehlgeschlagen mit
  Halbzustand (z. B. Tabelle erstellt, Index nicht). `alembic_version`
  steht dann ggf. inkonsistent zur tatsächlichen Schema-Realität.

In allen drei Fällen: **erst stoppen, dann denken, dann rollen.**

---

## Wer darf rollen?

**Nur Wolf.** Kein automatischer Rollback, kein Self-Service durch
andere Operator. Rollback in Production = Schema-mutierende DDL gegen
Live-Daten — das ist ausschließlich Wolfs Hand am Knopf.

---

## Schritte (Standard-Sequenz)

### 1. Service stoppen
Über Railway-Dashboard das `creative-radar`-Service-Deployment pausieren
oder den letzten erfolgreichen Vor-Migration-Container per
„Redeploy previous" erneut aktivieren — verhindert weitere Schreibzugriffe
auf den fehlerhaften Schema-Stand.

### 2. DB-Backup-Snapshot
Im Railway-Dashboard → Postgres-Service → **Backups** → manuelles Backup
auslösen. Vor *jedem* Rollback-Versuch. Kein Rollback ohne Snapshot.

Falls die Migration Daten verloren hat (z. B. `drop_column`): erst
prüfen, ob der jüngste Auto-Backup-Snapshot vor der Migration noch
liegt.

### 3. `alembic downgrade` ausführen

#### Pfad A (bevorzugt) — Railway CLI
```bash
# Lokal Railway CLI installieren (einmalig):
npm i -g @railway/cli   # oder: brew install railway

railway login
railway link            # creative-radar Service auswählen

# Eine Revision zurück:
railway run alembic downgrade -1

# Oder gezielt auf eine bekannte Revision:
railway run alembic downgrade <revision_id>
```

`railway run` führt das Kommando im Service-Context aus — mit denselben
Env-Vars (`DATABASE_URL` etc.) wie der Production-Container, aber von
deinem Laptop aus. Das ist die saubere Variante.

#### Pfad B (Notfall, wenn CLI-Pfad nicht verfügbar)
Einmalig einen Throwaway-Endpoint reaktivieren — Pattern aus Sprint
5.2.1 PR #49 (Endpoint anlegen, Migration triggern) und PR #50 (sofort
wieder entfernen).

```python
# Beispiel-Skizze, NICHT mergen, NICHT pushen — temporär im Branch:
@router.post("/api/_emergency/alembic-downgrade", include_in_schema=False)
def _emergency_downgrade(target: str, _admin = Depends(require_admin_token)):
    from alembic import command
    from scripts.apply_alembic_upgrade import _alembic_config
    command.downgrade(_alembic_config(), target)
    return {"downgraded_to": target}
```

Sequenz:
1. Branch `chore/emergency-alembic-downgrade-<timestamp>` anlegen.
2. Endpoint hinzufügen, **mit Admin-Token-Schutz**.
3. Deploy.
4. Endpoint einmal aufrufen.
5. Verifizieren (siehe Schritt 4).
6. Endpoint sofort wieder entfernen + neuer Deploy.
7. Branch löschen.

Schritt 6 ist nicht optional. Throwaway heißt Throwaway.

### 4. Verifizieren
```sql
-- In Postgres-Session:
SELECT version_num FROM creative_radar.alembic_version;
-- Sollte die erwartete Vor-Migration-Revision zurückgeben.

-- Dann Schema-Stand stichprobenartig prüfen:
\d creative_radar.<betroffene_tabelle>
```

API-Health-Check:
```bash
curl -fsS https://<production-url>/api/health
```

### 5. Service neu starten
Im Railway-Dashboard den letzten guten Container redeployen oder den
Service unpausen.

### 6. Post-Mortem
Was lief falsch? Konsequenz: Tests anpassen, damit dieselbe Klasse von
Fehlern in CI gefangen wird.

---

## Maintenance-Tooling (nicht Rollback, aber verwandt)

`backend/scripts/apply_alembic_upgrade.py` ist der Wrapper, der lokal
und in alten Migrations-Sprints für **Vorwärts**-Migrationen genutzt
wurde. Er bleibt im Repo:

- für lokale Dev-Runs gegen frisch geclonte Postgres-Instanzen,
- als Referenz-Implementation für die Throwaway-Endpoint-Variante
  (Pfad B oben),
- für hermetische Tests in `backend/app/tests/test_alembic_apply.py`.

Er wird **nicht** mehr für Production-Deploys aufgerufen — das übernimmt
seit Sprint 5.2.2-Hygiene `preDeployCommand`.

---

## Was NICHT tun

- Kein `alembic downgrade base` ohne sehr guten Grund (löscht alles bis
  zum Baseline-Stamp).
- Keine direkten DDL-Statements gegen die Live-DB ohne entsprechende
  Migration im Repo — sonst läuft `alembic_version` und Schema-Realität
  auseinander.
- Keine manuellen `INSERT INTO alembic_version` zur Schein-Reparatur,
  ohne die zugehörigen Schema-Schritte tatsächlich auszuführen oder zu
  reverten.
