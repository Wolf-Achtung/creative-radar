# Trailer-Intelligence Stufe 1 — erste Auswertung

Stand: 2026-08-06, abends; **Abschnitt 8 ergänzt am 07.08.2026** — er korrigiert den
Hauptbefund aus Abschnitt 4b. Erste Anwendung der in
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

> **Überholt durch Abschnitt 8 (07.08.2026).** Die Plattform-Aufteilung hat gezeigt,
> dass dieser Befund überwiegend ein verkleideter Plattform-Vergleich war. Der
> Dauer-Effekt existiert, aber nur auf YouTube — auf TikTok gar nicht. Auch die
> U-Form löst sich je Plattform auf. Der Absatz bleibt als Beleg dafür stehen, wie
> eine korpusweite Basisquote in die Irre führt.

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
4. ~~**Plattform-Confound bei Dauer** vor jeder belastbaren Aussage zu "lange Formate
   gewinnen" mit der Query aus Abschnitt 5 klären.~~ **Erledigt, siehe Abschnitt 8** —
   der Confound war real und hat den Befund gekippt.
5. Mit steigender Klassifikations-Abdeckung (aktuell 13,8 %, durch die neue
   Cron-Stage wachsend) werden `format`/`tone`/`lifecycle_stage`-Zellen dichter und
   die Aussagen belastbarer — diese Auswertung in ein paar Wochen wiederholen.

## 8. Auflösung des Plattform-Confounds (07.08.2026)

Die Query aus Abschnitt 5 lief read-only auf der Produktions-Postgres. Ergebnis in
zwölf Zeilen — und es kippt den stärksten Befund der ersten Auswertung.

### 8.1 Die Basisquoten liegen um Faktor vier auseinander

| Plattform | eigene Basisquote |
|---|---:|
| Instagram | 42,1 % |
| YouTube | 15,9 % |
| TikTok | 9,9 % |

Die korpusweite Basisquote von 20,0 %, gegen die Abschnitt 3 gemessen hat, ist damit
ein Mittelwert ohne Bedeutung. Sie liegt zwischen den Plattformen, entspricht keiner
von ihnen, und jede Zelle, die überproportional auf Instagram liegt, sieht dagegen
automatisch gut aus — ganz ohne inhaltlichen Effekt.

### 8.2 Der Dauer-Effekt ist plattformspezifisch

Vollständiges Ergebnis (Posts / Kanäle / Median-Lift / Trefferquote):

| Plattform | Bucket | Posts | Kanäle | Median | Treffer | eigene Basis |
|---|---|---:|---:|---:|---:|---:|
| Instagram | >60s | 631 | 78 | 1,84 | 46,9 % | 42,1 % |
| Instagram | <15s | 251 | 68 | 1,83 | 46,2 % | 42,1 % |
| Instagram | 15–30s | 572 | 76 | 1,71 | 41,3 % | 42,1 % |
| Instagram | 30–60s | 671 | 77 | 1,53 | 36,8 % | 42,1 % |
| YouTube | >60s | 406 | 37 | 1,23 | 21,4 % | 15,9 % |
| YouTube | <15s | 91 | 22 | 0,90 | 14,3 % | 15,9 % |
| YouTube | 30–60s | 253 | 36 | 0,93 | 13,0 % | 15,9 % |
| YouTube | 15–30s | 201 | 33 | 0,86 | 9,0 % | 15,9 % |
| TikTok | 30–60s | 751 | 37 | 1,03 | 10,1 % | 9,9 % |
| TikTok | 15–30s | 642 | 37 | 0,97 | 10,1 % | 9,9 % |
| TikTok | <15s | 345 | 35 | 1,04 | 9,9 % | 9,9 % |
| TikTok | >60s | 470 | 37 | 0,95 | 9,1 % | 9,9 % |

Gegen die jeweils eigene Basis geprüft (z-Test auf den Binomialanteil):

| Plattform | `>60s` | Basis | z | Urteil |
|---|---:|---:|---:|---|
| YouTube | 21,4 % | 15,9 % | ≈ 3,0 | klar darüber |
| Instagram | 46,9 % | 42,1 % | ≈ 2,4 | schwach darüber |
| TikTok | 9,1 % | 9,9 % | ≈ −0,6 | kein Effekt |

**„Lange Formate gewinnen" gilt dort, wo Langformate leben (YouTube), und gar nicht
dort, wo Cutdowns leben (TikTok).** Korpusweit sah es nach einem universellen
Dauer-Gesetz aus, weil lange Posts überproportional auf den Plattformen mit hoher
Basisquote liegen. Es war überwiegend ein verkleideter Plattform-Vergleich.

Die U-Form aus Abschnitt 4b überlebt ebenfalls nicht: auf Instagram sind `>60s` und
`<15s` praktisch gleichauf (46,9 % / 46,2 %) mit einem klaren Tief in der Mitte, auf
YouTube ist es eine schlichte Rangfolge mit `>60s` vorn, auf TikTok ist die Kurve
flach. Drei verschiedene Muster, die korpusweit zu einer künstlichen U-Form
verschmelzen.

### 8.3 Konsequenz im Code

`trailer_patterns.py` prüft die Trefferquote seither nicht mehr gegen die
Korpusquote, sondern gegen den **Erwartungswert aus der Plattform-Mischung der
jeweiligen Zelle** — das mit ihrer Besetzung gewichtete Mittel der Plattform-Quoten
(`expected_breakout_rate`). Der Bericht liefert `platform_breakout_rates` und je
Zelle `platform_mix` mit, damit die Rechnung nachvollziehbar bleibt.
`baseline_breakout_rate` bleibt als Kontextwert und als Rückfall für Plattformen mit
weniger als 30 Posts erhalten.

Eine Zelle, die nur deshalb gut aussieht, weil sie überwiegend auf Instagram liegt,
fällt damit auf `neutral` zurück. Der Test
`test_platform_composition_alone_is_not_a_pattern` baut genau diesen Fall nach und
zeigt in der Gegenprobe, dass die alte Referenz ihn als klaren Befund durchgewinkt
hätte.

### 8.4 Offen: die Instagram-Quote ist selbst verdächtig

42,1 % Trefferquote heißt: gut vier von zehn Instagram-Posts erreichen die doppelte
Aktivierung ihres eigenen Kanal-Medians. Das ist für eine Median-normierte Größe
unplausibel hoch — bei sauberen Daten müssten es grob 25 % sein.

Wahrscheinliche Ursache: nur 2.532 von 4.239 Instagram-Posts tragen überhaupt Views.
Posts ohne Views bekommen Aktivierung 0,0 (`compute_activation_rate`), drücken den
Kanal-Median nach unten und heben damit die Lifts aller übrigen Posts rechnerisch an.
Bei einem Kanal, bei dem die Mehrheit der Posts keine Views hat, kann der Median
sogar exakt auf einem 0,0-Wert liegen — dann greift zwar der `med > 0`-Filter, aber
knapp darüber bleibt der Effekt bestehen.

