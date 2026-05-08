# 05 — Ranking-Machbarkeit

## Was wurde geprüft

- `backend/app/models/entities.py` Felder `Post.visible_*`,
  `duration_seconds`, `published_at`, `detected_at`, `raw_payload`.
- `backend/app/services/apify_connector.py` (`normalize_public_item`,
  `normalize_tiktok_item`).
- `backend/app/services/youtube_connector.py` (`normalize_youtube_video`).
- `backend/app/services/insight_engine.py` (`_engagement_sum`, Top-Post-Sort).

## Befunde

### Pro Plattform: was steht in der DB

Identisches Spaltenset auf `creative_radar.post` für alle drei Plattformen
(`entities.py:249-279`):

| Spalte | TikTok | Instagram | YouTube |
|---|---|---|---|
| `visible_likes` | `diggCount` / `heartCount` / `likes` | `likesCount` / `likes` | `statistics.likeCount` |
| `visible_comments` | `commentCount` / `comments` | `commentsCount` / `comments` | `statistics.commentCount` |
| `visible_views` | `playCount` / `views` | `videoViewCount` / `videoPlayCount` / `views` | `statistics.viewCount` |
| `visible_shares` | `shareCount` / `shares` | `shareCount` / `shares` | **null** (nicht über API) |
| `visible_bookmarks` | `collectCount` / `bookmarks` | `collectCount` / `bookmarks` | **null** |
| `duration_seconds` | `videoMeta.duration` / `duration` | `duration` / `videoDuration` | aus ISO-8601 (`PT1M30S`) |
| `published_at` | `createTimeISO` | `timestamp` / `takenAt` | `snippet.publishedAt` |
| `caption` | `text` / `description` / `caption` | `caption` / `text` / `description` | `title + description` |
| `raw_payload` | komplett gespeichert | komplett gespeichert | komplett gespeichert |

**TikTok + Instagram haben volle Metrik-Parität** (likes, comments, views,
shares, saves). **YouTube fehlt strukturell shares + saves** — das ist
eine API-Limitation der YouTube Data API v3, kein Code-Bug.

### Was Apify im `raw_payload` zusätzlich liefert (Stichprobe via Code-Mapping)

Aus `_image_from_item`, `normalize_*_item` und Apify-Actor-Doku
(`apify~instagram-scraper`, `clockworks~tiktok-scraper`):

| Feld in `raw_payload` | TikTok | Instagram | nicht persistiert? |
|---|---|---|---|
| `hashtags` (Array of `{name, ...}`) | ja | ja | wird in `_extract_hashtags` extrahiert |
| `musicMeta` (TT) | ja | — | als `_creative_radar_music` im raw mitgeführt |
| `authorMeta.fans` (Follower-Count) | ja | — | nicht persistiert; in `raw_payload` |
| `videoMeta.coverUrl` | ja | — | über `_image_from_item` |
| `videoMeta.coverDownloadUrl` | ja | — | nicht persistiert |
| `mentions` | ja | ja | nicht persistiert |
| `effectStickers` (TT) | ja | — | nicht persistiert |
| `displayUrl` / `images[]` (IG) | — | ja | über `_image_from_item` |
| Engagement-Rate (Apify-natives Feld) | **nein** (TT-Actor liefert es nicht direkt) | **nein** | n/a |

→ **Apify-natives `engagement_rate` ist nicht zuverlässig vorhanden.** Der
Wert müsste aus den `visible_*`-Feldern + Follower-Count berechnet werden,
und Follower steht nur in `raw_payload['authorMeta']['fans']` (TT) oder
müsste pro IG-Channel separat erfragt werden.

### Aktuelle Engagement-Logik im Code

`insight_engine.py:576-582`:

```python
def _engagement_sum(post):
    return (likes or 0) + (comments or 0) + (shares or 0) + (bookmarks or 0)
```

→ **Views fließen NICHT in den Engagement-Sum ein.** Der Sortier-Wert
für Top-Posts (`_channel_stats:811-815`) sortiert nach diesem
absoluten Engagement, nicht nach einer Rate.

→ Das benachteiligt:

- **YouTube** (kein shares/saves) — wenn der Multi-Plattform-Sprint
  eine Plattform-übergreifende Top-Liste will, schneiden YT-Posts immer
  schlechter ab.
- **Posts mit hoher View- aber niedriger Reactions-Quote** (typisch für
  reines Reichweiten-Material) — die werden nicht "top".

### Aktivierungs-Rate — drei Optionen mit Pro/Contra

