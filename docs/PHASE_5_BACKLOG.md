# Phase 5 — Living Backlog (out-of-sprint findings)

This file collects items surfaced *during* Phase-5 work that are deliberately
not fixed in their finding-sprint, with an explicit owner sprint.

## Sprint 5.1 (Image-Bug-Fix) — side findings

### Ephemeral container storage for legacy evidence files

**Found:** While diagnosing the asset-thumbnail bug.

**Mechanic:** `LocalFileStorage` writes to `backend/storage/` inside the
Railway container. The container filesystem is wiped on every redeploy, so any
file referenced by an `asset.visual_evidence_url` of the form
`/storage/evidence/<file>.jpg` (pre-F0.1 layout) or by a bare object key
written before the last deploy is gone — the URL still resolves to a 404 from
the StaticFiles mount.

After the 5.1 fix, *new* assets render correctly because the scrape pipeline
writes them to local storage on the live container before the asset is shown.
But thumbnails for assets older than the most recent redeploy will still
break, because their backing files no longer exist.

**Owner sprint:** Phase 5/6 boundary — pull into Sprint 5.4 (Hardening) if
time, otherwise Phase 6. Title: **"S3/R2 migration for evidence files"**.

**Scope:**
- Flip `STORAGE_BACKEND=s3` in Railway with R2 credentials (we already use R2
  for new assets per Wolf's note — this is about wiring it as the *default*
  backend so writes survive deploys).
- One-shot backfill script that walks `asset.visual_evidence_url` rows still
  pointing at `/storage/evidence/...`, attempts a re-scrape from
  `visual_source_url`, and updates the column to the new object key.
- Drop the StaticFiles mount once no live row references it (or keep as
  fallback if backfill is partial).

**Not in 5.1:** the bug-fix only addresses URL routing on the frontend; the
underlying file-availability problem is separate and bigger.

### YouTube thumbnail host-allowlist

**Found:** While reviewing `proxyImageUrl` allowlist.

**Mechanic:** YouTube thumbnail URLs (`i.ytimg.com`, `i9.ytimg.com`) are not
in `PROXY_HOST_SUFFIXES`. They flow through unchanged today, which works
because YouTube generally allows hotlinking — but the moment the YouTube
connector lands and the asset volume jumps, we want them on the same proxy
path as Instagram/TikTok for cache-control consistency.

**Owner sprint:** **Sprint 5.2.3** (YouTube Data API connector). When that
sprint adds YouTube as a data source, extend two allowlists in lockstep:
- `PROXY_HOST_SUFFIXES` in `frontend/src/api/imageUrl.js`
- `image_proxy_host_suffixes` default in `backend/app/config.py`

Test in `frontend/src/api/imageUrl.test.mjs` already documents the current
pass-through behaviour as a regression guard so the 5.2.3 PR will naturally
flip it to a proxy-routing assertion.

**Not in 5.1:** allowlist is per-data-source and belongs with the connector
that introduces the source.

## Sprint 5.1 smoke-test findings — Sprint 5.4 Hardening Block

Smoke-test on the live deploy after PR #45 came back "ok with findings". URL
routing and auth are correct (Tests 3+4 green), but `<img>` requests still
surface as `net::ERR_BLOCKED_BY_ORB` in the browser console for two distinct
reasons. None are caused by PR #45 — the PR closed the routing bug it was
scoped to. Track the four items below as a single hardening block before
Sprint 5.4 closes.

### B1 — S3/R2 migration for persistent storage

**Mechanic:** confirmed ephemeral-container symptom. Files written before the
14:39 redeploy are gone; `/storage/<key>` returns 404. Same root cause as the
"ephemeral container storage" finding above — kept as B1 here so the
hardening block reads end-to-end.

**Owner sprint:** Sprint 5.4. Cross-reference: see "Ephemeral container
storage for legacy evidence files" above for full scope.

### B2 — Apify refresh logic / data freshness