Für den *Vergleich zwischen Zellen* ist das jetzt unschädlich, weil jede Zelle gegen
dieselbe verzerrte Instagram-Quote geprüft wird. Die **absolute Höhe** der
Instagram-Zahlen ist bis zur Klärung mit Vorbehalt zu lesen. Nächster Schritt: prüfen,
ob die fehlenden Views ein Erfassungsproblem des Instagram-Connectors sind oder ob
Instagram sie für bestimmte Post-Typen (Karussells, Bilder) gar nicht ausliefert. Im
zweiten Fall gehören diese Posts aus der Aktivierungs-Rechnung heraus statt mit 0,0
hinein.

### 8.5 Korrigierte Query für die nächste Auswertung

Ersetzt die Fassung aus Abschnitt 6. Gleiche fünf Dimensionen, aber jede Zelle wird
gegen ihren eigenen Erwartungswert geprüft statt gegen die Korpusquote. Neue Spalten:
`plattformen` (woraus die Zelle besteht), `erwartet_pct` (die Quote, die allein aus
der Plattform-Mischung folgt) und `z`. Sortiert nach `z` — was oben steht, ist
auffällig *gegenüber seiner eigenen Plattform*, nicht gegenüber dem Korpus.

Gegen ein lokales Postgres 16 mit zwei synthetischen Fällen geprüft: (a) ein Format,
das nur auf der starken Plattform liegt und dort exakt deren Quote hat → `z = 0,00`,
obwohl die Korpusquote 21,7 % beträgt; (b) ein Format, das innerhalb seiner Plattform
wirklich heraussticht (80 % gegen 26,7 % erwartet) → `z = 6,61`. Beide Ergebnisse
stimmen mit den Unit-Tests in `test_trailer_patterns.py` überein.

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
  SELECT s.*, s.activation/b.med AS lift
  FROM scoped s JOIN baseline b ON b.channel_id=s.channel_id WHERE b.med > 0
), korpus AS (
  SELECT count(*) FILTER (WHERE lift >= 2.0)::numeric / count(*) AS rate FROM lifted
), plattform AS (
  SELECT platform, count(*) FILTER (WHERE lift >= 2.0)::numeric / count(*) AS rate
  FROM lifted GROUP BY platform HAVING count(*) >= 30
), cells AS (
  SELECT 'duration_bucket' AS dimension,
         CASE WHEN duration_seconds<15 THEN '1_<15s' WHEN duration_seconds<30 THEN '2_15-30s'
              WHEN duration_seconds<60 THEN '3_30-60s' ELSE '4_>60s' END AS value,
         lift, channel_id, platform
  FROM lifted WHERE duration_seconds IS NOT NULL

  UNION ALL SELECT 'music_kind',
         CASE WHEN raw_payload -> '_creative_radar_music' ->> 'musicOriginal' = 'true' THEN 'original_sound'
              WHEN raw_payload -> '_creative_radar_music' ->> 'musicOriginal' = 'false' THEN 'licensed_track'
              ELSE 'unknown' END, lift, channel_id, platform
  FROM lifted WHERE (raw_payload -> '_creative_radar_music')::text NOT IN ('null', '{}')

  UNION ALL SELECT 'format', analysis->>'format', lift, channel_id, platform FROM lifted
  WHERE analysis->>'format' IS NOT NULL AND (analysis->>'confidence')::numeric >= 0.7

  UNION ALL SELECT 'tone', analysis->>'tone', lift, channel_id, platform FROM lifted
  WHERE analysis->>'tone' IS NOT NULL AND (analysis->>'confidence')::numeric >= 0.7

  UNION ALL SELECT 'lifecycle_stage', analysis->>'lifecycle_stage', lift, channel_id, platform FROM lifted
  WHERE analysis->>'lifecycle_stage' IS NOT NULL AND (analysis->>'confidence')::numeric >= 0.7
), agg AS (
  SELECT c.dimension, c.value,
         count(*) AS posts,
         count(DISTINCT c.channel_id) AS kanaele,
         percentile_cont(0.5) WITHIN GROUP (ORDER BY c.lift) AS median_lift,
         percentile_cont(0.9) WITHIN GROUP (ORDER BY c.lift) AS p90_lift,
         count(*) FILTER (WHERE c.lift >= 2.0)::numeric / count(*) AS treffer,
         avg(COALESCE(pl.rate, (SELECT rate FROM korpus))) AS erwartet,
         string_agg(DISTINCT c.platform, '+' ORDER BY c.platform) AS plattformen
  FROM cells c LEFT JOIN plattform pl ON pl.platform = c.platform
  GROUP BY c.dimension, c.value
  HAVING count(*) >= 5 AND count(DISTINCT c.channel_id) >= 3
)
SELECT dimension, value, posts, kanaele, plattformen,
       round(median_lift::numeric, 2) AS median_lift,
       round(100 * treffer, 1)  AS treffer_pct,
       round(100 * erwartet, 1) AS erwartet_pct,
       round(100 * (SELECT rate FROM korpus), 1) AS korpus_pct,
       round(((treffer - erwartet)
              / NULLIF(sqrt(erwartet * (1 - erwartet) / posts), 0))::numeric, 2) AS z,
       round(p90_lift::numeric, 2) AS p90_lift
