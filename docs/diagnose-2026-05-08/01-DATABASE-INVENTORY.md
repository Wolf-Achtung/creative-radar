# 01 — Datenbank-Inventar

> **Zugriffshinweis:** Live-DB nicht erreichbar (sandbox host allowlist + kein
> `DATABASE_URL`/`API_TOKEN` im Env). Diese Inventur ist ausschließlich aus
> Code, Migrationen und Seed-Files abgeleitet. Alle Mengen-/Frische-Aussagen,
> die Live-Daten benötigen, sind als **needs live verification** markiert.
> Wolf-Ping zur Token-/Allowlist-Frage am Ende des Tagesberichts.

## Was wurde geprüft

- `backend/app/models/entities.py` — vollständiges SQLModel-Schema.
- `backend/migrations/versions/*.py` — 11 Alembic-Migrationen, jüngste
  `e5d8f1a36b40_whitelist_expansion_2026_05_05.py`.
- `backend/data/channels_perplexity_2026_05_03.csv` — 50-Zeilen-Seed.
- `backend/app/api/channels.py`, `backend/app/api/assets.py`.
- `grep` nach `channel_priority`, `synced_at`, `ki_sicherheit`, `kpa`,
  `briefing_id` über `backend/`.

## Befunde

### Schema-Übersicht (`creative_radar.*`)

11 Tabellen, alle in Schema `creative_radar` (Postgres) bzw. ohne Schema
(SQLite-Tests). Quelle: `entities.py:173-426`.

| Tabelle | Zweck | Hot-Felder |
|---|---|---|
| `channel` | Whitelist-Registry | `name, platform, url, handle, market, channel_type, priority, active, mvp, channel_role, quality_tier, acquisition_strategy, monitoring_enabled, category, import_source, notes` |
| `title` | TMDb-/manuelle Titel | `tmdb_id, title_original, title_local, franchise, content_type, market_relevance, release_date_de, release_date_us, aliases, priority, active` |
| `titlekeyword` | Keyword/Hashtag → Title | `title_id, keyword, keyword_type, active` |
| `post` | Scrapter Post / Video | `channel_id, platform, post_url(unique), external_id, published_at, detected_at, caption, raw_payload(JSON), visible_likes/comments/views/shares/bookmarks, duration_seconds, media_type, status, analysis(JSON), last_analyzed_at` |
| `asset` | Visuelles Asset zu Post | `post_id, title_id, asset_type(ENUM), language, screenshot_url, thumbnail_url, ocr_text, detected_keywords(JSON), ai_summary_de/en, ai_trend_notes, confidence_score, review_status, curator_note, include_in_report, is_highlight, visual_*, placement_*, has_title_placement, has_kinetic, kinetic_*, de_us_match_key, asset_url, vision_description, vision_model, analyzed_at` |
| `titlesyncrun` | TMDb-Pull-Audit | `source, markets, date_from/to, fetched/upserted/deduped_count, status` |
| `titlecandidate` | LLM/Regex-Vorschläge | `asset_id, suggested_title, suggested_franchise, source(ENUM), confidence, status` |
| `weeklyreport` | **Legacy** Report-Tabelle | `week_start, week_end, status, executive_summary_de/en, html_url, pdf_url, html_content` |
| `costlog` | Apify/OpenAI-Audit | `provider, operation, cost_usd_cents, cost_eur_cents, cost_meta(JSON)` |
| `cron_run` | Cron-Lauf-Audit | `started_at, completed_at, status, run_index, summary_json, error_message` |

### ENUMs (Postgres-nativ, Schema `creative_radar` resp. `public`)

Alle aus `entities.py` und Migration `7e3b2c4a8f51`:

| ENUM | Werte |
|---|---|
| `Market` (`public.market`) | `DE, US, INT, UK, MIXED, UNKNOWN` (UK seit `e5d8f1a36b40`, Postgres-only ADD VALUE) |
| `Priority` (`public.priority`) | `A, B, C` |
| `AssetType` | 19 Werte (Trailer, Trailer Drop, Teaser, Poster, Poster / Key Art, Story, Kinetic, Character Card, Character / Cast Post, Quote / Review, CTA Post, Ticket CTA, Release Reminder, Behind the Scenes, Event / Festival, Series Episode Push, Franchise / Brand Post, Discovery, Unknown) |
| `ReviewStatus` | `new, approved, highlight, rejected, needs_review` |
| `ReportStatus` | `draft, reviewed, final` |
| `CandidateStatus` | `open, resolved, ignored` |
| `CandidateSource` | `hashtag, text, ocr, openai, perplexity, matcher` |
| `ChannelRole` (`creative_radar.channel_role`) | `studio_distributor, franchise, talent_cast, regional, publisher_platform` |
| `QualityTier` (`creative_radar.quality_tier`) | `P0, P1, P2` |
| `AcquisitionStrategy` (`creative_radar.acquisition_strategy`) | `apify, youtube_api, manual` |

### Indizes (Migration `857d9777a8d0`)

Per `CREATE INDEX CONCURRENTLY` in `creative_radar`:

- `ix_post_detected_at` (`post.detected_at`)
- `ix_post_channel_id` (`post.channel_id`)
- `ix_asset_title_id` (`asset.title_id`)
- `ix_asset_review_status` (`asset.review_status`)
- `ix_asset_visual_analysis_status` (`asset.visual_analysis_status`)

Plus implizite via Migration `9a2e7c4f5b18`: partial-unique
`(post_id, asset_url) WHERE asset_url IS NOT NULL`.

`cron_run`: zwei Indizes (`started_at`, `status`).

### Channel-Inventar (Code-Sicht — siehe Hinweis am Anfang)

Quellen, die die Whitelist erzeugen:

1. **Perplexity-Seed** `backend/data/channels_perplexity_2026_05_03.csv`
   (50 Zeilen, `import_source` per `import_channels.py` gesetzt):
   - 25× Instagram (mehrheitlich DE Verleih + DE/US Streamer)
   - 6× TikTok (`disney, paramountpics, sonypictures, universalpictures, warnerbros, netflix`)
   - 19× YouTube (siehe vollständige Liste unten)

2. **Whitelist-Expansion** Migration `e5d8f1a36b40` (39 neue Rows):
   - 10× TikTok DE (Tier-A Studios + Discovery, z. B. `warnerbrosdeutschland, universalpicturesde, paramountpicturesgermany, sonypicturesgermany, disneyde, netflixde, primevideode, constantinfilm, leoninestudios, xverleih, moviepilot`)
   - 10× TikTok US (Tier-A US Studios + Streamer-Subs: `disneystudios, disneyanimation, 20thcentury, focusfeatures, searchlightpics, sonypicturesclassics, disneyplus, netflix, hulu, netflixgeeked, indiewire`)
   - 7× TikTok UK (`paramountpicturesuk, paramountplusuk, primevideouk, warnerbrosuk, universalpicturesuk, lionsgateuk, sonypictures.uk`)
   - 5× IG US Netflix-Subs/Editorial (`netflixqueue, netflixgeeked, strongblacklead, netflixtudum, hbomaxmovies`)
   - 1× IG DE (`hbomaxde`)
   - 3× IG UK (`disneyuk, disneystudiosuk, marvel_uk`)
   - 1× IG US Indie (`neonrated`)
   - 2× Discovery (TikTok DE/US: `moviepilot, indiewire`)
   - **Hygiene-Effekte:** Duplikat `warnerbros` US/TT gelöscht, `@netflix` YT
     auf `Netflix` re-mapped + gelöscht, 4× DE/IG `channel_type` backfilled.

   Die Migration ist **idempotent** (SELECT-then-INSERT) — keine Aussage über
   tatsächliche Live-Counts. **needs live verification.**