**Mechanic:** Instagram/TikTok signed CDN URLs carry an `x-expires` query
param (TikTok example from the smoke test: `x-expires=1777532400` =
2026-04-30 11:00 UTC). Once expired, the upstream returns 403 and our
`/api/img` proxy wraps that into a 502 (`proxy.py:61-67`). All assets older
than ~24-36h hit this path. Apify-run cadence is currently push-only — there
is no refresh trigger when a stale asset is opened.

**Owner sprint:** Sprint 5.4.

**Scope options (pick one in the implementation PR):**
- Detect expired `x-expires` at proxy time and trigger a targeted re-scrape
  of `asset.visual_source_url` (background task, return 502 to the current
  request, succeed on the next page-load).
- Add a "refresh asset" button on the Asset-Detail view that re-runs the
  Apify connector for a single asset.
- Background sweep: cron job that re-scrapes assets whose
  `visual_evidence_url` carries an `x-expires` within the next 6h.

Wolf-decision needed on which path before implementation.

### B3 — Image-shaped error responses for `/storage` and `/api/img`

**Mechanic:** today both endpoints return JSON on errors (`{"detail":"..."}`,
content-type `application/json`). Chrome ORB sniffs the JSON body and blocks
the response cross-origin, so the `<img onError>` handler never sees a usable
status — the user gets a blank slot instead of the broken-image icon.

**Owner sprint:** Sprint 5.4. Pure hardening — does not change the error
condition, only the failure shape.

**Scope:**
- `/storage`: replace the StaticFiles 404 handler for image-extension paths
  with a 1×1 transparent PNG response, status 404, `image/png`.
- `/api/img`: catch `HTTPException` for image requests and return the same
  1×1 PNG, preserving the status code in a `X-Proxy-Error` header for
  debugging.
- Frontend `<img onError>` keeps working; broken thumbnails render as the
  intended fallback instead of an opaque ORB block.

### B4 — `Cross-Origin-Resource-Policy: cross-origin` headers

**Mechanic:** neither `/storage` nor `/api/img` sets a CORP header today.
With B3 in place the bodies will be image-shaped and ORB sniffing should
pass, but explicit `Cross-Origin-Resource-Policy: cross-origin` removes the
last ambiguity for cross-origin `<img>` loads from `app.creative-radar.de`.

**Owner sprint:** Sprint 5.4. Two-line change once B3 lands.

**Scope:**
- Add `Cross-Origin-Resource-Policy: cross-origin` to the `/api/img`
  `StreamingResponse` headers (`proxy.py:88-91`).
- Add the same header to the StaticFiles mount via a small middleware or
  custom `StaticFiles` subclass on `/storage`.
- Optional: `X-Content-Type-Options: nosniff` once we are confident every
  served file has a correct `content-type`.

### Block-level note

B1 and B2 are the *real* failure sources observed in the smoke test. B3 and
B4 are hardening — they make the failure shape image-tauglich in the browser
but do not fix the underlying 404/502 conditions. Land all four in Sprint
5.4; sequencing is B1, B2 (parallel) → B3 → B4.

## Sprint Tech-Debt-CI (Backend-CI) — side findings

### Move per-test DB fixtures from SQLite to the Postgres service

**Found:** PR #143 final-fix sprint, while scharfschalten des Backend-CI-Workflows.

**Mechanic:** The CI workflow runs a Postgres-13 service container, but
the per-test DB fixtures in the existing suite (≥ 25 test files: e.g.
`test_admin_regenerate_replace.py:db`, `test_brief_previous_context.py:db`,
`test_json_parse_retry.py:db`, every `tempfile.mkstemp(..., suffix=".db")`)
each instantiate their own tempfile **SQLite** engine. `entities.py:_resolve_table_schema`
flips the `schema='creative_radar'` clause on/off at module-import time
based on `settings.database_url`; if pytest sees `DATABASE_URL=postgresql://...`,
the entity metadata carries the schema and the fixtures' sqlite engines
crash on `SQLModel.metadata.create_all` with
`sqlite3.OperationalError: unknown database creative_radar`.

