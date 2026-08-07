# Trailer-Intelligence Stufe 3, Schritt 1 — den Langform-Vorsprung eingrenzen

Stand: 07.08.2026. Baut auf dem einzigen Befund auf, den
[Stufe 1](./TRAILER_INTELLIGENCE_STAGE1_ERGEBNISSE.md) nach drei Korrekturrunden
übrig gelassen hat.

## 1. Der Ausgangspunkt

| Klasse | Posts | Kanäle | Treffer | erwartet | z |
|---|---:|---:|---:|---:|---:|
| Langform (≥ 90 s) | 869 | 176 | 16,9 % | 14,2 % | **+2,27** |
| Übergang (60–89 s) | 758 | 153 | 13,3 % | 13,4 % | −0,04 |
| Kurzform (< 60 s) | 3.980 | 180 | 12,3 % | 13,0 % | −1,25 |

Format-Label, Ton, Musik und Lebenszyklus tragen nichts bei — von 28 geprüften Zellen
erreichten nur zwei die Schwelle, und eine davon (`behind_the_scenes`, n = 32) ist der
wahrscheinlichste Zufallstreffer.

Die Frage ist damit nicht mehr *ob*, sondern *warum*.

## 2. Was dieses Modul leistet — und was ausdrücklich nicht

Es beantwortet das *Warum* **nicht**. Es grenzt ein, welche Erklärungen es **nicht**
sind. Das ist an dieser Stelle der ehrliche Beitrag: die Antwort auf das Warum steckt
sehr wahrscheinlich im Videomaterial, und das erhebt Creative Radar noch nicht.

Es gibt mindestens fünf konkurrierende Erklärungen. Vier lassen sich mit vorhandenen
Daten prüfen:

| # | Erklärung | prüfbar mit |
|---|---|---|
| 1 | **Auswahl** — nur hochwertige Assets werden überhaupt so lang produziert; die Länge ist Marker für Investition, nicht Ursache | Titel-Match, Kanal-Gewohnheit |
| 2 | **Plattform** — Langform trägt nur dort, wo der Player es hergibt | Plattform-Schichtung |
| 3 | **Release-Fenster** — Langform häuft sich nahe am Kinostart, wo das Interesse ohnehin hoch ist | `days_to_release` über `Title.release_date` |
| 4 | **Markt** — ein DE/US/UK-Effekt | Markt-Schichtung |
| 5 | **Handwerk** — Langform hat Raum für Aufbau, Wendepunkt, Auflösung | **nur mit Video (Stufe 5)** |

Das Modul rechnet den Langform-Vorsprung **innerhalb** jeder Schicht neu. Verschwindet
er dort, erklärt diese Schicht ihn. Überlebt er überall, bleibt Erklärung 5 — und dann
ist belegt, dass die Antwort in der Ausführung steckt und nicht in Metadaten.

Kennzahl der Ausgabe: **`survives_in` von `tested_strata`**.

## 3. Warum ein anderer Test als in Stufe 1

Stufe 1 prüft jede Zelle gegen einen Erwartungswert aus ihrer Plattform-Mischung. Für
die Frage „erklärt Schicht X den Vorsprung?" ist das der falsche Test — dort geht es
nicht um Abweichung von einer Erwartung, sondern um die **Differenz zweier Gruppen im
selben Kontext**.

Deshalb hier ein Zwei-Stichproben-z-Test auf Anteilsdifferenz mit gepooltem
Standardfehler:

```
p_pool = (treffer_lang + treffer_kurz) / (n_lang + n_kurz)
se     = sqrt(p_pool · (1 − p_pool) · (1/n_lang + 1/n_kurz))
z      = (rate_lang − rate_kurz) / se
```

Gepoolt, weil unter der Nullhypothese beide Seiten Schätzungen mit eigener Unsicherheit
sind — anders als in Stufe 1, wo gegen einen berechneten Erwartungswert geprüft wird.

**Die Übergangszone bleibt außen vor.** Sie liegt statistisch exakt in der Mitte
(z = −0,04) und würde beide Arme verwässern. Sie wird als Kontextzahl mitgeführt, aber
nicht verglichen.

## 4. Ehrlichkeits-Regeln

