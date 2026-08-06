# Trailer-Intelligence Stufe 1 — Schritt 1: Daten-Inventur

Stand: 2026-08-06. Grundlage: `creative_radar.post` / `creative_radar.asset` in der
Produktions-DB, Fenster `detected_at > now() - interval '90 days'` (7.623 Posts).

## 1. Grundmenge

| Plattform | Posts | mit `duration_seconds` | mit `visible_views` |
|---|---:|---:|---:|
| Instagram | 4.239 | 2.526 | 2.532 |
| TikTok | 2.335 | 2.248 | 2.335 |
| YouTube | 1.049 | 1.049 | 1.049 |
| **Summe** | **7.623** | **≈5.823** | **≈5.916** |

≈5.800 Videoposts mit Dauer *und* Reichweite in 90 Tagen — ausreichend für
Muster-Aggregation nach Markt/Genre/Dauer-Bucket.

TikTok-Musik-Metadaten (`raw_payload -> '_creative_radar_music'`): **2.335 / 2.335 —
100 % Abdeckung**. Track, Autor und Original-Sound-Flag liegen für jeden TikTok-Post
bereits vor, ohne neue Erfassung.

## 2. `Post.analysis` (Format/Tone/Purpose/Lifecycle)

`post_analyzer.py` schreibt pro Post ein `PostAnalysis`-Dict mit vier Feldern:

- `format`: trailer, teaser, clip, behind_the_scenes, interview, short, compilation, promo, other
- `tone`: energetic, emotional, humorous, suspenseful, informative, inspirational, edgy, neutral
- `purpose`: launch_announcement, release_week, ongoing_promotion, evergreen, audience_engagement, event_coverage, other
- `lifecycle_stage`: pre_launch, launch, post_launch, evergreen, unclear

**Wichtiger Messfehler, hier dokumentiert für spätere Abfragen:** `count(analysis)` in
Postgres zählt auch NULL-Objekte, weil `sa.JSON()` Python-`None` standardmäßig als
JSON-Skalar `null` persistiert (`none_as_null=False`), nicht als SQL-NULL. Die korrekte
Abfrage filtert auf den tatsächlichen Feldwert:

```sql
SELECT analysis->>'format' AS format, count(*) AS posts,
       round(avg(duration_seconds)) AS avg_dauer_s,
       round(avg(visible_views)) AS avg_views
FROM creative_radar.post
WHERE detected_at > now() - interval '90 days'
GROUP BY 1 ORDER BY 2 DESC;
```

Ergebnis: **920 von 7.623 Posts (12 %) tatsächlich klassifiziert.** Die Pipeline läuft
korrekt und wurde nie flächendeckend angewandt — sie hängt nur am manuellen
Admin-Endpunkt (`POST /api/admin/analyze/{channel_id}`), nicht am Wochen-Cron.

### Reichweite pro Format (n=920, nur Stichprobe — s. Vorbehalt unten)

| format | Posts | ⌀ Dauer (s) | ⌀ Views |
|---|---:|---:|---:|
| trailer | 51 | 99 | 632.923 |
| teaser | 41 | 67 | 265.839 |
| behind_the_scenes | 52 | 101 | 174.741 |
| other | 14 | 531 | 168.196 |
| interview | 19 | 148 | 141.598 |
| promo | 439 | 73 | 114.797 |
| clip | 279 | 204 | 69.389 |
| compilation | 17 | 604 | 14.520 |
| short | 8 | 190 | 8.008 |

Trailer/Teaser schlagen Clip/Compilation um Faktor 5–9. Ein Dauer-Effekt zeichnet sich
ab: Formate unter ~100 s performen durchweg besser als Formate über ~190 s.

**Vorbehalt:** Mittelwerte über eine 12-%-Stichprobe, keine Median-/Ausreißer-Kontrolle,
keine Normierung auf Kanal-Baseline (ein Studio-Kanal startet nicht bei denselben
Followern wie ein kleiner Clip-Kanal). Als Richtungsindikator belastbar, nicht als
Endergebnis. Schritt 2 muss beides herausrechnen.

## 3. Asset-Bildanalyse (`asset_type`, Standbild-basiert)

19 Werte, größte Klassen (Top 5 von 19, 90-Tage-Fenster):

| asset_type | Anzahl |
|---|---:|
| UNKNOWN | 2.060 |
| DISCOVERY | 931 |
| FRANCHISE_BRAND_POST | 817 |
| CTA_POST | 693 |
| CAST_POST | 590 |

Trailer-Familie (TRAILER + TEASER + TRAILER_DROP): 394 + 78 + 40 = **512** Assets.

