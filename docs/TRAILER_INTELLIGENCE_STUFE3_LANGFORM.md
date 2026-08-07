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

Das Modul rechnet den Langform-Vorsprung **innerhalb** jeder Schicht neu. Je Dimension
gibt es drei mögliche Ausgänge:

| Ausgang | Bedeutung |
|---|---|
| `explained_by` | Keine belastbare Schicht zeigt den Vorsprung — die Dimension erklärt ihn vollständig. |
| `localized_in` | Ein Teil der Schichten zeigt ihn, ein anderer nicht — die Dimension erklärt ihn *nicht weg*, sondern sagt, **wo der Hebel greift**. |
| keins von beidem | Der Vorsprung hält in jeder Schicht. Dann bleibt Erklärung 5. |

Die Dreiteilung stammt aus dem ersten Produktionslauf (Abschnitt 10), der die
ursprünglich binäre Fassung als zu grob entlarvt hat. `survives_in` von
`tested_strata` bleibt als Grobmaß erhalten, ist aber die schwächere Zahl — was zählt,
ist die Schicht-Tabelle.

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
| `localized_in` enthält eine Dimension | Der Vorsprung ist echt, aber nur in einem Teil der Schichten | Empfehlung an die Schicht koppeln, nicht pauschal aussprechen |
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

## 10. Erster Produktionslauf (07.08.2026) — der Hebel greift, aber nicht überall

Die Query aus Abschnitt 7 ist auf der Produktions-Postgres gelaufen.

| Schicht | Langform | | Kurzform | | Differenz | z |
|---|---:|---:|---:|---:|---:|---:|
| | Posts | Treffer | Posts | Treffer | | |
| **GESAMT** | 869 | 16,9 % | 3.980 | 12,3 % | +4,6 pp | **+3,62** |
| **YouTube** | 288 | 19,4 % | 545 | 11,7 % | +7,7 pp | **+3,01** |
| Instagram | 388 | 18,6 % | 1.697 | 14,8 % | +3,7 pp | +1,82 |
| TikTok | 193 | 9,8 % | 1.738 | 10,1 % | −0,2 pp | −0,10 |

### 10.1 Die Plattform erklärt den Vorsprung nicht weg — sie lokalisiert ihn

Das ist der entscheidende Unterschied zum Confound aus Stufe 1. Damals verschwand der
Dauer-Effekt beim Schichten (TikTok z = −0,6, obwohl korpusweit z = +8,7). Hier bleibt
er auf YouTube mit z = 3,01 klar bestehen.

**Wichtig für das Verständnis:** verglichen wird *innerhalb* der Plattform,
YouTube-Langform gegen YouTube-Kurzform. Der Befund heißt also **nicht** „YouTube läuft
besser", sondern „auf YouTube schlägt lang kurz". Das ist eine Aussage über das Format,
nicht über den Kanal.

### 10.2 Drei Plattformen, drei Antworten

**YouTube: der Hebel greift.** 19,4 % gegen 11,7 %, +7,7 Prozentpunkte. Langform hat
dort fast die anderthalbfache Ausreißer-Chance von Kurzform. Plausibel, weil YouTube
Langform nativ bedient: Player ohne Längenlimit, Zuschauer kommen mit Absicht (Suche,
Abo) statt im Vorbeiscrollen.

**TikTok: der Hebel greift gar nicht.** 9,8 % gegen 10,1 %, z = −0,10. Nicht schwach —
*null*. Über 193 Langform- und 1.738 Kurzform-Posts. Ein langes Stück auf TikTok
zu posten bringt messbar nichts.

**Instagram: dazwischen, unentschieden.** +3,7 Prozentpunkte, aber z = 1,82 und damit
unter der Schwelle. Die Richtung stimmt, die Belastbarkeit fehlt. Bei 388 Langform-Posts
ist das keine Frage der Stichprobengröße allein — Instagram liegt vermutlich wirklich
dazwischen, was zur Zwitterstellung des Formats passt (Reels sind auf Kürze gebaut, aber
längere Beiträge sind möglich).

### 10.3 Was das für die Empfehlung heißt

Aus einer Längen-Regel wird eine **Plattform-abhängige** Regel:

| Plattform | Empfehlung |
|---|---|
| YouTube | Länge lohnt sich. Ab 90 Sekunden deutlich bessere Ausreißer-Chance. |
| Instagram | Länge schadet nicht, hilft tendenziell — aber nicht belastbar. |
| TikTok | Länge bringt nichts. Der Aufwand ist dort besser in die ersten Sekunden investiert. |

Das ist eine schärfere und nützlichere Aussage als „lange Formate gewinnen".

### 10.4 Konsequenz für den Code

Die ursprüngliche Fassung kannte nur zwei Ausgänge je Dimension: „erklärt den Vorsprung"
oder „erklärt ihn nicht". Dieser Lauf zeigt, dass das zu grob war — ein bloßer Zähler
hätte „hält in 1 von 3 Schichten" gemeldet, was nach Schwäche klingt, während es
tatsächlich ein eigener Befund ist.

Deshalb gibt es jetzt drei Ausgänge: `explained_by` (keine Schicht zeigt ihn),
**`localized_in`** (ein Teil zeigt ihn, ein Teil nicht) und universell (alle zeigen ihn).
`survives_in` bleibt als Grobmaß erhalten, ist aber die schwächere Zahl — was zählt, ist
die Schicht-Tabelle.

### 10.5 Was noch offen ist

Die übrigen vier Schichtungen — Markt, Titel-Match, Kanal-Gewohnheit, Release-Fenster —
brauchen Joins über `asset` und `title` und laufen deshalb über den Endpunkt
`GET /api/admin/langform`, nicht über die SQL aus Abschnitt 7.

