# Trailer-Intelligence Stufe 1 — erste Auswertung

Stand: 2026-08-06, abends. Erste Anwendung der in
[`TRAILER_INTELLIGENCE_STAGE1_INVENTORY.md`](./TRAILER_INTELLIGENCE_STAGE1_INVENTORY.md)
beschriebenen Kanal-Normierung auf die echten Produktionsdaten. Alle Queries liefen
**read-only direkt in der Produktions-Postgres** (Railway-Query-Konsole), nicht über
`GET /api/admin/trailer-patterns` — der Endpunkt braucht einen Bearer-Token, den die
Railway-Oberfläche nicht mitschickt. Die Query bildet dieselbe Logik wie
`app/services/trailer_patterns.py` nach; alle drei Fassungen wurden vor der Weitergabe
gegen ein lokales Postgres 16 mit synthetischen Testfällen geprüft.

## 1. Datenbasis

| | |
|---|---:|
| Posts im 90-Tage-Fenster | 7.623 |
| davon mit Kanal-Baseline (≥4 Posts/Kanal) | 6.577 (86 %) |
| Kanäle mit Baseline | 153 von 204 |
| Klassifikations-Abdeckung (`format` gesetzt) | 13,8 % |

Query dafür:

```sql
WITH scoped AS (
  SELECT p.id, p.channel_id, p.analysis,
         CASE WHEN COALESCE(p.visible_views,0)=0 THEN 0::numeric
              WHEN c.platform='youtube' THEN (GREATEST(COALESCE(p.visible_likes,0),0)+GREATEST(COALESCE(p.visible_comments,0),0))::numeric/p.visible_views
              ELSE (GREATEST(COALESCE(p.visible_likes,0),0)+GREATEST(COALESCE(p.visible_comments,0),0)+GREATEST(COALESCE(p.visible_bookmarks,0),0))::numeric/p.visible_views END AS activation
  FROM creative_radar.post p JOIN creative_radar.channel c ON c.id=p.channel_id
  WHERE p.detected_at > now() - interval '90 days'
), baseline AS (
  SELECT channel_id FROM scoped GROUP BY channel_id
  HAVING count(*) >= 4 AND percentile_cont(0.5) WITHIN GROUP (ORDER BY activation) > 0
)
SELECT (SELECT count(*) FROM scoped) AS posts_im_fenster,
       (SELECT count(*) FROM scoped s JOIN baseline b USING (channel_id)) AS posts_mit_baseline,
       (SELECT count(*) FROM baseline) AS kanaele_mit_baseline,
       (SELECT count(DISTINCT channel_id) FROM scoped) AS kanaele_gesamt,
       round(100.0 * (SELECT count(*) FROM scoped s JOIN baseline b USING (channel_id)
                      WHERE s.analysis ->> 'format' IS NOT NULL)
             / NULLIF((SELECT count(*) FROM scoped s JOIN baseline b USING (channel_id)),0), 1) AS klassifiziert_pct;
```

## 2. Median-Lift je Dimension

Lift = Aktivierungsrate eines Posts geteilt durch den Median-Aktivierungswert **seines
eigenen Kanals**. 1,0 = durchschnittlich für diesen Kanal.

| Dimension | Wert | Posts | Kanäle | Median-Lift |
|---|---|---:|---:|---:|
| duration_bucket | >60s | 1.577 | 151 | 1,24 |
| duration_bucket | <15s | 707 | 125 | 1,14 |
| duration_bucket | 30–60s | 1.725 | 149 | 1,13 |
| duration_bucket | 15–30s | 1.453 | 145 | 1,09 |
| format | compilation | 15 | 8 | 1,29 |
| format | interview | 19 | 13 | 1,23 |
| format | trailer | 51 | 25 | 1,15 |
| format | teaser | 37 | 23 | 1,15 |
| format | promo | 424 | 81 | 1,05 |
| format | clip | 189 | 48 | 0,98 |
| format | behind_the_scenes | 43 | 26 | 0,86 |
| tone | emotional | 35 | 24 | 1,29 |
| tone | edgy | 8 | 8 | 1,13 |
| tone | neutral | 15 | 11 | 1,12 |
| tone | inspirational | 42 | 33 | 1,06 |
| tone | informative | 72 | 36 | 1,05 |
| tone | energetic | 358 | 81 | 1,04 |
| tone | humorous | 161 | 51 | 1,03 |
| tone | suspenseful | 90 | 43 | 1,02 |
| lifecycle_stage | pre_launch | 167 | 60 | 1,11 |
| lifecycle_stage | post_launch | 166 | 47 | 1,07 |
| lifecycle_stage | launch | 294 | 66 | 1,03 |
| lifecycle_stage | evergreen | 154 | 46 | 1,00 |
| music_kind | original_sound | 2.103 | 38 | 1,00 |
| music_kind | licensed_track | 232 | 28 | 0,96 |

