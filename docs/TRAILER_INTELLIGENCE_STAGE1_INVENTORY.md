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

> **Korrektur 06.08.2026 (nachträglich).** Die erste Fassung dieses Abschnitts nannte
> ~$20 und hatte dabei den **Sonnet-Vision-Call übersehen**, den `analyze_post` als
> Schritt 2 ausführt. Es sind drei Modellaufrufe pro Post, nicht zwei — und der
> Vision-Call ist der teuerste davon. Die korrigierten Zahlen stehen unten.

`analyze_post` macht pro Post bis zu drei Aufrufe. Modelle laut `app/config.py`:
Sonnet (`claude-sonnet-5`) für Vision und purpose/lifecycle, Haiku
(`claude-haiku-4-5-20251001`) für format/tone.

| Call | Input (~Token) | Output (~Token) | Kosten |
|---|---:|---:|---:|
| Sonnet Vision (Bildbeschreibung) | ~1.770 | ~130 | **~$0,0073** |
| Sonnet (purpose/lifecycle) | ~537 | ~40 | ~$0,0022 |
| Haiku (format/tone) | ~455 | ~40 | ~$0,0007 |

Der Vision-Call allein ist **~72 % der Kosten pro Post** — ein Bild schlägt mit
~1,6k Input-Token zu Buche, ein Caption-Prompt mit ~0,5k.

### Zwei Varianten

| Variante | pro Post | 6.703 Posts | liefert |
|---|---:|---:|---|
| **Text-only** (`skip_vision`) | ~$0,0029 | **~$19** | format, tone, purpose, lifecycle_stage |
| Vollständig (mit Vision) | ~$0,0101 | ~$56–68 | zusätzlich `vision_description` |

**Empfehlung: text-only.** Der Konsument, dem heute die Abdeckung fehlt — der
Empfehlungs-Baustein im `insight_engine` — liest ausschließlich
`analysis['format']` und `analysis['lifecycle_stage']`. `vision_description` wird
von ihm nie gelesen. Die drei Viertel Mehrkosten kaufen also nichts, was der
aktuelle Engpass braucht.

Umgesetzt als `analyze_post(..., skip_vision=True)`; der Default bleibt `False`,
damit der manuelle Admin-Endpunkt unverändert die volle Pipeline fährt.

## 6. Fazit

| Baustein | Stand | Bewertung |
|---|---|---|
| Videoposts mit Dauer + Reichweite | ≈5.800 | trägt Schritt 2 |
| TikTok-Musikdaten | 2.335 / 2.335 | trägt, ohne neue Erfassung |
| Format/Tone/Purpose/Lifecycle-Taxonomie | existiert, läuft, aber nur 12 % Abdeckung | Cron-Anbindung (umgesetzt, s. Abschnitt 7) |
| Bildanalyse (asset_type) | 76 % brauchbar, 1.005 reparierbare Fehlabrufe | mittelfristig verbessern |
| Hook-Typ / Schnittfrequenz | nicht erhoben | eigener Baustein, kostenpflichtig, außerhalb Stufe 1 |

**Empfehlung:** Stufe 1 auf der vorhandenen `Post.analysis`-Taxonomie aufsetzen statt
neu zu entwerfen. Offen bleibt der Termin mit dem Trailerhaus-Team — als
**Validierung** der bestehenden Format/Tone-Taxonomie, nicht als Neuentwicklung, und
mit der Hook-Typ-Lücke (Abschnitt 4) als explizitem Agenda-Punkt, damit dort keine
Taxonomie für ein Signal entsteht, das wir aktuell nicht erheben können.

## 7. Umsetzung: Cron-Anbindung statt Einmal-Backfill

Die naheliegende Lösung wäre ein einmaliger Backfill-Lauf gewesen. Dagegen sprachen
zwei Dinge:

- Ein Skript dafür **existierte bereits** (`scripts/backfill_post_analyzer.py`, mit
  Dry-Run, Pair-Filter und Resume-Sicherheit). Es wurde nur nie flächendeckend
  ausgeführt, weil es eine Railway-Shell verlangt — in einem Browser-Workflow eine
  echte Hürde. Ein zweites Skript hätte dasselbe Problem gehabt.
- Ein Einmal-Lauf löst das Problem einmal. Ohne Automatisierung wäre die Abdeckung
  Woche für Woche wieder abgesunken.

Deshalb ist der Analyzer jetzt eine reguläre Stage im Wochen-Cron
(`_run_post_analysis_backlog` in `app/api/cron.py`), gebaut nach dem Muster der
beiden bestehenden Vision-Stages daneben.

**Auswahl: newest-first.** Der Empfehlungs-Baustein aggregiert ein 7-Tage-Fenster.
Oldest-first hätte den Cap auf 90 Tage alte Zeilen verbraucht, während die aktuelle
Woche unklassifiziert bleibt — das Feature wäre trotz laufendem Backfill weiter
ausgehungert. Newest-first klassifiziert die laufende Woche im ersten Lauf; der
Alt-Bestand zieht mit dem Rest des Caps nach.

**Kostenbremsen, dreifach:**

| Bremse | Wirkung |
|---|---|
| `cron_post_analysis_max_posts_per_run` (800) | ~$2,30 pro Lauf, ~$10/Monat |
| `cron_post_analysis_skip_vision` (true) | text-only, spart ~72 % pro Post |
| `anthropic_monthly_budget_usd` (bestehend) | Pre-Flight bricht den Lauf vorher ab |

Bei ~600 neuen Posts pro Woche geht der laufende Zufluss in einem Lauf durch, und
es bleibt Kapazität für den Alt-Bestand.

### Railway-Variablen