FROM agg
ORDER BY z DESC NULLS LAST;
```

Die Zeile `avg(COALESCE(pl.rate, (SELECT rate FROM korpus)))` ist der Kern: gemittelt
wird über die *Posts* der Zelle, nicht über die Plattformen — damit ist es das mit der
Besetzung gewichtete Mittel. Plattformen mit weniger als 30 Posts haben keine eigene
Quote und fallen auf die Korpusquote zurück (`HAVING count(*) >= 30` in `plattform`).

## 9. Formatklassen: Langform und Kurzform getrennt auswerten

Die Auswertung hat bisher Langformate (Trailer, Teaser, Promo, ab rund einer Minute)
und Kurzformate (TV- und Social-Spots, 5 bis rund 90 Sekunden) in einem Topf gehabt
und nach „dem" erfolgreichen Muster gesucht. Das mischt zwei verschiedene Handwerke:
Langform ist auf Aufbau, Wendepunkt und Auflösung gebaut, Kurzform muss in den ersten
Sekunden alles unterbringen.

### 9.1 Die Klassen

| Klasse | Dauer | |
|---|---|---|
| `kurzform` | < 60 s | TV- und Social-Spots |
| `uebergang_60_90s` | 60 – 89 s | Grauzone, in der sich beide Definitionen überlappen |
| `langform` | ≥ 90 s | Trailer, Teaser, Promo |

**Aus der Dauer, nicht aus dem Format-Label.** Das Label läge näher — `trailer` ist per
Definition Langform. Aber es existiert für 13,8 % der Posts, die Dauer für rund 90 %.
Eine Einteilung auf Label-Basis hätte in genau der Frage, wegen der sie gebaut wird,
fast keine Datengrundlage.

**Die Grauzone bekommt einen eigenen Namen statt einer Zuordnung.** Ein
75-Sekunden-Stück kann ein kurzer Trailer oder ein langer Spot sein; das entscheidet
der Aufbau, nicht die Sekundenzahl. Sie still einer Seite zuzuschlagen wäre schlimmer,
als die Lücke zu zeigen — dieselbe Regel wie bei `verdict="insufficient"`. Wenn die
Trailerhaus-Fragebögen zurück sind, kann diese Zone gezielt aufgelöst werden.

### 9.2 Zwei Wege, sie zu nutzen

`format_class` ist zugleich Dimension und Filter:

- **Ohne Eingrenzung** erscheint `format_class` als eigene Dimension im Bericht — man
  sieht auf einen Blick, ob und wie stark die Klassen sich unterscheiden.
- **Mit `?format_class=langform`** läuft die *gesamte* Auswertung nur innerhalb dieser
  Klasse, inklusive der Plattform-Quoten. Langformate werden dann ausschließlich mit
  Langformaten verglichen.

### 9.3 Was dabei bewusst nicht mitgefiltert wird

Die Kanal-Baseline bleibt auch bei eingegrenzter Auswertung der Median des **gesamten**
Kanal-Outputs. Das ist die eine Stelle, an der eine naheliegende Vereinfachung das
Ergebnis zerstören würde: Filtert man die Baseline mit, vergleichen sich Langformate
nur noch mit Langformaten desselben Kanals. Der Median-Lift läge dann per Konstruktion
bei 1,0, und die Frage „trägt Langform überhaupt?" wäre nicht mehr beantwortbar — die
Antwort wäre immer „durchschnittlich".

Gesichert durch `test_scoped_report_keeps_the_full_channel_baseline`: ein Kanal mit
schwachem Kurzform-Grundrauschen und durchgehend doppelt so starker Langform muss auch
im eingegrenzten Bericht Lift 2,0 und `verdict="over"` liefern.

## 10. Nächste Diagnose: die fehlenden Instagram-Views

Aus Abschnitt 8.4. Zwei Queries, beide gegen ein lokales Postgres 16 mit synthetischen
Testfällen geprüft. Read-only, beliebig oft ausführbar.

### 10.1 Query A — wo genau fehlen die Views?

Entscheidet die eigentliche Frage: **Erfassungsproblem oder Plattform-Verhalten?**
Wenn die Lücke sich auf `image` und `sidecar` (Karussells) konzentriert, liefert
Instagram für diese Post-Typen schlicht keine Views — dann gehören sie aus der
Aktivierungsrechnung heraus. Verteilt sie sich gleichmäßig über alle Asset-Typen,
ist es ein Fehler im Connector.

Die Query trennt außerdem `NULL` von `0`. Das ist kein Detail: `NULL` heißt „nicht
erfasst", `0` heißt „erfasst und tatsächlich null" — und nur im ersten Fall ist die
Behandlung als Aktivierung 0,0 falsch.

```sql
SELECT c.platform,
       COALESCE(p.asset_type, '(kein asset_type)') AS asset_typ,
       count(*) AS posts,
       count(*) FILTER (WHERE p.visible_views IS NULL)  AS views_null,
       count(*) FILTER (WHERE p.visible_views = 0)      AS views_null_wert,
       count(*) FILTER (WHERE COALESCE(p.visible_views,0) > 0) AS views_vorhanden,
       round(100.0 * count(*) FILTER (WHERE COALESCE(p.visible_views,0) = 0)
             / count(*), 1) AS ohne_views_pct
FROM creative_radar.post p
JOIN creative_radar.channel c ON c.id = p.channel_id
WHERE p.detected_at > now() - interval '90 days'
GROUP BY 1, 2
ORDER BY 1, ohne_views_pct DESC, posts DESC;
```

### 10.2 Query B — wie stark hängt die Trefferquote daran?

Rechnet dieselbe Trefferquote zweimal nebeneinander: einmal wie heute (Posts ohne
Views zählen mit Aktivierung 0,0), einmal ohne diese Posts — auch aus der
Kanal-Baseline heraus.

**Erwartung, falls die Hypothese stimmt:** die Instagram-Quote fällt von 42,1 % Richtung
25 %, TikTok und YouTube bleiben praktisch unverändert. Bleibt Instagram oben, liegt es
nicht an den fehlenden Views und die Ursache ist woanders zu suchen.

```sql
WITH alle AS (
  SELECT p.id, p.channel_id, c.platform, p.visible_views,
         CASE WHEN COALESCE(p.visible_views,0)=0 THEN 0::numeric
              WHEN c.platform='youtube' THEN (GREATEST(COALESCE(p.visible_likes,0),0)+GREATEST(COALESCE(p.visible_comments,0),0))::numeric/p.visible_views
              ELSE (GREATEST(COALESCE(p.visible_likes,0),0)+GREATEST(COALESCE(p.visible_comments,0),0)+GREATEST(COALESCE(p.visible_bookmarks,0),0))::numeric/p.visible_views END AS activation
  FROM creative_radar.post p JOIN creative_radar.channel c ON c.id=p.channel_id
  WHERE p.detected_at > now() - interval '90 days'
), mit_views AS (
  SELECT * FROM alle WHERE COALESCE(visible_views,0) > 0
),
base_ist AS (
  SELECT channel_id, percentile_cont(0.5) WITHIN GROUP (ORDER BY activation) AS med
  FROM alle GROUP BY channel_id HAVING count(*) >= 4
),
base_ohne AS (
  SELECT channel_id, percentile_cont(0.5) WITHIN GROUP (ORDER BY activation) AS med
  FROM mit_views GROUP BY channel_id HAVING count(*) >= 4
),
lift_ist AS (
  SELECT a.platform, a.activation/b.med AS lift
  FROM alle a JOIN base_ist b ON b.channel_id=a.channel_id WHERE b.med > 0
),
lift_ohne AS (
  SELECT m.platform, m.activation/b.med AS lift
  FROM mit_views m JOIN base_ohne b ON b.channel_id=m.channel_id WHERE b.med > 0
)
SELECT COALESCE(i.platform, o.platform) AS plattform,
       i.posts AS ist_posts, i.quote AS ist_trefferquote_pct,
       o.posts AS ohne_posts, o.quote AS ohne_trefferquote_pct