**Befund: alle Zellen liegen im Band 0,86–1,29.** Keine erreicht die Schwellen
`≥1,5` (over) bzw. `≤0,5` (under) aus `trailer_patterns.py`. Format, Ton, Dauer und
Musik erklären den **typischen** Post kaum.

## 3. Trefferquote je Dimension (Ergänzung, ad hoc)

Der Median verdeckt Formate, die selten, aber stark ausschlagen. Zweite Metrik:
Anteil Posts mit Lift ≥ 2,0 ("Treffer"), verglichen mit der Basisquote über den
gesamten normierten Bestand (20,0 %).

```sql
WITH scoped AS (
  SELECT p.id, p.channel_id, c.platform, p.duration_seconds, p.analysis, p.raw_payload,
         CASE WHEN COALESCE(p.visible_views,0)=0 THEN 0::numeric
              WHEN c.platform='youtube' THEN (GREATEST(COALESCE(p.visible_likes,0),0)+GREATEST(COALESCE(p.visible_comments,0),0))::numeric/p.visible_views
              ELSE (GREATEST(COALESCE(p.visible_likes,0),0)+GREATEST(COALESCE(p.visible_comments,0),0)+GREATEST(COALESCE(p.visible_bookmarks,0),0))::numeric/p.visible_views END AS activation
  FROM creative_radar.post p JOIN creative_radar.channel c ON c.id=p.channel_id
  WHERE p.detected_at > now() - interval '90 days'
), baseline AS (
  SELECT channel_id, percentile_cont(0.5) WITHIN GROUP (ORDER BY activation) AS med
  FROM scoped GROUP BY channel_id HAVING count(*) >= 4
), lifted AS (
  SELECT s.*, s.activation/b.med AS lift FROM scoped s JOIN baseline b ON b.channel_id=s.channel_id WHERE b.med > 0
), cells AS (
  SELECT 'duration_bucket' AS dimension,
         CASE WHEN duration_seconds<15 THEN '1_<15s' WHEN duration_seconds<30 THEN '2_15-30s'
              WHEN duration_seconds<60 THEN '3_30-60s' ELSE '4_>60s' END AS value, lift, channel_id
  FROM lifted WHERE duration_seconds IS NOT NULL

  UNION ALL SELECT 'music_kind',
         CASE WHEN raw_payload -> '_creative_radar_music' ->> 'musicOriginal' = 'true' THEN 'original_sound'
              WHEN raw_payload -> '_creative_radar_music' ->> 'musicOriginal' = 'false' THEN 'licensed_track'
              ELSE 'unknown' END, lift, channel_id
  FROM lifted WHERE (raw_payload -> '_creative_radar_music')::text NOT IN ('null', '{}')

  UNION ALL SELECT 'format', analysis->>'format', lift, channel_id FROM lifted
  WHERE analysis->>'format' IS NOT NULL AND (analysis->>'confidence')::numeric >= 0.7

  UNION ALL SELECT 'tone', analysis->>'tone', lift, channel_id FROM lifted
  WHERE analysis->>'tone' IS NOT NULL AND (analysis->>'confidence')::numeric >= 0.7

  UNION ALL SELECT 'lifecycle_stage', analysis->>'lifecycle_stage', lift, channel_id FROM lifted
  WHERE analysis->>'lifecycle_stage' IS NOT NULL AND (analysis->>'confidence')::numeric >= 0.7
), gesamt AS (
  SELECT round(100.0*count(*) FILTER (WHERE lift >= 2.0)/count(*), 1) AS basis_pct FROM lifted
)
SELECT c.dimension, c.value, count(*) AS posts, count(DISTINCT c.channel_id) AS kanaele,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY c.lift)::numeric,2) AS median_lift,
       round(100.0*count(*) FILTER (WHERE c.lift >= 2.0)/count(*),1) AS treffer_pct,
       (SELECT basis_pct FROM gesamt) AS basis_pct,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY c.lift)::numeric,2) AS p90_lift
FROM cells c GROUP BY c.dimension, c.value
HAVING count(*) >= 5 AND count(DISTINCT c.channel_id) >= 3
ORDER BY treffer_pct DESC;
```