Die Defaults sind bewusst so gewählt, dass **nichts gesetzt werden muss** — die Stage
läuft nach dem Deploy von allein. Zum Nachjustieren (Backend-Service → Variables,
Namen in Großschreibung, Pydantic-Settings mappt sie auf die Attribute oben):

| Variable | Default | Wann ändern |
|---|---|---|
| `CRON_POST_ANALYSIS_MAX_POSTS_PER_RUN` | `800` | Höher, um den Alt-Bestand schneller abzubauen (jede +100 ≈ +$0,29/Lauf). `0` schaltet die Stage ab. |
| `CRON_POST_ANALYSIS_SKIP_VISION` | `true` | Auf `false`, wenn `vision_description` flächendeckend gebraucht wird — verdreifacht die Kosten dieser Stage. |

Kontrolle nach dem ersten Lauf: `GET /api/admin/cron/runs` → `summary.post_analysis`
zeigt `selected`, `analyzed`, `errors` und `estimated_cost_usd`.

**Fehlerverhalten:** Ein einzelner fehlgeschlagener Post erhöht einen Zähler und der
Lauf geht weiter. Ein Anthropic-Auth-Fehler stoppt die Stage sofort — er würde sich
für jeden Folge-Post identisch wiederholen und den Cap sinnlos verbrauchen. Fehlt der
Anthropic-Key ganz (Staging), wird die Stage sauber übersprungen statt zu scheitern.
Das Ergebnis landet unter `summary["post_analysis"]` im CronRun.

Für gezielte Nachläufe steht das bestehende Skript weiterhin bereit, jetzt zusätzlich
mit `--skip-vision`.

## 8. Schritt 2: Muster-Aggregation

`app/services/trailer_patterns.py`, abrufbar über
`GET /api/admin/trailer-patterns?window_days=90&market=DE`. Rein lesend, kein
Modell-Aufruf, kein Budget-Effekt.

**Abgrenzung zum bestehenden Empfehlungs-Baustein** — der bleibt unverändert, er
beantwortet eine andere Frage:

| | Empfehlungs-Baustein | Muster-Aggregation |
|---|---|---|
| Zuschnitt | ein Pair (Studio DE+US) | ganzer Bestand, optional je Markt |
| Fenster | 7 Tage | 90 Tage (parametrisierbar) |
| Zweck | „diese Woche auffällig" | stabiles Strukturmuster |
| Baseline | Median des Pairs | Median des **Kanals** |

**Kanal-Normierung** ist der methodische Kern und behebt den Vorbehalt aus
Abschnitt 2. Statt Roh-Reichweiten zu mitteln (was Kanalgröße misst, nicht
Kreativ-Wirkung), bekommt jeder Post einen Lift:

```
lift = activation_rate(post) / median(activation_rate aller Posts desselben Kanals)
```

Ein Lift von 2,0 heißt „doppelt so gut wie dieser Kanal üblicherweise abschneidet" —
vergleichbar zwischen Netflix und einem kleinen Kanal.

**Fünf Dimensionen:** `format`, `tone`, `lifecycle_stage`, `duration_bucket`,
`music_kind`. Die letzte wertet erstmals die TikTok-Musikdaten aus (Abschnitt 1,
100 % Abdeckung) — Original-Sound vs. lizensierter Track.

**Ehrlichkeits-Regeln**, übernommen vom Empfehlungs-Baustein plus zwei neue:

1. Mindest-Stichprobe 5 Posts je Zelle
2. **Mindest-Kanalzahl 3 je Zelle** (neu) — korpusweit könnte sonst ein einzelner
   Vielposter im Alleingang ein „Muster" erzeugen
3. Effektstärke > 1,5× (over) bzw. < 0,5× (under)
4. Konfidenz ≥ 0,7 — aber **nur für modell-erzeugte Dimensionen** (neu).
   `duration_bucket` und `music_kind` sind gemessen, nicht klassifiziert; sie durch
   den Konfidenz-Filter zu schicken würde bei 12 % Abdeckung sieben Achtel
   brauchbarer Daten wegwerfen, ohne die Qualität zu erhöhen
5. Kanäle mit < 4 Posts im Fenster liefern keine belastbare Baseline und werden
   übersprungen

Zellen, die eine Schwelle reißen, werden mit `verdict="insufficient"` und Begründung
ausgegeben statt weggefiltert — eine dünne Datenlage ist ein Befund.

### Interpretationsfalle beim Lesen

Der Lift misst gegen den Median des Kanals, also gegen dessen **eigenen Output-Mix**.
Macht ein Format die Mehrheit der Posts eines Kanals aus, bestimmt es den Median mit
und kann rechnerisch kaum darüber liegen. Das Signal erscheint dann gespiegelt: nicht
„Trailer over", sondern „alles andere under". Wer nur auf `verdict == "over"` schaut,
übersieht diesen Fall — **immer beide Richtungen einer Dimension zusammen lesen.**

### Offener Punkt: Musik-Feldstruktur nicht verifiziert

Die Extraktion liest `raw_payload['_creative_radar_music']['musicOriginal']`. Dieses
Feld ist Apify-seitig nicht vertraglich zugesichert; die Extraktion behandelt bool,
String und Fehlen defensiv und liefert im Zweifel `unknown` statt zu raten. Ob das
Feld in den echten Daten so heißt, ließ sich ohne Produktionszugriff nicht prüfen.
Gegenprobe:

```sql
SELECT raw_payload -> '_creative_radar_music' AS musik
FROM creative_radar.post
WHERE platform = 'tiktok' AND raw_payload -> '_creative_radar_music' <> 'null'::jsonb
LIMIT 3;
```

Liefert das etwas anderes als einen `musicOriginal`-Schlüssel, landet alles in
`music_kind = "unknown"` — sichtbar, aber nutzlos. Dann ist der Extraktor
anzupassen.