PR #143 final fix sets a step-level `DATABASE_URL=sqlite:///:memory:`
override for the pytest step. That makes CI green and meaningful **for
the test code as it stands**, but the residual gap is honest:

- Bootstrap + `alembic stamp head` exercise Postgres dialect (schema-
  qualified DDL, Enum types, migration apply on a real PG instance).
- The 606 pytest tests themselves run against SQLite — Postgres-specific
  paths (advisory locks via `pg_try_advisory_lock`, the
  `creative_radar.*`-schema-qualified queries, JSONB column behaviour,
  composite-PK ON CONFLICT semantics) are **not** exercised in CI.

**Owner sprint:** dedicated Tech-Debt-CI follow-up. Not a single-commit
fix.

**Scope (rough sketch, to be refined when the sprint starts):**
- Replace the per-test sqlite-tempfile fixture pattern with a fixture
  that wraps the CI Postgres service in a per-test transaction (rollback
  on teardown) or per-test schema (drop on teardown).
- Audit `entities.py:_resolve_table_schema` for race conditions when
  multiple test modules import in different orders.
- Verify advisory-lock tests (`generate_and_persist_report`'s
  double-check-locking path) actually execute the Postgres branch, not
  the SQLite fallback.
- Local dev experience: keep an opt-in fast path for sqlite so `pytest`
  without infra still works on a laptop without Docker.

**Why now (the note, not the fix):** logged here so the gap is visible
and tracked, not invisible. The 12.05 CI-failures and the 2026-05-17/25
Disney/warnerbros JSON-parse incidents both rode through a green-but-
not-meaningful test suite; this is the structural lever to make
future "CI is green" claims actually carry weight on Postgres-only code
paths.


## Daten-Hygiene-Sprint (26.05.2026) — side findings

### Sauberes `media_type`-Feld (Option A2 aus dem Daten-Hygiene-Befund)

**Befund:** Beim Apify-Import wird `Post.media_type` auf den Plattform-
Namen gesetzt (`monitor.py:172`: `media_type=platform`) — also "instagram"
/ "tiktok" / "youtube", nicht den echten Medientyp. Apifys eigenes `type`-
Feld ("Image" / "Sidecar" / "Video" auf Instagram) bleibt im
`raw_payload`-JSON erhalten, ist aber nicht als saubere Spalte
abfragbar. Damit gibt es heute keinen direkten Filter, um Bild-Posts,
Carousels und Video-Posts zu unterscheiden — der A1-Fix muss die
Unterscheidung über das Proxy-Signal `views == 0 && likes > 0` machen.

**Bewusst nicht jetzt gefixt (Wolf-Entscheidung 26.05.):** Der A1-Anzeige-
Fix in `_format_post_line` löst die akute „0 Views"-Verzerrung im
Roundup-Prompt. Ein sauberes `media_type`-Feld wäre die robuste Lösung
für künftige Filter/Sichten, ist aber nur dann tech-debt-würdig, wenn
weitere Filter/Sichten den Medientyp brauchen.

**Owner-Sprint:** ungeplant. Pull in, sobald ein Sprint Medientyp-
abhängige Filter braucht (z. B. „Roundup nur über Video-Posts" oder
„Bild-Post-Eigenstatistik").

**Scope (Skizze):**
- Apify-Item-`type`-Feld in den Import-Normalizer aufnehmen
  (`apify_connector.normalize_instagram_item` und
  `normalize_tiktok_item`).
- `monitor.py:172` `media_type=platform` durch den echten Medientyp
  ersetzen.
- Backfill-Skript für bestehende `post`-Rows aus
  `raw_payload->>'type'` (Instagram) bzw. konstant „Video" (TikTok).
- Frontend/Reports nicht zwingend anfassen — der A1-Fix bleibt
  funktional, A2 ist additive Reinheit.

**Warum als Notiz, nicht als Fix:** A1 löst das sichtbare Symptom; A2
wäre Reinraum-Hygiene ohne aktuellen Bedarf. Sichtbar gehalten, damit
ein späterer „Medientyp-Filter"-Sprint nicht von vorne diagnostizieren
muss.