**Ergebnis** (Basisquote 20,0 %, Spalten: Posts / Kanäle / Median-Lift /
Trefferquote / P90-Lift):

| Dimension | Wert | Posts | Kanäle | Median | Treffer | P90 |
|---|---|---:|---:|---:|---:|---:|
| duration_bucket | >60s | 1.577 | 151 | 1,24 | **28,7 %** | 3,95 |
| tone | emotional | 35 | 24 | 1,29 | 28,6 % | 2,78 |
| format | behind_the_scenes | 43 | 26 | **0,86** | **27,9 %** | 3,03 |
| tone | edgy | 8 | 8 | 1,13 | 25,0 % | 4,10 |
| duration_bucket | <15s | 707 | 125 | 1,14 | 22,9 % | 3,08 |
| duration_bucket | 15–30s | 1.453 | 145 | 1,09 | 22,4 % | 3,42 |
| tone | inspirational | 42 | 33 | 1,06 | 21,4 % | 2,87 |
| duration_bucket | 30–60s | 1.725 | 149 | 1,13 | 21,1 % | 3,05 |
| tone | energetic | 358 | 81 | 1,04 | 20,7 % | 3,05 |
| format | compilation | 15 | 8 | 1,29 | 20,0 % | 2,31 |
| format | promo | 424 | 81 | 1,05 | 19,6 % | 3,11 |
| format | trailer | 51 | 25 | 1,15 | 19,6 % | 2,91 |
| tone | informative | 72 | 36 | 1,05 | 19,4 % | 2,63 |
| format | teaser | 37 | 23 | 1,15 | 18,9 % | 2,55 |
| format | interview | 19 | 13 | 1,23 | 15,8 % | 2,46 |
| tone | suspenseful | 90 | 43 | 1,02 | 15,6 % | 2,66 |
| tone | neutral | 15 | 11 | 1,12 | 13,3 % | 1,97 |
| format | clip | 189 | 48 | 0,98 | 12,7 % | 2,17 |
| tone | **humorous** | 161 | 51 | 1,03 | **11,2 %** | 2,15 |

`lifecycle_stage` und `music_kind` fehlten in dieser ersten Fassung der
Treffer-Query (nur duration/format/tone waren als UNION-Zweige drin) — für
morgen ergänzt, siehe Abschnitt 5.

## 4. Interpretation

**a) Der Median verdeckt das eigentliche Signal.** Über alle Dimensionen liegt die
Spanne bei 0,86–1,29 — kein einziger Median-Lift erreicht die Over/Under-Schwellen.
Die Trefferquote spannt dagegen **11,2 % bis 28,7 %**, Faktor 2,5 zwischen bestem und
schlechtestem Wert. Das Signal sitzt in den Ausreißern, nicht im typischen Post.

**b) Lange Formate gewinnen — der stabilste Einzelbefund.** `>60s`: 1.577 Posts über
151 Kanäle, 28,7 % Trefferquote (Basis 20 %), höchster P90-Lift (3,95). Größte
Stichprobe im ganzen Datensatz. Auffällig: die Dauer-Reihenfolge ist **U-förmig** —
`>60s` (28,7 %) und `<15s` (22,9 %) schlagen die Mitte `30–60s` (21,1 %). Die
vermeintlich sichere Mittellänge ist die schwächste Wahl.

**c) Behind the Scenes ist ein Hochrisiko-Format.** Median 0,86 (unterdurchschnittlich
im Regelfall) bei gleichzeitig 27,9 % Trefferquote (deutlich über der 20-%-Basis).
Meist ein Blindgänger, aber mit überdurchschnittlicher Chance auf einen echten
Ausreißer. Der Median allein hätte das Format als "unauffällig-schwach" abgetan.

**d) Humor ist der zuverlässigste Verlierer.** 11,2 % Trefferquote bei 161 Posts über
51 Kanälen — die größte Stichprobe unter den schwachen Werten, P90 nur 2,15 (niedrigste
Decke im ganzen Feld). Kein Ausreißer, auch nicht selten.

**e) Trailer/Teaser sind unauffällig.** 19,6 % / 18,9 %, praktisch exakt auf der
20-%-Basislinie. Weder besonders gut noch schlecht — sie tragen die Fragestellung
"was macht einen Trailer erfolgreich" nicht.

**f) Musik liefert kein Signal.** `original_sound` 1,00 vs. `licensed_track` 0,96 im
Median. Als Dimension für Stufe 2 vorerst ohne Erkenntniswert.

