# User-Login (E-Mail + Code) — Rollout-Anleitung

Sprint User-Login 2026-07. Die gesamte Website (Startseite + alle
Wochen-Briefs) steht hinter einem E-Mail+Code-Login für ~15 bekannte
Nutzer. Flow-Parameter identisch zum Schwesterprojekt
(api-ki-backend-neu / make-ki-frontend): **6-stelliger Code, 10 Minuten
gültig, Einmal-Nutzung**; Rate-Limits 10× Code-Anfordern / 5× Login pro
5 Minuten (pro IP **und** pro E-Mail-Adresse).

Bewusste Abweichungen von der Referenz:

| Thema | Referenz | Creative Radar | Warum |
| --- | --- | --- | --- |
| Allowlist | Frozenset im Quellcode | `app_user`-Tabelle + Admin-UI | User pflegen ohne Deploy |
| Code-Speicher | Redis/In-Memory, Klartext | DB, SHA-256-Hash, `attempts`-Deckel | überlebt Restarts, DB-Leak leakt keinen Code |
| Session | JWT, 1 h | HMAC-Cookie, 30 Tage | Wochen-Brief-Leser sollen nicht wöchentlich neu einloggen |
| Revocation | — (JWT läuft ab) | Pro-Request-Check auf `app_user.active` | Sperren im Admin-Bereich wirkt sofort |

## Railway-Variablen (Wolf-Schritt)

Backend-Service (Railway):

| Variable | Wert | Pflicht |
| --- | --- | --- |
| `USER_AUTH_ENABLED` | `true` — **zuletzt** setzen (Master-Schalter) | ja |
| `USER_SESSION_SECRET` | `openssl rand -hex 32` (eigener Wert, NICHT das Admin-Secret) | ja |
| `RESEND_API_KEY` | API-Key aus dem Resend-Dashboard | ja (bei Resend) |
| `MAIL_FROM` | z. B. `login@creative-radar.de` (Domain muss in Resend verifiziert sein) | ja |
| `MAIL_FROM_NAME` | Default `Creative Radar` | nein |
| `EMAIL_PROVIDER` | Default `resend`; alternativ `smtp` | nein |
| `USER_SESSION_TTL_SECONDS` | Default 2592000 (30 Tage) | nein |
| `LOGIN_CODE_TTL_SECONDS` | Default 600 (10 Minuten) | nein |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD` / `SMTP_STARTTLS` | nur bei `EMAIL_PROVIDER=smtp` | nein |
| `ADMIN_USER_EMAILS` | komma-separierte E-Mails, die nach dem normalen Code-Login automatisch Voll-Admin sind (z. B. `wolf@trailerhaus.de`) — Passwort-Login unter `/admin` bleibt als Fallback | nein |
| `DISABLE_EMAILS` | `true` = Kill-Switch (Code landet nur im Log) — nie in Production | nein |

Resend-Setup (einmalig): Domain `creative-radar.de` in Resend anlegen,
DNS-Records (SPF/DKIM) bei Netlify/Registrar setzen, verifizieren —
erst dann stellt Resend an fremde Postfächer zu.

## Rollout-Reihenfolge (Stufen-Muster wie beim Admin-Login)

1. Backend deployen (Migration `f7a3d2c815b9` läuft mit; `USER_AUTH_ENABLED`
   bleibt aus — Website weiter öffentlich).
2. Frontend deployen (Login-Gate ist bei ausgeschalteter Auth unsichtbar,
   `/api/auth/me` antwortet `auth_enabled=false` → „eingeloggt“).
3. `USER_SESSION_SECRET`, `RESEND_API_KEY`, `MAIL_FROM` in Railway setzen.
4. Unter `/admin` → Tab **Nutzer** die ~15 E-Mail-Adressen anlegen.
5. `USER_AUTH_ENABLED=true` setzen → ab dem nächsten Deploy/Restart ist
   die Website hinter dem Login.

Rückweg: `USER_AUTH_ENABLED=false` genügt — kein Code-Rollback nötig.

## Was bleibt öffentlich

- `/impressum`, `/datenschutz` (Rechtsseiten, Frontend-Routen ohne Gate)
- `/api/health*` (Probes), `/api/auth/*` (Login-Flow selbst)
- `/api/img`, `/api/thumbnails`, `/storage` (Bild-Subresourcen, `<img src>`-
  Limitierung — wie bei den bestehenden Auth-Schichten)
- `/docs`, `/redoc`, `/openapi.json` — **nur noch bei `DOCS_PUBLIC=true`**
  (Staging/lokal). Die Pilot-Entscheidung wurde am 06.08.2026 zurückgenommen:
  in Production liegt die API-Beschreibung hinter dem Bearer-Token. Auf der
  User-Session-Schicht bleiben die Pfade offen — wer den Token hat, braucht
  für die Doku kein Login-Cookie.
- `/api/admin/*` hat weiter die eigene Passwort-Session; eine gültige
  Admin-Session darf zusätzlich alle Daten-Endpoints nutzen (kein
  Doppel-Login in der Admin-UI). Admin-Zugriffe erzeugen KEINE
  Usage-Events.

## Nutzungs-Analyse

Event-Log `usage_event` (User × Aktion × Zeitpunkt + Kontext), nur für
eingeloggte Nutzer. Aktionen: `login`, `landing_view`, `brief_view`
(mit `pair`), `title_view`, `forecast_view`, `report_download`.
Auswertung: `/admin` → Monitoring → Sektion **Nutzung** (wer zuletzt
aktiv, meistgeöffnete Studio-Briefs, Aktionen gesamt); API:
`GET /api/admin/usage?days=30`.