1. Kanal-Baselines und Lift kommen aus `build_lift_context` — **dieselbe**
   Implementierung wie Stufe 1. Bewusst geteilt statt kopiert: dort stecken die Regeln,
   die drei Korrekturrunden gekostet haben (Mindest-Postzahl je Kanal, Ausschluss der
   Posts ohne messbare Views, Median als Baseline). Zwei Kopien liefen auseinander, und
   dann wären die Zahlen beider Stufen nicht mehr vergleichbar.
2. Eine Schicht braucht in **beiden** Armen mindestens 30 Posts. Ein Vergleich von 400
   gegen 7 ist keine Schichtung, sondern Rauschen mit Etikett.
3. Schichten unter der Schwelle verschwinden nicht — sie werden mit
   `verdict="insufficient"` und Grund gemeldet.
4. **`survives_in` zählt nur belastbare Schichten.** Eine Schicht, die mangels Daten
   kein Urteil zulässt, ist kein bestandener Test. Sonst ließe sich die Bilanz durch
   dünne Schichten schönen.
5. Ohne Gesamtvorsprung ist die Schichtung gegenstandslos — das steht dann in den
   `notes`, damit niemand die Schicht-Tabelle als Befund liest.

## 5. Der Dauer-Gradient

Eine eigene Frage, absichtlich getrennt geführt: Bänder **innerhalb** der Langform
(90–120 s, 120–180 s, 180–300 s, > 300 s), jeweils gegen dieselbe Kurzform-Kontrolle.

- Wächst der Vorsprung mit der Länge weiter → „mehr Raum für Aufbau" ist plausibel.
- Springt er bei 90 Sekunden und bleibt flach → es ist eine Format-Konvention.

Der Gradient geht **nicht** in `survives_in` ein, weil er keine konkurrierende
Erklärung testet.

## 6. Abruf

```
GET /api/admin/langform?window_days=90
GET /api/admin/langform?window_days=90&market=DE
GET /api/admin/langform?min_posts_per_arm=15     # kleinere Zuschnitte
```

Rein lesend, kein Modell-Call, kein Budget-Effekt.

## 7. Dieselbe Rechnung als SQL

Für die Railway-Konsole, wo kein Bearer-Token mitgeht. Gegen ein lokales Postgres 16
geprüft, mit zwei Fällen:

- **Plattform-Confound** (innerhalb jeder Plattform kein Effekt, aber Langform liegt
  auf der starken Plattform) → GESAMT z = 3,79, je Plattform z = 0,00. Die Schichtung
  löst den Scheinbefund korrekt auf.
- **Echter Effekt** (in beiden Plattformen Langform 50 % gegen Kurzform 10 %) → GESAMT
  z = 10,94, je Plattform z = 7,74. Der Befund überlebt korrekt.