**Option 1 — `(reactions + comments + saves) / views`** (Meta-Branchen-Standard)

```
activation = (visible_likes + visible_comments + visible_bookmarks) / NULLIF(visible_views, 0)
```

| Pro | Contra |
|---|---|
| Branchen-üblich, GF und CD kennen die Größenordnung (1-5 % gut, >5 % stark). | YouTube hat keine `bookmarks` → Formel ist YT-schief, müsste auf `(likes + comments) / views` ausgewichen werden. |
| Funktioniert pro Post ohne Channel-Kontext. | Setzt voraus, dass `views` immer nicht-null ist — IG-Carousel-Posts ohne Video haben oft `views=null`. |
| Vergleichbar TT ↔ IG-Reels, weniger TT ↔ YT. | "Saves" verhalten sich auf TT/IG sehr unterschiedlich (TT-Save = "Lesezeichen", IG-Save = "Sammlung") — nicht 1:1. |

**Option 2 — `reactions / views`** (engste Definition, Fan-Aktion)

```
activation = visible_likes / NULLIF(visible_views, 0)
```

| Pro | Contra |
|---|---|
| Maximaler Plattform-Vergleich (alle drei haben likes + views). | Ignoriert Comments + Saves — die im Trailer-Kontext oft aussagekräftiger sind ("ich kommentiere = ich bin investiert"). |
| Klare Größenordnung (TT: 5-15 % ist normal, IG-Reels 2-7 %). | Likes sind plattform-beeinflusst (TT-Likes deutlich häufiger als IG-Likes). |

**Option 3 — `_engagement_sum / views`** (aktuelle Code-Konvention erweitert)

```
activation = (visible_likes + visible_comments + visible_shares + visible_bookmarks) / NULLIF(visible_views, 0)
```

| Pro | Contra |
|---|---|
| Konsistent mit `_engagement_sum` im Insight-Engine — keine zweite Konvention. | YouTube hat shares=null, bookmarks=null → kollabiert auf likes+comments. |
| Berücksichtigt auch Shares (Word-of-Mouth-Signal). | Shares sind auf TT/IG sehr unterschiedlich erfasst. |

→ **Empfehlung Wolf-Decision:** Option 1 für TT+IG, mit YT-Sonderpfad
`(likes + comments) / views`. Decision-Bauplan in `06-OPEN-QUESTIONS.md`.

### Cross-Plattform-Vergleichbarkeit: TT-Views vs. IG-Plays

- TikTok-`playCount` zählt **jeden Play, auch < 1s und Auto-Replay**.
- Instagram-`videoViewCount` zählt **Views ab 3s**.
- YouTube-`viewCount` zählt **Views ab 30s** (dauerhaft) oder **die volle
  Lifetime View-Sum**.

→ **TT-Views sind ~2-5× höher als äquivalente IG-/YT-Reichweite.** Eine
naive Sortierung nach `views` gibt jedem TT-Post einen Bonus.

**Normalisierungs-Optionen:**

| Strategie | Pro | Contra |
|---|---|---|
| `views / channel_followers` | Plattform-übergreifend stabil | Follower-Count nicht persistiert (siehe oben) — müsste aus `raw_payload['authorMeta']['fans']` (TT) bzw. eigenem IG-Profile-Pull (IG) ergänzt werden. |
| Plattform-Z-Score (`(views - mean_views_per_platform) / stdev`) | Keine externen Daten nötig | Brauche genug Posts pro Plattform pro Channel; bei aktuell wenig Daten unzuverlässig. |
| Plattform-getrennt ranken, im Frontend nebeneinander | Trivial, kein Modell-Risiko | Kein einzelner "Top-3 Quartal"-Wert. |

→ **Empfehlung:** **Plattform-getrennt ranken im V1.** Cross-Plattform-Top-Liste
ist Sprint-2-Material.

### Datenfrische pro Major-Studio-Channel

**needs live verification.** Aus Code: pro Channel die Aggregation
`MAX(post.detected_at)` liefert die letzte Sync-Zeit. Cron läuft alle
3 Tage — Worst-Case 3 Tage Drift. Bei Wochen-Cadence (Sonntag-only) wäre
Worst-Case 7 Tage.