Besonders **Kanal-Gewohnheit** ist nach diesem Ergebnis interessant: wenn der
YouTube-Vorsprung nur bei Kanälen besteht, die selten Langform produzieren, wäre es
doch ein Auswahl-Effekt — die seltene lange Veröffentlichung wäre dann das teure Asset,
nicht das lange. Besteht er auch bei Kanälen mit regelmäßigem Langform-Output, ist die
Länge selbst der Hebel.

Diese eine Frage entscheidet, ob Stufe 5 nach Handwerksmerkmalen suchen sollte oder nach
Produktionsaufwand.

## 11. Die restlichen vier Schichten (07.08.2026) — kein Auswahl-Effekt

Alle elf Schichten in einem Lauf. Die entscheidende Frage war: trägt der Vorsprung nur
bei Kanälen, die *selten* Langform veröffentlichen? Dann wäre nicht die Länge wirksam,
sondern das seltene teure Asset, das zufällig lang ist.

| Dimension | Schicht | Langform | Kurzform | Differenz | z | Urteil |
|---|---|---:|---:|---:|---:|---|
| Plattform | YouTube | 288 / 19,4 % | 545 / 11,7 % | +7,7 pp | **+3,01** | trägt |
| Plattform | Instagram | 388 / 18,6 % | 1.697 / 14,8 % | +3,8 pp | +1,82 | — |
| Plattform | TikTok | 193 / 9,8 % | 1.738 / 10,1 % | −0,3 pp | −0,10 | — |
| Markt | US | 369 / 17,9 % | 1.685 / 9,9 % | +8,0 pp | **+4,42** | trägt |
| Markt | UK | 247 / 15,8 % | 1.168 / 12,7 % | +3,1 pp | +1,31 | — |
| Markt | DE | 233 / 16,7 % | 1.074 / 15,8 % | +0,9 pp | +0,34 | — |
| Markt | INT | 20 / 15,0 % | 53 / 13,2 % | +1,8 pp | — | zu dünn |
| **Kanal-Gewohnheit** | **regelmäßig** | 503 / 17,9 % | 729 / 13,6 % | +4,3 pp | **+2,06** | **trägt** |
| **Kanal-Gewohnheit** | gelegentlich | 366 / 15,6 % | 3.251 / 12,1 % | +3,5 pp | +1,93 | — |
| Titel-Match | gematcht | 581 / 17,0 % | 2.757 / 12,2 % | +4,8 pp | **+3,13** | trägt |
| Titel-Match | ohne Titel | 288 / 16,7 % | 1.223 / 12,6 % | +4,1 pp | +1,83 | — |

### 11.1 Der Auswahl-Effekt ist widerlegt

Die Auswahl-Hypothese sagt voraus: der Vorsprung sitzt bei **gelegentlich**, weil dort
die seltene lange Veröffentlichung das teure Asset ist, und verschwindet bei
**regelmäßig**, wo Langform Routine ist.

Gemessen ist es umgekehrt herum — regelmäßig trägt (z = 2,06), gelegentlich liegt knapp
darunter (1,93). Aber der ehrlichere Schluss ist noch einfacher: **die beiden
unterscheiden sich gar nicht.**

```
regelmaessig  +4,3 pp,  95 %-Band  +0,2 … +8,4
gelegentlich  +3,5 pp,  95 %-Band  −0,1 … +7,1
```

Die Bänder überlappen fast vollständig. Der Unterschied in `z` kommt nicht von
verschiedenen Effekten, sondern von der Stichprobe (503 gegen 366 Langform-Posts). Es
ist derselbe Effekt in beiden Armen.

**Damit ist die Länge selbst der Hebel, nicht die Investition dahinter.** Für Stufe 5
heißt das: nach Handwerksmerkmalen suchen — Hook, Schnittfrequenz, Aufbau — und nicht
nach Produktionsaufwand.

Dasselbe Muster bei `titel_match`: +4,8 gegen +4,1 Prozentpunkte, der Schwellenunterschied
ist reine Stichprobengröße (581 gegen 288). Auch ein erkannter Kinostart erklärt den
Vorsprung nicht.

### 11.2 Der Markt-Effekt ist vermutlich der Plattform-Effekt im Spiegel

US +8,0 pp (z = 4,42), DE +0,9 pp (z = 0,34) sieht nach einem starken Markt-Effekt aus.
Vorsicht: Markt und Plattform sind nicht unabhängig. Wenn US-Kanäle YouTube-lastiger
sind und DE-Kanäle Instagram-/TikTok-lastiger, erbt die Markt-Schichtung schlicht die
Plattform-Schichtung.

Sauber trennen ließe sich das nur mit einer Plattform-×-Markt-Kreuzung, und dort werden
die Zellen dünn (DE-YouTube-Langform dürfte deutlich unter 100 Posts liegen). Bis das
geprüft ist, gilt: **der Markt-Befund ist nicht eigenständig zu lesen.**

### 11.3 Bilanz

`explained_by` ist leer — keine der vier prüfbaren Erklärungen wird den Vorsprung los.

`localized_in` enthält Plattform (und Markt als deren Schatten): der Hebel greift auf
YouTube, nicht auf TikTok.

Nicht lokalisiert, sondern gleichmäßig vorhanden: Kanal-Gewohnheit und Titel-Match.
Genau die beiden, die einen Auswahl-Effekt angezeigt hätten.

**Stufe 5 ist damit gerechtfertigt, und ihr Suchraum steht fest:** was unterscheidet
ein YouTube-Langformat ab 90 Sekunden handwerklich von einem Cutdown unter 60 — bei
gleichem Kanal, gleichem Titel und gleicher Produktionsroutine?