FROM (SELECT platform, count(*) AS posts,
             round(100.0*count(*) FILTER (WHERE lift >= 2.0)/count(*),1) AS quote
      FROM lift_ist GROUP BY platform) i
FULL OUTER JOIN
     (SELECT platform, count(*) AS posts,
             round(100.0*count(*) FILTER (WHERE lift >= 2.0)/count(*),1) AS quote
      FROM lift_ohne GROUP BY platform) o
  ON o.platform = i.platform
ORDER BY 1;
```

**Der Mechanismus, den die Query sichtbar macht** (so im Testfall nachgebaut): ein Kanal
mit 5 Posts à Aktivierung 0,10 und 5 Posts ohne Views hat einen Median von 0,05 statt
0,10. Damit bekommen die fünf echten Posts rechnerisch Lift 2,0 und gelten als Treffer —
Trefferquote 50 % statt 0 %. Die Verzerrung entsteht also nicht bei den Posts ohne
Views selbst, sondern bei allen anderen Posts desselben Kanals.

### 10.3 Danach

- Bestätigt sich der Verdacht: `compute_activation_rate` bekommt eine Unterscheidung
  zwischen „keine Views erfasst" (Post gehört nicht in die Rechnung) und „null Views"
  (Aktivierung 0,0 ist korrekt). Das betrifft auch den Empfehlungs-Baustein, nicht nur
  dieses Modul — die Änderung will sorgfältig gemacht werden.
- Bestätigt er sich nicht, bleiben die Instagram-Zahlen wie sie sind, und die Ursache
  der hohen Quote ist eine offene Frage für die nächste Auswertung.

## 11. Lauf vom 07.08.2026 — vollständige fünf Dimensionen

Die Query aus Abschnitt 6 (korpusweite Basisquote), diesmal mit `lifecycle_stage` und
`music_kind`, die beim ersten Lauf gefehlt hatten.

Datenbasis: 7.358 Posts im Fenster, 6.374 mit Baseline, 154 von 203 Kanälen,
Klassifikations-Abdeckung **12,2 %**. Basisquote 20,0 %.

| Dimension | Wert | Posts | Kanäle | Median | Treffer | P90 |
|---|---|---:|---:|---:|---:|---:|
| tone | edgy | 6 | 6 | 1,42 | 33,3 % | 5,14 |
| duration_bucket | >60s | 1.507 | 152 | 1,24 | 28,3 % | 3,95 |
| tone | emotional | 33 | 23 | 1,32 | 27,3 % | 2,80 |
| format | behind_the_scenes | 37 | 24 | 0,81 | 27,0 % | 3,02 |
| lifecycle_stage | pre_launch | 121 | 51 | 1,10 | 25,6 % | 3,26 |
| format | teaser | 29 | 21 | 1,07 | 24,1 % | 3,08 |
| duration_bucket | <15s | 687 | 125 | 1,15 | 23,7 % | 3,15 |
| tone | inspirational | 35 | 28 | 1,10 | 22,9 % | 3,04 |
| duration_bucket | 15–30s | 1.415 | 146 | 1,08 | 22,5 % | 3,41 |
| format | trailer | 37 | 22 | 1,33 | 21,6 % | 3,09 |
| duration_bucket | 30–60s | 1.675 | 150 | 1,13 | 21,3 % | 3,07 |
| tone | energetic | 291 | 76 | 1,04 | 18,6 % | 2,96 |
| format | promo | 351 | 75 | 1,06 | 18,5 % | 2,96 |
| lifecycle_stage | post_launch | 153 | 44 | 1,10 | 17,6 % | 2,39 |
| tone | suspenseful | 75 | 36 | 1,00 | 17,3 % | 2,80 |
| lifecycle_stage | launch | 245 | 56 | 1,02 | 17,1 % | 2,94 |
| tone | informative | 61 | 31 | 0,98 | 16,4 % | 2,33 |
| format | clip | 170 | 48 | 0,99 | 13,5 % | 2,17 |
| tone | humorous | 142 | 48 | 1,03 | 13,4 % | 2,17 |
| format | interview | 17 | 11 | 1,15 | 11,8 % | 1,93 |
| lifecycle_stage | evergreen | 137 | 45 | 1,00 | 11,7 % | 2,14 |
| music_kind | original_sound | 2.065 | 38 | 1,00 | 10,1 % | 2,01 |
| music_kind | licensed_track | 228 | 28 | 0,96 | 9,6 % | 1,89 |
| format | compilation | 13 | 7 | 1,19 | 7,7 % | 1,92 |
| tone | neutral | 13 | 9 | 1,12 | 7,7 % | 1,90 |

### 11.1 Musik bestätigt die Plattform-Korrektur an einem sauberen Fall

`music_kind` stammt aus `raw_payload._creative_radar_music` und existiert damit
**ausschließlich für TikTok-Posts** (2.065 + 228 = 2.293 von 2.335). Die Zelle hat also
eine reine Plattform-Mischung — und TikToks eigene Basisquote ist 9,9 % (Abschnitt 8.1).

| Referenz | `original_sound` (10,1 %, n=2.065) | Lesart |
|---|---:|---|
| Korpusquote 20,0 % | z ≈ **−11,2** | katastrophaler Einbruch |
| TikTok-Quote 9,9 % | z ≈ **+0,3** | exakt durchschnittlich |

Dieselbe Zahl, zwei völlig verschiedene Aussagen. `licensed_track` liegt mit 9,6 %
(z ≈ −0,2 gegen TikTok) ebenfalls genau auf Plattform-Niveau.

Der Befund aus Abschnitt 4f — „Musik liefert kein Signal" — bleibt damit richtig, aber
aus dem umgekehrten Grund als gedacht: nicht weil beide Musikarten gleich schwach
sind, sondern weil beide **normal** sind und der scheinbare Einbruch reine
Plattform-Zusammensetzung war. Ein besserer Beleg für die Korrektur aus Abschnitt 8
als jeder synthetische Testfall.

### 11.2 Die Sortierung dieser Query ist irreführend

`ORDER BY treffer_pct DESC` setzt `tone=edgy` mit **6 Posts** an die Spitze. Gegen die
Korpusquote gerechnet ist das z ≈ 0,8 — reines Rauschen. Genau deshalb sortiert
`trailer_patterns.py` nach `breakout_z` statt nach der Rohquote. Für den nächsten Lauf
gehört die Query aus Abschnitt 8.5 verwendet, nicht diese.

### 11.3 Was noch aussteht

`lifecycle_stage` ist neu in der Auswertung und spannt von `pre_launch` 25,6 % bis
`evergreen` 11,7 %. Ob davon nach der Plattform-Korrektur etwas übrigbleibt, sagt erst
die Query aus Abschnitt 8.5 — die Klassen sind über alle drei Plattformen verteilt.

## 12. Warnsignal: die Klassifikations-Abdeckung fällt

| Datum | Posts mit Baseline | Abdeckung | klassifizierte Posts |
|---|---:|---:|---:|
| 06.08.2026 | 6.577 | 13,8 % | ≈ 908 |
| 07.08.2026 | 6.374 | 12,2 % | ≈ 778 |

Die Cron-Stage aus PR #331 klassifiziert bis zu 800 Posts pro Lauf. Ein einziger
vollständiger Lauf müsste die Abdeckung um rund 12 Prozentpunkte anheben. Sie ist
stattdessen um 1,6 Punkte gefallen.

Ein Teil davon ist erklärbar: die per Backfill klassifizierten Posts sind die älteren,
und die fallen als erste aus dem 90-Tage-Fenster. Aber das erklärt kein Ausbleiben von
Zuwachs — nur eine Dämpfung.

Drei mögliche Ursachen, in absteigender Wahrscheinlichkeit:

1. Die Stage läuft nicht (Deploy nicht durch, oder das Anthropic-Budget-Preflight
   bricht den Lauf vorher ab).
2. Sie läuft, aber `is_anthropic_configured()` ist falsch — dann meldet sie sich mit
   `{"enabled": false, "reason": "anthropic_not_configured"}` und tut nichts.
3. Sie läuft und setzt `last_analyzed_at`, liefert aber keine verwertbaren Formate —
   dann steigt `analysiert`, ohne dass `mit_format` mitwächst.

> **Beantwortet am 07.08.2026 — siehe Abschnitt 14.** Keine der drei Ursachen war es.
> Der Cron läuft wöchentlich montags, und der Merge kam drei Tage nach dem letzten
> Lauf. Die Stage hatte schlicht noch keine Gelegenheit.

Die folgenden Queries unterscheiden die drei Fälle. Read-only, gegen ein lokales
Postgres 16 geprüft.

```sql
-- C1: Was hat die Stage in den letzten Läufen gemeldet?
--     NULL = dieser Lauf kannte die Stage noch nicht (alter Deploy).
SELECT started_at, status,
       summary_json -> 'post_analysis' AS post_analysis_stage