3. **Erwartete Maximal-Counts (Code-only Obergrenze):**
   - TikTok: ≤ 6 (Seed) + 27 (Expansion) − Duplikate = **vermutlich 30-33**
   - YouTube: 19 (Seed, keine Expansion-YT) = **19**
   - Instagram: 25 (Seed) + 9 (Expansion) = **vermutlich 33-34**
   - **Total: vermutlich 82-86 Channels.**

   Wolfs Erinnerungs-Notiz aus dem alten Handover (168 / IG 114 / TT 35 / YT 19)
   passt nicht zu den hier sichtbaren Quellen. Mögliche Erklärungen: weitere
   Imports über `scripts/import_channels.py` mit anderem CSV (Talent, Franchise,
   regional), die nicht im Repo liegen; oder die alten Zahlen waren aus einer
   früheren Sprint-Phase. **needs live verification.**

### Major-Studio-Handles (aus PAIRS + Migrations + Seed)

| Studio | DE-TT | US-TT | UK-TT | DE-IG | US-IG | YT |
|---|---|---|---|---|---|---|
| warnerbros | warnerbrosdeutschland | warnerbros | warnerbrosuk | (siehe Seed) | — | WarnerBrosDE, WarnerBrosPictures |
| sonypictures | sonypicturesgermany | sonypictures + sonypicturesclassics | sonypictures.uk | sonypictures.de | — | SonyPicturesGermany, SonyPicturesEntertainment |
| primevideo / amazon | primevideode | primevideo | primevideouk | (Seed) | — | PrimeVideo |
| disney | disneyde | disney + disneystudios + disneyanimation + 20thcentury + focusfeatures + searchlightpics + disneyplus | — | (Seed: disneyplusde) | — | WaltDisneyStudios, DisneyPlus |
| netflix | netflixde | netflix + netflixgeeked + hulu | — | (Seed) | netflixqueue, netflixgeeked, strongblacklead, netflixtudum | Netflix, NetflixDE |
| paramount | paramountpicturesgermany | paramountpics | paramountpicturesuk + paramountplusuk | (Seed) | hbomaxmovies | ParamountPictures, ParamountPlus |
| universalpictures | universalpicturesde | universalpictures | universalpicturesuk | (Seed) | — | UniversalPictures, UniversalPicturesDE |

**`disney` US-Handle in `PAIRS["disney"]`** lautet `disney`, aber die Whitelist
registriert `disneystudios` und `disneyanimation`. `aggregate_pair`
schreibt das in `notes`, crasht nicht — aber der Pair-Report bekommt
US-seitig **nichts**, solange kein Channel mit Handle exakt `disney` in
der DB existiert. **needs live verification** — falls der Handle `disney`
tatsächlich existiert (z. B. aus einem älteren Seed), läuft alles; sonst
ist das ein latenter Bug in PAIRS.

### Asset-/Post-Volumen, Frische, Metriken

- Volumen pro Plattform: **needs live verification.** (Cron-Selektion-Code in
  `cron_channel_selection.py:24-30` nennt einen Default-Threshold von `1`,
  Begründung: "Datenbasis 2026-05-04 nur ~31 Posts gesamt" — also vor wenigen
  Tagen war das Volumen sehr klein.)
- Letztes `synced_at` pro Channel: **es gibt kein `synced_at`-Feld.** Frische
  ist aus `MAX(post.detected_at)` bzw. `MAX(post.published_at)` pro
  `post.channel_id` ableitbar; `channel.updated_at` ist nur Audit der
  Channel-Row selbst.
- Metriken pro Post (alle drei Plattformen, Spaltennamen identisch):
  `visible_likes, visible_comments, visible_views, visible_shares,
  visible_bookmarks, duration_seconds`. Detail siehe `05-RANKING-FEASIBILITY.md`.
- Aggregations-/Briefing-Output-Tabelle: `weeklyreport` existiert, wird aber
  vom **neuen** Insight-Engine-Pfad **nicht beschrieben** — siehe
  `02-INSIGHT-ENGINE-AUDIT.md`.

### `channel_priority` — Wolfs Vermeidungsregel