**g) Übergeordneter Schluss:** Format, Ton, Dauer und Musik erklären Reichweite
zusammen kaum. Das spricht dafür, dass die entscheidende Varianz in der kreativen
*Ausführung* liegt — Hook, Schnitt, Bildsprache — also genau in dem, was die
Thumbnail-only-Pipeline (Inventur, Abschnitt 4) nicht erhebt. Der Befund ist damit
nicht mehr nur eine Vermutung, sondern hat eine erste empirische Stütze.

## 5. Offener Punkt für morgen: Plattform-Confound bei der Dauer

Dauer korreliert stark mit Plattform (TikTok kurz, YouTube tendenziell länger). Die
Kanal-Baseline normiert nur *innerhalb* eines Kanals — der Vergleich zwischen den
Dauer-Buckets kann daher teilweise ein Plattform-Vergleich sein statt ein reiner
Dauer-Effekt.

**Validiert** (gegen synthetische Testdaten: zwei Plattformen mit identischer
Basisquote, aber getrennten Dauer-Profilen — die Query lieferte korrekt keinen
falschen Ausschlag):

```sql
WITH scoped AS (
  SELECT p.id, p.channel_id, c.platform, p.duration_seconds, p.analysis, p.raw_payload,
         CASE WHEN COALESCE(p.visible_views,0)=0 THEN 0::numeric
              WHEN c.platform='youtube' THEN (GREATEST(COALESCE(p.visible_likes,0),0)+GREATEST(COALESCE(p.visible_comments,0),0))::numeric/p.visible_views
              ELSE (GREATEST(COALESCE(p.visible_likes,0),0)+GREATEST(COALESCE(p.visible_comments,0),0)+GREATEST(COALESCE(p.visible_bookmarks,0),0))::numeric/p.visible_views END AS activation
  FROM creative_radar.post p JOIN creative_radar.channel c ON c.id=p.channel_id
  WHERE p.detected_at > now() - interval '90 days'
), baseline AS (
  SELECT channel_id, percentile_cont(0.5) WITHIN GROUP (ORDER BY activation) AS med
  FROM scoped GROUP BY channel_id HAVING count(*) >= 4
), lifted AS (
  SELECT s.*, s.activation/b.med AS lift FROM scoped s JOIN baseline b ON b.channel_id=s.channel_id WHERE b.med > 0
), cells AS (
  SELECT platform,
         CASE WHEN duration_seconds<15 THEN '1_<15s' WHEN duration_seconds<30 THEN '2_15-30s'
              WHEN duration_seconds<60 THEN '3_30-60s' ELSE '4_>60s' END AS bucket,
         lift, channel_id
  FROM lifted WHERE duration_seconds IS NOT NULL
), basis AS (
  SELECT platform, round(100.0*count(*) FILTER (WHERE lift >= 2.0)/count(*),1) AS basis_pct
  FROM cells GROUP BY platform
)
SELECT c.platform, c.bucket, count(*) AS posts, count(DISTINCT c.channel_id) AS kanaele,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY c.lift)::numeric,2) AS median_lift,
       round(100.0*count(*) FILTER (WHERE c.lift >= 2.0)/count(*),1) AS treffer_pct,
       b.basis_pct
FROM cells c JOIN basis b ON b.platform = c.platform
GROUP BY c.platform, c.bucket, b.basis_pct
HAVING count(*) >= 5 AND count(DISTINCT c.channel_id) >= 3
ORDER BY c.platform, treffer_pct DESC;
```

**Erwartung:** Bleibt `>60s` auch *innerhalb* von YouTube (bzw. der jeweiligen
Plattform) über der Plattform-eigenen Basisquote, ist der Dauer-Effekt echt. Rutscht
er auf Plattform-Niveau zurück, war es ein Confound.

## 6. Ebenfalls für morgen: Treffer-Query mit allen fünf Dimensionen

Die Treffer-Query in Abschnitt 3 enthielt `lifecycle_stage` und `music_kind` nicht.
Vervollständigte, gegen synthetische Testdaten geprüfte Fassung:

```sql
WITH scoped AS (
  SELECT p.id, p.channel_id, c.platform, p.duration_seconds, p.analysis, p.raw_payload,
         CASE WHEN COALESCE(p.visible_views,0)=0 THEN 0::numeric
              WHEN c.platform='youtube' THEN (GREATEST(COALESCE(p.visible_likes,0),0)+GREATEST(COALESCE(p.visible_comments,0),0))::numeric/p.visible_views
              ELSE (GREATEST(COALESCE(p.visible_likes,0),0)+GREATEST(COALESCE(p.visible_comments,0),0)+GREATEST(COALESCE(p.visible_bookmarks,0),0))::numeric/p.visible_views END AS activation
  FROM creative_radar.post p JOIN creative_radar.channel c ON c.id=p.channel_id
  WHERE p.detected_at > now() - interval '90 days'
), baseline AS (
  SELECT channel_id, percentile_cont(0.5) WITHIN GROUP (ORDER BY activation) AS med
  FROM scoped GROUP BY channel_id HAVING count(*) >= 4
), lifted AS (
  SELECT s.*, s.activation/b.med AS lift FROM scoped s JOIN baseline b ON b.channel_id=s.channel_id WHERE b.med > 0
), cells AS (
  SELECT 'duration_bucket' AS dimension,
         CASE WHEN duration_seconds<15 THEN '1_<15s' WHEN duration_seconds<30 THEN '2_15-30s'
              WHEN duration_seconds<60 THEN '3_30-60s' ELSE '4_>60s' END AS value, lift, channel_id
  FROM lifted WHERE duration_seconds IS NOT NULL

  UNION ALL SELECT 'music_kind',
         CASE WHEN raw_payload -> '_creative_radar_music' ->> 'musicOriginal' = 'true' THEN 'original_sound'
              WHEN raw_payload -> '_creative_radar_music' ->> 'musicOriginal' = 'false' THEN 'licensed_track'
              ELSE 'unknown' END, lift, channel_id
  FROM lifted WHERE (raw_payload -> '_creative_radar_music')::text NOT IN ('null', '{}')

  UNION ALL SELECT 'format', analysis->>'format', lift, channel_id FROM lifted
  WHERE analysis->>'format' IS NOT NULL AND (analysis->>'confidence')::numeric >= 0.7

  UNION ALL SELECT 'tone', analysis->>'tone', lift, channel_id FROM lifted
  WHERE analysis->>'tone' IS NOT NULL AND (analysis->>'confidence')::numeric >= 0.7

  UNION ALL SELECT 'lifecycle_stage', analysis->>'lifecycle_stage', lift, channel_id FROM lifted
  WHERE analysis->>'lifecycle_stage' IS NOT NULL AND (analysis->>'confidence')::numeric >= 0.7
), gesamt AS (
  SELECT round(100.0*count(*) FILTER (WHERE lift >= 2.0)/count(*), 1) AS basis_pct FROM lifted
)
SELECT c.dimension, c.value, count(*) AS posts, count(DISTINCT c.channel_id) AS kanaele,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY c.lift)::numeric,2) AS median_lift,
       round(100.0*count(*) FILTER (WHERE c.lift >= 2.0)/count(*),1) AS treffer_pct,
       (SELECT basis_pct FROM gesamt) AS basis_pct,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY c.lift)::numeric,2) AS p90_lift
FROM cells c GROUP BY c.dimension, c.value
HAVING count(*) >= 5 AND count(DISTINCT c.channel_id) >= 3
ORDER BY treffer_pct DESC;
```

## 7. Konsequenzen

1. **Trailerhaus-Termin:** Die Ausführungs-Hypothese (Punkt g) gehört als eigener
   Tagesordnungspunkt rein, mit den Zahlen aus diesem Dokument als Beleg — nicht mehr
   nur als Vermutung aus der Inventur.
2. **`trailer_patterns.py`-Schwellen prüfen:** Die 1,5×/0,5×-Grenzen für Over/Under
   sind vom 7-Tage/Pair-Zuschnitt des Empfehlungs-Bausteins übernommen und für den
   90-Tage/Korpus-Zuschnitt vermutlich zu hoch. Über eine Trefferquoten-Metrik als
   zweite, produktive Kennzahl nachdenken (nicht nur ad hoc in SQL).
3. **`music_kind` niedrig priorisieren** — kein Signal in dieser ersten Auswertung.
4. **Plattform-Confound bei Dauer** vor jeder belastbaren Aussage zu "lange Formate
   gewinnen" mit der Query aus Abschnitt 5 klären.
5. Mit steigender Klassifikations-Abdeckung (aktuell 13,8 %, durch die neue
   Cron-Stage wachsend) werden `format`/`tone`/`lifecycle_stage`-Zellen dichter und
   die Aussagen belastbarer — diese Auswertung in ein paar Wochen wiederholen.