FROM creative_radar.cron_run
ORDER BY started_at DESC
LIMIT 10;
```

```sql
-- C2: Backlog und die Lücke zwischen "analysiert" und "hat ein Format".
SELECT count(*) FILTER (WHERE last_analyzed_at IS NULL)          AS nie_analysiert,
       count(*) FILTER (WHERE last_analyzed_at IS NOT NULL)      AS analysiert,
       count(*) FILTER (WHERE analysis ->> 'format' IS NOT NULL) AS mit_format,
       max(last_analyzed_at)                                     AS zuletzt
FROM creative_radar.post;
```

```sql
-- C3: Verlauf — an welchen Tagen wurde tatsaechlich klassifiziert?
SELECT last_analyzed_at::date AS tag,
       count(*)                                                  AS analysiert,
       count(*) FILTER (WHERE analysis ->> 'format' IS NOT NULL) AS davon_mit_format
FROM creative_radar.post
WHERE last_analyzed_at IS NOT NULL
GROUP BY 1 ORDER BY 1 DESC LIMIT 14;
```

Solange die Abdeckung nicht steigt, bleiben `format`, `tone` und `lifecycle_stage` auf
einem Achtel des Bestands sitzen — und genau diese drei Dimensionen tragen die
Trailer-Fragestellung. `duration_bucket` und `music_kind` sind davon nicht betroffen,
sie werden gemessen statt klassifiziert.

## 13. Auflösung (07.08.2026, nachmittags) — die Posts ohne Views waren der Fehler

Zwei Läufe zusammen haben den Rest der Auswertung geklärt: die korrigierte Query aus
Abschnitt 8.5 und Query B aus Abschnitt 10.2. Ergebnis-Screenshots liegen unter
`docs/screenshots/`.

### 13.1 Die Plattform-Korrektur funktioniert — nachweisbar

`music_kind` ist der sauberste denkbare Testfall, weil das Feld nur für TikTok-Posts
existiert. Die korrigierte Query erkennt das und setzt `erwartet_pct` korrekt auf die
TikTok-Quote:

| Dimension | Wert | Posts | Plattformen | Treffer | erwartet | z |
|---|---|---:|---|---:|---:|---:|
| music_kind | original_sound | 2.065 | tiktok | 10,1 % | 10,1 % | **0,00** |
| music_kind | licensed_track | 228 | tiktok | 9,6 % | 10,1 % | −0,24 |

Gegen die alte Korpusquote von 20 % wäre `original_sound` mit z ≈ −11,2 ein
katastrophaler Einbruch gewesen. Es ist exakt durchschnittlich.

Ebenfalls gekippt: **„Humor ist der zuverlässigste Verlierer"** (Abschnitt 4d). 13,4 %
gegen eine Erwartung von 14,3 % ergibt z ≈ −0,3. Der Befund war ein
Plattform-Artefakt, kein Ton-Effekt.

### 13.2 Aber: alle vier Dauer-Buckets kamen „über" heraus

| Bucket | Posts | Treffer | erwartet | z |
|---|---:|---:|---:|---:|
| >60s | 1.507 | 28,3 % | 19,4 % | **+8,74** |
| <15s | 687 | 23,7 % | 17,6 % | **+4,20** |
| 15–30s | 1.415 | 22,5 % | 18,4 % | **+3,98** |
| 30–60s | 1.675 | 21,3 % | 18,4 % | **+3,06** |

Nach der Korrektur war das die *einzige* Dimension mit signifikanten Ausschlägen — und
alle vier zeigten in dieselbe Richtung. Vier gleichgerichtete Ausschläge in einer
Dimension sind kein Befund, sondern ein systematischer Fehler.

### 13.3 Der Fehler saß im Nenner der Plattform-Quote

Instagram im Fenster: 3.130 Posts, Trefferquote 28,6 %. Davon mit Dauer-Angabe: 2.125
Posts, Trefferquote 42,1 %. Nachgerechnet:

```
3.130 × 28,6 %  =  895,2 Treffer   (alle Instagram-Posts)
2.125 × 42,1 %  =  894,6 Treffer   (nur die mit Dauer)
────────────────────────────────
1.005 Posts ohne Dauer  →  0,6 Treffer, also null
```

**Die 1.005 Instagram-Posts ohne Dauer liefern exakt keinen einzigen Treffer.** Es sind
Bilder und Karussells, für die Instagram keine Views ausliefert — dieselben 1.005, die
in der Todo-Liste als „kaputte Bildabrufe" stehen.

Ihre Aktivierung ist 0,0. Das sieht aus wie eine Messung, ist aber eine Leerstelle. Sie
können per Konstruktion nie ein Treffer sein, saßen aber im Nenner der Plattform-Quote.
Jede Zelle, die nur Posts mit Dauer enthält — also jeder Dauer-Bucket — lag dadurch
automatisch über ihrer Erwartung.

### 13.4 Query B misst den zweiten, größeren Teil des Schadens

| Plattform | mit den 0,0-Posts | ohne sie | Differenz |
|---|---:|---:|---:|
| Instagram | 28,6 % | **15,1 %** | −13,5 pp |
| YouTube | 15,9 % | 15,9 % | 0 |
| TikTok | 10,1 % | 10,1 % | 0 |

TikTok und YouTube haben keinen einzigen Post ohne Views — identische Zahlen in beiden
Spalten. Der Effekt ist rein instagram-seitig, halbiert dort aber die Quote.

Der Mechanismus ist der aus Abschnitt 10.2: die Null geht in den Kanal-Median ein und
drückt ihn, wodurch die Lifts **aller übrigen** Posts desselben Kanals steigen. Die
Verzerrung trifft also nicht die Posts ohne Views, sondern alle anderen.

### 13.5 Korrektur im Code und was von der Plattform-Aussage übrig bleibt

`trailer_patterns.py` schließt Posts ohne messbare Views jetzt aus — vor der
Baseline-Berechnung, damit der Median sauber bleibt (`_has_measurable_views`). Die
Notes melden, wie viele Posts je Plattform betroffen sind; eine stille Filterung wäre
gegen die Ehrlichkeits-Regeln des Moduls.

**Damit ist auch meine Darstellung aus Abschnitt 8 zu korrigieren.** Die dort genannten
„Faktor vier" zwischen den Plattformen beruhten auf Instagrams 42,1 % — einer Zahl, die
doppelt verzerrt war (nur Posts mit Dauer, und durch die 0,0-Posts aufgebläht). Die
tatsächliche Spanne:

| Plattform | bereinigte Trefferquote |
|---|---:|
| YouTube | 15,9 % |
| Instagram | 15,1 % |
| TikTok | 10,1 % |

Faktor 1,6, nicht 4. Die Plattform-Korrektur bleibt richtig und notwendig — TikTok
liegt belastbar unter den anderen beiden —, aber das Ausmaß war überzeichnet.

### 13.6 Korrigierte Query A

Die Fassung in Abschnitt 10.1 lief nicht: `column p.asset_type does not exist`. Mein
Fehler — `asset_type` sitzt auf der `asset`-Tabelle, `post` hat `media_type`. Diese
Fassung ist gegen ein lokales Postgres 16 geprüft:

```sql
SELECT c.platform,
       COALESCE(p.media_type, '(kein media_type)') AS medien_typ,
       count(*)                                                AS posts,
       count(*) FILTER (WHERE p.visible_views IS NULL)         AS views_null,
       count(*) FILTER (WHERE p.visible_views = 0)             AS views_null_wert,
       count(*) FILTER (WHERE COALESCE(p.visible_views,0) > 0) AS views_vorhanden,
       round(100.0 * count(*) FILTER (WHERE COALESCE(p.visible_views,0) = 0)
             / count(*), 1)                                    AS ohne_views_pct