`grep` findet **eine einzige Stelle**: `backend/app/api/assets.py:75`. Dort
wird das Feld als API-**Output** unter dem Namen `channel_priority` aus
`channel.priority` gemappt. **Es ist keine DB-Spalte.** Wolfs Aussage stimmt
also schemaseitig — die Inkonsistenz ist nur die Umbenennung im JSON-Output
(`priority` → `channel_priority`). Keine Migration, keine Templates, keine
Briefings im Repo referenzieren `channel_priority`.

## Inkonsistenzen / Lücken

1. **PAIRS["disney"] vs. Whitelist:** `PAIRS` erwartet US-Handle `disney`,
   Whitelist hat `disneystudios` + `disneyanimation`. **needs live
   verification, dann ggf. PAIRS oder Whitelist anpassen.**
2. **Kein `synced_at` auf `channel`**: jede Frische-Auswertung muss über
   `MAX(post.detected_at)` aggregieren. Für Cadence-Sprint potenziell
   relevant, aber nicht blockierend.
3. **`weeklyreport`-Tabelle ist toter Code**: Modell existiert, der neue
   Insight-Engine-Pfad nutzt sie nicht (siehe `02`). Migration zur Entfernung
   nicht vorhanden. **Nicht entfernen ohne Wolf-Decision.**
4. **`asset.visual_analysis_status` kennt zwei Quellen für "fertig"**:
   `analyzed` (neue Pipeline) und `done` (Legacy). Der Selector akzeptiert
   beide; Code-Kommentar nennt das explizit. Kein Schaden, aber zwei
   parallele Pfade in Querys.
5. **`(handle, platform)` hat keinen UNIQUE-Index** (siehe Migration-Docstring
   `e5d8f1a36b40`). Duplikate sind nur per Python-Precheck verhindert. Risiko
   bei direkter SQL-Insertion außerhalb der Migration.

## Risiken

- **Whitelist-Drift:** Ein neuer Channel-Handle, der über Admin-API oder
  ad-hoc-SQL hinzugefügt wird, kann das `(handle, platform)`-Doppelpaar
  brechen. Schon einmal passiert (`warnerbros` US/TT in `e5d8f1a36b40` H1).
- **Disney-US-Pair**: Ohne Live-Check unklar, ob der Pair-Report leer ist.
- **`weeklyreport`-Tabelle**: bei Schema-Migrationen sammelt sie ungenutzten
  Ballast; wenn jemand sie versehentlich für den neuen Pfad reaktiviert,
  schreibt sie über alte Drafts hinweg.

## Empfehlung für Sprint-Planung

1. **Vor Ranking-Sprint:** Live-Counts pro Plattform/Markt bestätigen
   (Wolf-Ping → Token + Allowlist), Disney-US-Handle-Frage klären.
2. **`synced_at`-Spalte nicht nötig** für Cadence — Ableitung aus
   `post.detected_at` reicht.
3. **`weeklyreport` vor Multi-Plattform V2a klären**: entweder als
   Persistenz für Briefings reaktivieren oder formal als deprecated
   markieren. Siehe `06-OPEN-QUESTIONS.md`.

---

## Anhang: Verzahnung mit ki-sicherheit.jetzt

`grep -rn "ki-sicherheit\|ki_sicherheit\|kpa\|briefing_id" backend/` → **0
Treffer**. Kein geteilter Code, kein gemeinsamer JWT-Helper, keine geteilten
Service-Module. Imports nur aus `app.*` (Creative-Radar-eigen) und üblichen
Public-PyPI-Paketen. **Saubere Trennung — Token-Rotation hat keine
Cross-Project-Auswirkung auf den Code in diesem Repo.**

DB-Trennung: nicht aus Code prüfbar (`DATABASE_URL` setzt der Deploy). Wolfs
Vorab-Notiz nennt Postgres `Postgres-_HAO` in Railway-Projekt
`overflowing-unity` als alleinige CR-DB; **Annahme akzeptiert, needs live
verification falls relevant.**
