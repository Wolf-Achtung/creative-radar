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