FROM creative_radar.post p
JOIN creative_radar.channel c ON c.id = p.channel_id
WHERE p.detected_at > now() - interval '90 days'
GROUP BY 1, 2
ORDER BY 1, ohne_views_pct DESC, posts DESC;
```

Sie ist für die Rechnung nicht mehr nötig — der Ausschluss steht und wirkt unabhängig
davon, warum die Views fehlen. Sie beantwortet aber die Anschlussfrage, ob sich der
Instagram-Connector reparieren lässt oder ob Instagram für Bilder und Karussells
schlicht keine Views liefert.

### 13.7 Was daraus folgt

1. Die Auswertung muss nach der Code-Korrektur **neu laufen**. Alle Zahlen in den
   Abschnitten 3, 8 und 11 stammen aus der verzerrten Grundgesamtheit.
2. Von den ursprünglich vier Befunden hält keiner: „lange Formate gewinnen"
   (Abschnitt 8), „Musik ist ein Verlierer" und „Humor ist ein Verlierer"
   (Abschnitt 13.1), und `behind_the_scenes` fällt mit z ≈ 1,6 unter die Schwelle.
   Es bleibt vorerst **kein einziger belastbarer Befund** aus Format, Ton, Dauer oder
   Musik.
3. Das ist kein Rückschlag, sondern die Bestätigung der Ausgangs-Hypothese aus
   Abschnitt 4g: die entscheidende Varianz sitzt nicht in diesen Metadaten, sondern in
   der kreativen Ausführung — Hook, Schnitt, Bildsprache. Genau das, was die
   Thumbnail-only-Pipeline nicht erhebt.

## 14. Die Cron-Stage lief nie — und warum das harmlos ist

Die Queries C1–C3 aus Abschnitt 12 sind gelaufen. Ergebnis: alle drei möglichen
Ursachen, die dort standen, treffen nicht zu.

### 14.1 C1 — zehn Läufe, zehnmal `NULL`

| | |
|---|---|
| Cron-Rhythmus | **wöchentlich, Montag 03:00 UTC** (`cron_scheduler.py`, `_TRIGGER_WEEKDAY = 0`) |
| Letzter Lauf | Mo **03.08.2026**, 03:00:04, `completed` |
| PR #331 gemergt | Do **06.08.2026**, 21:13 |

Der Merge kam drei Tage **nach** dem letzten Wochenlauf. Deshalb steht in allen zehn
protokollierten Läufen `NULL` bei `post_analysis` — keiner von ihnen kannte die Stage.
Kein Fehler, keine fehlende Konfiguration, kein blockierendes Budget. Nächster Lauf:
**Montag, 10.08.2026, 03:00 UTC.**

Nachtrag zur Methodik: der Zeitplan steht im Code und war ohne Datenbankzugriff
nachlesbar. Die Beobachtung in Abschnitt 12 war richtig, die dort formulierte
Dringlichkeit nicht.

### 14.2 C2 und C3 — der Bestand kommt aus zwei manuellen Läufen im Mai

| | |
|---|---:|
| nie analysiert | 7.212 |
| analysiert | 1.220 |
| davon mit `format` | **1.220** |
| zuletzt analysiert | 28.05.2026, 21:47 |

| Tag | analysiert | davon mit Format |
|---|---:|---:|
| 28.05.2026 | 1.210 | 1.210 |
| 03.05.2026 | 10 | 10 |

`analysiert` und `mit_format` sind identisch — der Analyzer liefert für jeden Post, den
er anfasst, auch ein Format. Die dritte Hypothese aus Abschnitt 12 („läuft, liefert
aber keine Formate") ist damit ausgeschlossen.

### 14.3 Eine Frist: 26. August

Die 1.210 Posts vom 28.05. fallen am **26.08.2026** aus dem 90-Tage-Fenster. Passiert
bis dahin nichts, sinkt die Klassifikations-Abdeckung nicht auf 12 %, sondern auf nahe
null — dann sind `format`, `tone` und `lifecycle_stage` als Dimensionen unbrauchbar.

Bei 800 Posts pro Lauf und einem Rhythmus von einer Woche braucht der Backlog von 7.212
Posts **neun Wochen**, also bis Mitte Oktober. Das ist nach der Frist.

**Empfehlung:** `CRON_POST_ANALYSIS_MAX_POSTS_PER_RUN` vor dem 10.08. auf **2500**
setzen. Kosten bei ~$0,0029 pro Post text-only: rund **$7 pro Lauf**, drei Läufe für
den ganzen Backlog, der erste noch vor der Frist.

Bewusst nicht 8.000 in einem Rutsch: die reale Latenz pro Post ist unbekannt, und
geschätzt wären das sechs bis sieben Stunden am Stück. Die Stage meldet
`duration_seconds` in ihrer Zusammenfassung — nach dem ersten Lauf lässt sich der Wert
mit einer echten Messung setzen statt mit einer Schätzung.

### 14.4 Kontrolle nach dem 10.08.

```sql
SELECT started_at, status, summary_json -> 'post_analysis' AS post_analysis_stage
FROM creative_radar.cron_run ORDER BY started_at DESC LIMIT 1;
```

Dort stehen dann `attempted`, `analyzed`, `errors` und `duration_seconds` — die vier
Zahlen, die für die Cap-Entscheidung nötig sind.

## 15. Query für die Neu-Erhebung nach der Views-Korrektur

Ersetzt Abschnitt 8.5. Entspricht dem Stand nach PR #342: `visible_views > 0`,
`format_class` als sechste Dimension, Erwartungswert je Plattform-Mischung. Gegen ein
lokales Postgres 16 geprüft — mit Testdaten, bei denen der Views-Filter eine künstlich
auf 18,2 % verdünnte Quote korrekt auf die echten 33,3 % zurückholt, während die
unbelastete Plattform unverändert bleibt.

```sql
WITH scoped AS (
  SELECT p.id, p.channel_id, c.platform, p.duration_seconds, p.analysis, p.raw_payload,
         CASE WHEN c.platform='youtube' THEN (GREATEST(COALESCE(p.visible_likes,0),0)+GREATEST(COALESCE(p.visible_comments,0),0))::numeric/p.visible_views
              ELSE (GREATEST(COALESCE(p.visible_likes,0),0)+GREATEST(COALESCE(p.visible_comments,0),0)+GREATEST(COALESCE(p.visible_bookmarks,0),0))::numeric/p.visible_views END AS activation
  FROM creative_radar.post p JOIN creative_radar.channel c ON c.id=p.channel_id
  WHERE p.detected_at > now() - interval '90 days'
    AND COALESCE(p.visible_views, 0) > 0
), baseline AS (
  SELECT channel_id, percentile_cont(0.5) WITHIN GROUP (ORDER BY activation) AS med
  FROM scoped GROUP BY channel_id HAVING count(*) >= 4
), lifted AS (
  SELECT s.*, s.activation/b.med AS lift
  FROM scoped s JOIN baseline b ON b.channel_id=s.channel_id WHERE b.med > 0
), korpus AS (
  SELECT count(*) FILTER (WHERE lift >= 2.0)::numeric / count(*) AS rate FROM lifted
), plattform AS (
  SELECT platform, count(*) FILTER (WHERE lift >= 2.0)::numeric / count(*) AS rate
  FROM lifted GROUP BY platform HAVING count(*) >= 30
), cells AS (
  SELECT 'format_class' AS dimension,
         CASE WHEN duration_seconds < 60 THEN '1_kurzform'
              WHEN duration_seconds < 90 THEN '2_uebergang_60_90s'
              ELSE '3_langform' END AS value, lift, channel_id, platform
  FROM lifted WHERE duration_seconds IS NOT NULL

  UNION ALL SELECT 'duration_bucket',
         CASE WHEN duration_seconds<15 THEN '1_<15s' WHEN duration_seconds<30 THEN '2_15-30s'
              WHEN duration_seconds<60 THEN '3_30-60s' ELSE '4_>60s' END, lift, channel_id, platform
  FROM lifted WHERE duration_seconds IS NOT NULL

  UNION ALL SELECT 'music_kind',
         CASE WHEN raw_payload -> '_creative_radar_music' ->> 'musicOriginal' = 'true' THEN 'original_sound'
              WHEN raw_payload -> '_creative_radar_music' ->> 'musicOriginal' = 'false' THEN 'licensed_track'
              ELSE 'unknown' END, lift, channel_id, platform
  FROM lifted WHERE (raw_payload -> '_creative_radar_music')::text NOT IN ('null', '{}')

  UNION ALL SELECT 'format', analysis->>'format', lift, channel_id, platform FROM lifted
  WHERE analysis->>'format' IS NOT NULL AND (analysis->>'confidence')::numeric >= 0.7

  UNION ALL SELECT 'tone', analysis->>'tone', lift, channel_id, platform FROM lifted
  WHERE analysis->>'tone' IS NOT NULL AND (analysis->>'confidence')::numeric >= 0.7

  UNION ALL SELECT 'lifecycle_stage', analysis->>'lifecycle_stage', lift, channel_id, platform FROM lifted
  WHERE analysis->>'lifecycle_stage' IS NOT NULL AND (analysis->>'confidence')::numeric >= 0.7
), agg AS (
  SELECT c.dimension, c.value,
         count(*) AS posts, count(DISTINCT c.channel_id) AS kanaele,
         percentile_cont(0.5) WITHIN GROUP (ORDER BY c.lift) AS median_lift,
         percentile_cont(0.9) WITHIN GROUP (ORDER BY c.lift) AS p90_lift,
         count(*) FILTER (WHERE c.lift >= 2.0)::numeric / count(*) AS treffer,
         avg(COALESCE(pl.rate, (SELECT rate FROM korpus))) AS erwartet,
         string_agg(DISTINCT c.platform, '+' ORDER BY c.platform) AS plattformen
  FROM cells c LEFT JOIN plattform pl ON pl.platform = c.platform
  GROUP BY c.dimension, c.value
  HAVING count(*) >= 5 AND count(DISTINCT c.channel_id) >= 3
)
SELECT dimension, value, posts, kanaele, plattformen,
       round(median_lift::numeric, 2) AS median_lift,
       round(100 * treffer, 1)  AS treffer_pct,
       round(100 * erwartet, 1) AS erwartet_pct,
       round(((treffer - erwartet)
              / NULLIF(sqrt(erwartet * (1 - erwartet) / posts), 0))::numeric, 2) AS z,
       round(p90_lift::numeric, 2) AS p90_lift