`asset_type` ist als Trailer-Filter ungeeignet (24 % UNKNOWN) — `analysis->>'format'`
ist die bessere Filterspalte, sobald der Backfill (Abschnitt 5) gelaufen ist: volle
Abdeckung statt Bild-Heuristik, und es ist eine Post- statt Asset-Eigenschaft.

### UNKNOWN-Diagnose (`visual_analysis_status` bei `asset_type = UNKNOWN`)

| Status | Anzahl | Ursache |
|---|---:|---|
| fetch_failed | 1.005 | Bildabruf gescheitert (typ. abgelaufene CDN-URL) — Durchsatzproblem, reparierbar |
| analyzed | 929 | Modell hat aktiv "unknown" geliefert — Prompt-Problem |
| pending | 74 | noch nicht verarbeitet |
| no_source | 52 | kein Bild im raw_payload |

## 4. Was NICHT ableitbar ist: Hook-Typ (erste 3 Sekunden)

Sämtliche Bildanalyse läuft auf einem einzigen, plattformseitig gewählten Thumbnail —
nicht auf Frame 0. Kein Video wird je heruntergeladen oder verarbeitet. Damit sind zwei
der drei im Briefing genannten Signale technisch nicht erhebbar:

| Signal | Ableitbar? | Warum |
|---|---|---|
| Hook-Typ (erste 3 s) | ❌ | Thumbnail ≠ Frame 0, kein Videostream vorhanden |
| Schnittfrequenz | ❌ | Braucht den Videostream |
| Musik-Einsatz | ⚠️ nur TikTok | `musicMeta` vorhanden; YouTube/Instagram liefern nichts |

Neue Erfassung (Video-Download der ersten Sekunden + Analyse) ist ein separater,
kostenpflichtiger Baustein und nicht Teil dieser Inventur.

## 5. Backfill-Kostenschätzung (`post_analyzer` auf die 6.703 unklassifizierten Posts)

Modelle laut `app/config.py`: Haiku (`claude-haiku-4-5-20251001`) für format/tone,
Sonnet (`claude-sonnet-5`) für purpose/lifecycle — ein Call pro Modell pro Post.

Gemessene Prompt-Länge (System + few-shots + Caption-Template, ohne Caption-Inhalt):

| Call | Input (~Token) | Output (~Token, geschätzt) |
|---|---:|---:|
| Haiku (format/tone) | ~455 | ~40 |
| Sonnet (purpose/lifecycle) | ~537 | ~40 |

Bei den konfigurierten Preisen ($0.001/$0.005 pro 1k Haiku, $0.003/$0.015 pro 1k
Sonnet):

- Kosten pro Post: **~$0.0029** (Haiku ~$0.00066 + Sonnet ~$0.00221)
- 6.703 Posts: **~$19,20** (~€17,70 bei aktuellem Kurs)
- mit 15 % Puffer für Retry-bei-Invalid-JSON: **~$22**

**Größenordnung: unter $25 einmalig.** Kein limitierender Faktor für die Entscheidung.

## 6. Fazit

| Baustein | Stand | Bewertung |
|---|---|---|
| Videoposts mit Dauer + Reichweite | ≈5.800 | trägt Schritt 2 |
| TikTok-Musikdaten | 2.335 / 2.335 | trägt, ohne neue Erfassung |
| Format/Tone/Purpose/Lifecycle-Taxonomie | existiert, läuft, aber nur 12 % Abdeckung | Backfill (~$20) + Cron-Anbindung nötig |
| Bildanalyse (asset_type) | 76 % brauchbar, 1.005 reparierbare Fehlabrufe | mittelfristig verbessern |
| Hook-Typ / Schnittfrequenz | nicht erhoben | eigener Baustein, kostenpflichtig, außerhalb Stufe 1 |

**Empfehlung:** Stufe 1 auf der vorhandenen `Post.analysis`-Taxonomie aufsetzen statt
neu zu entwerfen. Nächste Schritte vor Schritt 2 (Muster-Aggregation):

1. Backfill der 6.703 unklassifizierten Posts über `post_analyzer` (~$20, einmalig).
2. `post_analyzer` in den Wochen-Cron einhängen, damit neue Posts automatisch
   klassifiziert werden (aktuell nur manuell über den Admin-Endpunkt erreichbar).
3. Termin mit dem Trailerhaus-Team als **Validierung** der bestehenden
   Format/Tone-Taxonomie ansetzen, nicht als Neuentwicklung — mit der Hook-Typ-Lücke
   (Abschnitt 4) als expliziten Agenda-Punkt, damit dort keine Taxonomie für ein
   Signal entsteht, das wir aktuell nicht erheben können.