```sql
WITH scoped AS (
  SELECT p.id, p.channel_id, c.platform, c.market, p.duration_seconds,
         CASE WHEN c.platform='youtube' THEN (GREATEST(COALESCE(p.visible_likes,0),0)+GREATEST(COALESCE(p.visible_comments,0),0))::numeric/p.visible_views
              ELSE (GREATEST(COALESCE(p.visible_likes,0),0)+GREATEST(COALESCE(p.visible_comments,0),0)+GREATEST(COALESCE(p.visible_bookmarks,0),0))::numeric/p.visible_views END AS activation
  FROM creative_radar.post p JOIN creative_radar.channel c ON c.id=p.channel_id
  WHERE p.detected_at > now() - interval '90 days'
    AND COALESCE(p.visible_views,0) > 0
), baseline AS (
  SELECT channel_id, percentile_cont(0.5) WITHIN GROUP (ORDER BY activation) AS med
  FROM scoped GROUP BY channel_id HAVING count(*) >= 4
), lifted AS (
  SELECT s.*, s.activation/b.med AS lift
  FROM scoped s JOIN baseline b ON b.channel_id=s.channel_id WHERE b.med > 0
), kohorte AS (
  SELECT *, CASE WHEN duration_seconds >= 90 THEN 'langform'
                 WHEN duration_seconds <  60 THEN 'kurzform' END AS klasse
  FROM lifted WHERE duration_seconds IS NOT NULL
), roh AS (
  SELECT platform AS schicht,
         count(*) FILTER (WHERE klasse='langform')                          AS lang_posts,
         count(*) FILTER (WHERE klasse='kurzform')                          AS kurz_posts,
         COALESCE(count(*) FILTER (WHERE klasse='langform' AND lift>=2.0)::numeric
                  / NULLIF(count(*) FILTER (WHERE klasse='langform'),0), 0) AS lang_rate,
         COALESCE(count(*) FILTER (WHERE klasse='kurzform' AND lift>=2.0)::numeric
                  / NULLIF(count(*) FILTER (WHERE klasse='kurzform'),0), 0) AS kurz_rate,
         count(*) FILTER (WHERE lift>=2.0)::numeric / NULLIF(count(*),0)    AS p_pool
  FROM kohorte WHERE klasse IS NOT NULL GROUP BY platform
  UNION ALL
  SELECT 'GESAMT',
         count(*) FILTER (WHERE klasse='langform'),
         count(*) FILTER (WHERE klasse='kurzform'),
         COALESCE(count(*) FILTER (WHERE klasse='langform' AND lift>=2.0)::numeric
                  / NULLIF(count(*) FILTER (WHERE klasse='langform'),0), 0),
         COALESCE(count(*) FILTER (WHERE klasse='kurzform' AND lift>=2.0)::numeric
                  / NULLIF(count(*) FILTER (WHERE klasse='kurzform'),0), 0),
         count(*) FILTER (WHERE lift>=2.0)::numeric / NULLIF(count(*),0)
  FROM kohorte WHERE klasse IS NOT NULL
)
SELECT schicht,
       lang_posts, round(100.0*lang_rate,1) AS lang_pct,
       kurz_posts, round(100.0*kurz_rate,1) AS kurz_pct,
       round(100.0*(lang_rate-kurz_rate),1) AS gap_pp,
       CASE WHEN lang_posts >= 30 AND kurz_posts >= 30 AND p_pool > 0 AND p_pool < 1
            THEN round(((lang_rate-kurz_rate)
                 / sqrt(p_pool*(1-p_pool)*(1.0/lang_posts + 1.0/kurz_posts)))::numeric, 2)
       END AS z
FROM roh
ORDER BY (schicht='GESAMT') DESC, z DESC NULLS LAST;
```

Für die Markt-Schichtung `platform` durch `market` ersetzen (zwei Stellen: im `SELECT`
von `roh` und im `GROUP BY`). Titel-Match, Kanal-Gewohnheit und Release-Fenster brauchen
Joins über `asset` und `title` — dafür ist der Endpunkt der bequemere Weg.

## 8. Wie das Ergebnis zu lesen ist

| Ausgang | Bedeutung | nächster Schritt |
|---|---|---|
| `explained_by` enthält `platform` | Der Vorsprung ist ein Plattform-Effekt | Langform je Plattform getrennt betrachten, Befund aus Stufe 1 relativieren |
| `explained_by` enthält `title_match` oder `channel_habit` | Auswahl-Effekt: Länge ist Marker für Investition | Die Frage verschiebt sich auf „was macht ein hochinvestiertes Asset aus" |
| `explained_by` enthält `days_to_release` | Release-Fenster-Effekt | Langform-Empfehlung an das Zeitfenster koppeln, nicht an die Länge |
| `explained_by` ist leer, `survives_in == tested_strata` | Keine Metadaten-Erklärung trägt | **Stufe 5 ist gerechtfertigt** — die Antwort steckt in der Ausführung |

Der letzte Fall ist der wahrscheinlichste, gemessen an dem, was Stufe 1 gezeigt hat.
Er ist aber kein Freibrief: er sagt nur, dass die vorhandenen Metadaten es nicht
erklären, nicht dass es das Handwerk *ist*. Diese Aussage ließe sich erst mit
Videomerkmalen belegen.

## 9. Anschluss an den Trailerhaus-Fragebogen

Läuft der Bericht mit leerem `explained_by` durch, ist das die empirische Grundlage für
die Fragen, die im Fragebogen ohnehin gestellt sind: Hook, Schnittfrequenz,
Musikeinsatz, Aufbau. Dann liegt eine gemessene Frage vor, auf die Menschen antworten
können — statt einer Vermutung, die sie bestätigen sollen.

Umgekehrt: erklärt eine Schicht den Vorsprung, gehört das den Befragten mitgeteilt,
bevor sie über Handwerk sprechen. Sonst diskutieren alle über eine Wirkung, die es so
nicht gibt.