Für Ranking ist Frische-Konsistenz **innerhalb eines Pairs** kritisch:
wenn DE Donnerstag und US Sonntag synchronisiert wurde, ist der DE/US-
Vergleich verzerrt. Das aktuelle 3-Tage-Cron mit Run-Index-Drittel
**rotiert die B-Class** über drei Läufe — d. h. ein B-Class-Channel kann
3+3+3=9 Tage zwischen Syncs liegen. Major-Studios sollten in **A-Class**
landen (Posts-Threshold ≥ 1 in 30d), das prüft `cron_channel_selection.py`
automatisch.

→ **Vor Ranking-Sprint:** Live-Check, dass alle DE+US-Channels der sechs
aktiven Pairs in A-Class sind. Sonst sind Top-Listen bei einigen Pairs
3-9 Tage alt. **Wolf-Ping-Material falls A-Class fehlt.**

### Felder, die in der bestehenden Ranking-Spec aus dem Handover möglicherweise fehlen

Wolfs Roadmap nennt eine Ranking-Spec aus Section 11 des Handovers — die
liegt mir hier nicht vor. Aus den vorhandenen Daten ergeben sich folgende
**fehlende oder schwache Felder**:

| Feld | Status | Notwendig für Ranking? |
|---|---|---|
| `follower_count` (per Channel) | nicht persistiert (in TT-`raw_payload`) | nötig für Aktivierungs-Rate-Normalisierung |
| `is_short_form` (Reels/Shorts vs. lang) | impliziert über `duration_seconds < 60` | leicht ableitbar |
| `paid_promoted` Flag | nicht in Apify-Feldern | nicht notwendig für V1 |
| `ad_disclosure` / Sponsored-Indikator | nicht in Apify | nicht notwendig für V1 |
| Channel-Tier (P0/P1/P2) | vorhanden in `quality_tier` | als Sortier-Tiebreaker nutzbar |
| Asset-Type (Trailer/Teaser/Poster/...) | vorhanden in `asset.asset_type` | sinnvoll für **typ-getrenntes** Ranking ("Top Trailer", "Top Poster") |

## Inkonsistenzen / Lücken

1. **Engagement-Rate vs. -Sum**: Code aktuell konsequent `_engagement_sum`,
   Branche eher Rate. Beide haben Berechtigung, sollten nicht parallel
   eingeführt werden ohne Decision.
2. **YouTube ohne shares/saves**: jede Cross-Plattform-Top-Liste muss das
   adressieren.
3. **Follower-Count fehlt in der Persistierung**: ohne Backfill kein
   stabiler "/Follower"-Vergleich. Apify liefert es im `raw_payload`, ein
   Migrations-Skript könnte das aus den letzten N Posts pro Channel ziehen
   — **dokumentieren, nicht ausführen** (Pre-Commit 1).
4. **`avg_engagement` im `ChannelStats` ist Sum/Count** — keine Rate. Wenn
   Aktivierungs-Rate eingeführt wird, muss `avg_activation_rate` daneben.
5. **TT-Views ≠ IG-Plays ≠ YT-Views** strukturell. Plattform-Mix in einem
   einzigen Top-N ist immer schief.

## Risiken

- **Falsches Ranking-Signal in Production**: ein Posting mit 1 Mio Views
  und 3.000 Likes (TT-Reichweiten-Schuss) wird heute als "top" sortiert
  und bekommt vom LLM eine Lern-Empfehlung — die statistisch nicht trägt.
  Kein Bug, aber sicht-irrelevant.
- **Follower-Count-Backfill** ist eine notwendige One-Off-Schreibaktion.
  Kein automatisierter Pfad heute, also als Sprint-Prep einplanen.
- **Premature Multi-Plattform-Top-Liste** würde YT systematisch
  benachteiligen.

## Empfehlung für Sprint-Planung

1. **Ranking-Sprint V1 als plattform-getrennt anlegen** (TT, IG, YT
   separat). Spart Normalisierungs-Diskussion, lässt sich später mergen.
2. **Wolf-Decision zur Aktivierungs-Rate-Formel** (siehe `06`): bevor
   irgendetwas anderes passiert.
3. **Follower-Count-Backfill** als Pre-Sprint-Task (kein Sprint, ein
   Skript-Lauf): aus den letzten 5 Posts pro Channel den Wert in einer
   neuen Spalte `channel.followers_at_last_sync` festschreiben. Ohne das
   ist keine Normalisierung möglich.
4. **`avg_activation_rate` als zweites Feld in `ChannelStats`**, parallel
   zu `avg_engagement`. Beide sind kostengünstig zu berechnen und geben
   dem LLM mehr Anker.
5. **Asset-Type-getrenntes Ranking als V2** — für GF-Briefing-Update
   relevanter als für Ranking-Sprint.