FROM agg
ORDER BY z DESC NULLS LAST;
```

**Zwei Kontrollen beim Lesen des Ergebnisses:**

1. Die vier Dauer-Buckets dürfen nicht mehr alle gleichzeitig über ihrer Erwartung
   liegen. Tun sie es doch, steckt ein zweiter Confound derselben Bauart drin.
2. Die Plattform-Quoten sollten bei rund 15,9 % (YouTube), 15,1 % (Instagram) und
   10,1 % (TikTok) landen — die bereinigten Werte aus Abschnitt 13.5.

## 16. Neu-Erhebung nach allen drei Korrekturen (07.08.2026, 15:22)

Die Query aus Abschnitt 15 ist gelaufen. 28 Zellen, sortiert nach `z`. Screenshots
unter `docs/screenshots/`.

### 16.1 Die Korrektur hat gegriffen — beide Kontrollen bestanden

**Kontrolle 1: die Dauer-Buckets streuen wieder in beide Richtungen.**

| Bucket | vorher (Abschnitt 13.2) | | nachher | |
|---|---:|---:|---:|---:|
| | Treffer | z | Treffer | z |
| >60s | 28,3 % | **+8,74** | 15,2 % | ~+2 |
| <15s | 23,7 % | **+4,20** | 14,0 % | +0,96 |
| 15–30s | 22,5 % | **+3,98** | 12,3 % | −0,80 |
| 30–60s | 21,3 % | **+3,06** | 11,7 % | −1,75 |

Vorher vier gleichgerichtete Ausschläge, jetzt zwei über und zwei unter der Erwartung.
Das systematische Artefakt ist weg.

**Kontrolle 2: der Median-Lift ist zusammengefallen.** `>60s` hatte 1,24, jetzt 1,01.
Über alle 28 Zellen liegt der Median-Lift zwischen 0,96 und 1,32 — die Aufblähung durch
die gedrückten Kanal-Mediane ist verschwunden.

**Unerwarteter Nebeneffekt: die Auswertung deckt jetzt mehr Bestand ab.** `>60s` ging
von 152 auf **179 Kanäle**, `15–30s` von 146 auf 168. Grund: Kanäle, deren
Median-Aktivierung wegen der Posts ohne Views bei 0,0 lag, fielen vorher komplett durch
den `med > 0`-Filter. Mit dem Ausschluss haben sie einen positiven Median und zählen
wieder mit. Die Korrektur hat also nicht nur verzerrt gemessene Posts entfernt, sondern
zu Unrecht ausgeschlossene zurückgeholt.

### 16.2 Der einzige Befund, der übrig bleibt: die Formatklasse

| Klasse | Posts | Kanäle | Median-Lift | Treffer | erwartet |
|---|---:|---:|---:|---:|---:|
| `3_langform` (≥ 90 s) | 869 | 176 | 1,06 | **16,9 %** | ~13 % |
| `2_uebergang` (60–89 s) | 758 | 153 | 0,99 | 13,3 % | ~13 % |
| `1_kurzform` (< 60 s) | 3.980 | 180 | 1,00 | 12,3 % | 13,0 % |

Ein sauberer monotoner Verlauf über 5.607 Posts und 180 Kanäle — und die einzige
Struktur-Aussage, die alle drei Korrekturrunden überlebt hat.

**Die Grauzone hat sich empirisch bestätigt.** Sie war eine Konstruktionsentscheidung
mit dem Argument „ein 75-Sekunden-Stück kann ein kurzer Trailer oder ein langer Spot
sein". Die Daten sagen jetzt: der 60–89-Sekunden-Bereich liegt mit 13,3 % gegen ~13 %
Erwartung **exakt auf dem Durchschnitt** — er verhält sich weder wie Langform noch wie
Kurzform.

Das erklärt auch, warum `3_langform` mit 16,9 % **besser abschneidet als der rohe
`>60s`-Bucket mit 15,2 %**, obwohl `>60s` die Langformate enthält: die 758 Posts der
Grauzone verwässern ihn. Wäre die Grauzone der Langform zugeschlagen worden, wäre der
Befund auf den `>60s`-Wert zusammengeschrumpft.

### 16.3 Was sonst noch übrig bleibt: fast nichts

Nur drei der 28 Zellen erreichen plausibel |z| ≥ 2: `behind_the_scenes`,
`format_class 3_langform` und `duration_bucket >60s`. Alles ab Platz sechs liegt
zwischen z = +0,96 und z = −1,75.

`behind_the_scenes` ist mit 28,1 % Trefferquote die auffälligste Einzelzelle, hat aber
nur 32 Posts über 19 Kanäle. Bei 28 geprüften Zellen ist rund ein Zufallstreffer bei
|z| ≥ 2 zu erwarten (siehe `_breakout_z`, Absatz „Bekannte Grenze") — und die kleinste
der drei Zellen ist der wahrscheinlichste Kandidat dafür. Vorerst als Hypothese führen,
nicht als Befund.

Bemerkenswert im Negativen:

- **`trailer` (13,5 % gegen 15,4 % erwartet) und `teaser` (8,0 % gegen 13,7 %) sind
  unauffällig bis schwach.** Das Signal sitzt in der *Dauerklasse*, nicht im
  Format-Label. Ein als „Trailer" klassifizierter Post läuft nicht besser; ein Post ab
  90 Sekunden schon.
- **`music_kind` ist unverändert** (2.065 / 228 Posts, 10,1 % / 9,6 %). Erwartet, weil
  TikTok keine Posts ohne Views hat — die Korrektur konnte dort nichts ändern. Eine
  saubere Gegenprobe, dass sie nur dort gewirkt hat, wo sie sollte.

### 16.4 Konsequenz

Die Metadaten-Route ist damit ausgeschöpft. Nach drei Korrekturrunden über denselben
Datensatz bleibt **eine** belastbare Aussage: *Formate ab 90 Sekunden produzieren
überdurchschnittlich oft Ausreißer, Formate unter 60 Sekunden unterdurchschnittlich —
und der Bereich dazwischen ist genau das: der Bereich dazwischen.*

Das ist zugleich das Fundament für Stufe 3: die Frage ist nicht mehr, *ob* Langform
anders funktioniert, sondern *warum*. Und diese Antwort steckt in Hook, Schnitt und
Aufbau — nicht in Metadaten.
