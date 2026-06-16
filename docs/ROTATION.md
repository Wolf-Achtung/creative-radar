# Token-Rotation — Runbook

Stand: Sprint Security 16.06.2026.

Creative-Radar nutzt **zwei** Bearer-Tokens für die API. Beide leben an je
zwei Stellen (Server + Aufrufer), die getrennt gepflegt werden — bei einer
Rotation müssen beide Seiten in der unten vorgegebenen Reihenfolge angefasst
werden, sonst bricht der Aufrufer mit `403 Invalid token`.

## Die zwei Tokens

| Token | Server (Railway-ENV) | Aufrufer | Scope |
|---|---|---|---|
| **Haupt-API-Token** | `API_TOKEN` | Frontend-Bundle (Netlify-ENV) | Alle nicht-öffentlichen Routes |
| **Cron-Token** | `CRON_API_TOKEN` | GitHub-Secret `CR_API_TOKEN` (Workflow `.github/workflows/cron-sync.yml`) | **Nur** `POST /api/admin/cron/sync-all` |

Warum getrennt: Der Ausfall vom **15.06.2026** entstand, weil der Wochen-Cron
am Haupt-`API_TOKEN` hing — eine Rotation des Haupt-Tokens riss den Cron mit
(`403`). Der dedizierte `CRON_API_TOKEN` (Least-Privilege, nur der Sync-Pfad)
entkoppelt das: Haupt-Token rotieren berührt den Wochenlauf nicht mehr.

Mechanik im Code: `backend/app/auth.py` → `auth_middleware`. Der Cron-Token
wird via `_tokens_match` (konstant-zeitig, `hmac.compare_digest`) **nur** auf
`CRON_SYNC_PATH = "/api/admin/cron/sync-all"` zusätzlich akzeptiert. Ist
`CRON_API_TOKEN` nicht gesetzt, fällt der Sync-Pfad auf den Haupt-Token zurück
(Backward-Compat).

## Rotation: Haupt-API-Token (`API_TOKEN`)

Reihenfolge zwingend Server → Aufrufer, sonst sperrt sich das Frontend aus:

1. **Railway:** `API_TOKEN` auf den neuen Wert setzen.
2. **Redeploy abwarten**, bis die neue ENV live ist (Health-Check grün).
3. **Netlify:** den Frontend-Bundle-ENV-Wert auf denselben neuen Wert setzen,
   Frontend neu deployen.
4. **Verifizieren:** eine geschützte Route mit dem neuen Token aufrufen →
   `200`/`202`; mit dem alten → `403`.

> Hinweis: Solange `CRON_API_TOKEN` gesetzt ist, ist der Wochen-Cron von
> dieser Rotation **nicht** betroffen.

## Rotation: Cron-Token (`CRON_API_TOKEN`)

Reihenfolge zwingend Server → Aufrufer:

1. **Railway:** `CRON_API_TOKEN` auf den neuen Wert setzen.
2. **Redeploy abwarten**, bis die neue ENV live ist.
3. **GitHub:** Secret `CR_API_TOKEN` (Repo → Settings → Secrets and variables
   → Actions) auf denselben neuen Wert setzen.
4. **Verifikations-Run:** GitHub → Actions → „Cron Sync" → **Run workflow**
   (`workflow_dispatch`). Erwartung: HTTP `202` im Job-Log
   (`Cron sync triggered successfully.`). Bei `403` stimmen die zwei Werte
   nicht überein — Schritt 1/3 prüfen.

## Erstmalige Einführung des Cron-Tokens (Migration)

Solange `CRON_API_TOKEN` **nicht** gesetzt ist, läuft der Cron weiter über
`API_TOKEN` (kein Bruch). Zum Aktivieren der Entkopplung:

1. **Railway:** `CRON_API_TOKEN` = neuer, eigener Random-String setzen.
2. **Redeploy abwarten.**
3. **GitHub:** Secret `CR_API_TOKEN` auf diesen neuen Wert umstellen (vorher
   trug es den Wert von `API_TOKEN`).
4. **Verifikations-Run** wie oben (`workflow_dispatch` → `202`).

Ab dann ist der Cron unabhängig vom Haupt-Token rotierbar.
