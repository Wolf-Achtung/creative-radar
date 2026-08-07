# Briefing Montag, 10.08.2026 — Kontrolle und nächste Schritte

Ein Arbeitszettel, kein Bericht. Alle Abfragen sind gegen das echte Schema syntaxgeprüft
und lassen sich direkt kopieren. Reihenfolge einhalten: Schritt B hängt an A, D hängt an C.

---

## Teil 1 — Wo wir stehen

### Was in den letzten Tagen entstanden ist

| PR | Inhalt | Status |
|---|---|---|
| #351 | Stufe-5-Fundament: Merkmalsberechnung, zwei Rangtests, `video_feature`-Tabelle | gemergt |
| #352 | Plan-B-Werkzeuge: Tap-Along-Annotator, Kalibrier-Clips, Auswertungs-CLI | gemergt |
| #353 | P3-Auswertung: aus 47 Paaren werden 20, Query P4 | gemergt |

### Die Kette in einem Absatz

Stufe 3 hat den Suchraum festgelegt: *Was unterscheidet ein YouTube-Langformat ab 90
Sekunden handwerklich von einem Cutdown unter 60 — bei gleichem Kanal, gleichem Titel und
gleicher Produktionsroutine?* Vier konkurrierende Erklärungen sind ausgeschlossen, übrig
bleibt das Handwerk. Das steckt im Bewegtbild, und an Bewegtbild kommen wir wegen der
YouTube-Nutzungsbedingungen nicht per Download. **Plan B löst das, indem ein Mensch die
Schnitte im offiziellen Player mittippt** — die Werkzeuge dafür stehen und sind getestet.

### Die zwei Zahlen, die den Montag bestimmen

1. **20 verwertbare Annotations-Paare.** Aus 47 Rohtreffern nach drei Filtern (Langform
   über 180 s = kein Trailer, Titel-Platzhalter, regionale Doppelungen desselben Films).
   Genau die benötigte Menge, **ohne Reserve**.
2. **Frist 26.08.** Dann fallen 1.210 Posts vom 28.05. aus dem 90-Tage-Fenster. Passiert
   bis dahin nichts, sinkt die Klassifikations-Abdeckung nahe null und `format`, `tone`
   und `lifecycle_stage` sind als Dimensionen unbrauchbar.

Beides hängt am selben Cron-Lauf: **Montag, 10.08., 03:00 UTC = 05:00 Uhr deutscher
Zeit** (`_TRIGGER_WEEKDAY = 0`, `_TRIGGER_HOUR_UTC = 3`). Es ist der erste Lauf mit dem
von Wolf gesetzten `CRON_POST_ANALYSIS_MAX_POSTS_PER_RUN = 2500`.

---

## Teil 2 — Montag: die Kontrolle *(15 Minuten)*

Alles Read-only gegen die Produktions-Datenbank. Kein `UPDATE`, kein `DELETE`.

### Schritt A — Lief die Stage überhaupt, und mit welchen Zahlen?

```sql
SELECT started_at, status, summary_json -> 'post_analysis' AS post_analysis_stage
FROM creative_radar.cron_run ORDER BY started_at DESC LIMIT 1;
```

**Worauf zu achten ist:**

| Feld | erwartet | wenn nicht |
|---|---|---|
| `started_at` | Montag, 03:0x UTC | Scheduler hat nicht ausgelöst → Schritt A2 |
| `status` | `success` | siehe Fehlerpfade unten |
| `post_analysis_stage` | ein Objekt, **nicht** `null` | Stage lief nicht → Schritt A2 |
| `attempted` darin | nahe 2500 | siehe unten |
| `analyzed` darin | nahe `attempted` | viele `errors` → Screenshot hierher |
| `duration_seconds` darin | **die wichtigste Zahl** | siehe Schritt E |

Bisher stand dort zehnmal in Folge `null` — deshalb ist genau dieses Feld der erste
Blick. Steht `attempted` bei rund 800 statt 2500, hat die Variable nicht gegriffen.

### Schritt A2 — nur falls die Stage *nicht* lief

In Railway prüfen, ob `CRON_POST_ANALYSIS_MAX_POSTS_PER_RUN` in den **Production**-
Variablen steht (nicht nur in Staging) und ob der Wert `2500` ist. Screenshot der
Variablenliste hierher, dann klären wir gemeinsam. **Nicht** von Hand nachtriggern, bevor
die Ursache klar ist.

### Schritt B — Ist die Abdeckung gestiegen?

```sql
SELECT count(*)                                                               AS posts_im_fenster,
       count(*) FILTER (WHERE p.analysis::jsonb ? 'format')                   AS mit_format,
       round(100.0 * count(*) FILTER (WHERE p.analysis::jsonb ? 'format')
             / NULLIF(count(*), 0), 1)                                        AS abdeckung_prozent,
       count(*) FILTER (WHERE p.duration_seconds IS NOT NULL)                 AS mit_dauer,
       round(100.0 * count(*) FILTER (WHERE p.duration_seconds IS NOT NULL)
             / NULLIF(count(*), 0), 1)                                        AS dauer_prozent
FROM creative_radar.post p
WHERE p.detected_at > now() - interval '90 days';
```

Der `::jsonb`-Cast ist nötig: `analysis` ist eine `json`-Spalte, und der `?`-Operator
existiert nur für `jsonb`.

**Erwartung:** `abdeckung_prozent` springt von rund 12–14 % auf etwa 40 %. Bleibt sie
unverändert, obwohl Schritt A `analyzed > 0` meldet, stimmt etwas zwischen Stage und
Datenbank nicht — Screenshot beider Ergebnisse hierher.

`dauer_prozent` ist die Zahl für Plan B: je höher, desto mehr Annotations-Paare liefert
Schritt C.

### Schritt C — Wie viele Paare haben wir jetzt? *(Query P4)*

Die bereinigte Arbeitsliste. Ersetzt P3 — sie filtert alles heraus, was die Auswertung
vom Freitag als untauglich erwiesen hat.

```sql
WITH post_titel AS (
  SELECT DISTINCT p.id, p.channel_id, p.duration_seconds, p.post_url, a.title_id
  FROM creative_radar.post p
  JOIN creative_radar.asset a   ON a.post_id = p.id
  JOIN creative_radar.channel c ON c.id = p.channel_id
  JOIN creative_radar.title t   ON t.id = a.title_id
  WHERE a.title_id IS NOT NULL
    AND c.platform = 'youtube'
    AND p.detected_at > now() - interval '90 days'
    AND p.duration_seconds IS NOT NULL
    AND t.title_original NOT IN ('Unknown', 'Star Wars')
),
kandidaten AS (
  SELECT channel_id, title_id
  FROM post_titel
  GROUP BY 1, 2
  HAVING count(*) FILTER (WHERE duration_seconds BETWEEN 90 AND 180) > 0
     AND count(*) FILTER (WHERE duration_seconds BETWEEN 15 AND 59)  > 0
),
lang AS (
  SELECT DISTINCT ON (channel_id, title_id) channel_id, title_id, post_url, duration_seconds
  FROM post_titel WHERE duration_seconds BETWEEN 90 AND 180
  ORDER BY channel_id, title_id, duration_seconds ASC
),
kurz AS (
  SELECT DISTINCT ON (channel_id, title_id) channel_id, title_id, post_url, duration_seconds
  FROM post_titel WHERE duration_seconds BETWEEN 15 AND 59
  ORDER BY channel_id, title_id, duration_seconds DESC
),
je_titel AS (
  SELECT DISTINCT ON (k.title_id)
         k.channel_id, k.title_id, k.duration_seconds AS kurz_s, k.post_url AS kurz_url
  FROM kandidaten kd
  JOIN kurz k ON k.channel_id = kd.channel_id AND k.title_id = kd.title_id
  ORDER BY k.title_id, k.duration_seconds DESC
)
SELECT row_number() OVER (ORDER BY t.title_original) AS nr,
       regexp_replace(lower(t.title_original), '[^a-z0-9]+', '-', 'g') AS pair_key,
       t.title_original AS titel,
       c.name           AS kanal,
       round(l.duration_seconds) AS lang_s,
       l.post_url                AS langform_url,
       round(j.kurz_s)           AS kurz_s,
       j.kurz_url                AS kurzform_url
FROM je_titel j
JOIN lang l ON l.channel_id = j.channel_id AND l.title_id = j.title_id
JOIN creative_radar.channel c ON c.id = j.channel_id
JOIN creative_radar.title t   ON t.id = j.title_id
ORDER BY t.title_original;
```

**Entscheidungsregel:**

| Zeilenzahl | was das heißt | was zu tun ist |
|---:|---|---|
| ≥ 25 | komfortabel | Teil 3 starten |
| 20–24 | reicht, ohne Reserve | Teil 3 starten, aber sorgfältig aussortieren |
| < 20 | zu wenig | **nicht** anfangen — melden, dann Grenzfälle 180–240 s nachziehen |

Die Spalte `pair_key` ist so gebaut, dass sie direkt in den Annotator kopiert werden kann.
**Ergebnis als Screenshot oder CSV hierher** — auch wenn es reicht, denn die Liste ist die
Grundlage für alles Weitere.

### Schritt D — die „Unknown"-Frage klären *(2 Minuten, optional aber empfohlen)*

```sql
SELECT t.id, t.title_original, t.tmdb_id, t.source, t.content_type,
       count(DISTINCT a.post_id)    AS posts,
       count(DISTINCT p.channel_id) AS kanaele
FROM creative_radar.title t
LEFT JOIN creative_radar.asset a ON a.title_id = t.id
LEFT JOIN creative_radar.post p  ON p.id = a.post_id
WHERE t.title_original IN ('Unknown', 'Star Wars')
GROUP BY 1, 2, 3, 4, 5
ORDER BY posts DESC;
```

**Eine Zeile mit vielen Kanälen** = Sammelbecken, ein Zuordnungsartefakt, das über Stufe 5
hinaus Folgen hat. **Mehrere Zeilen mit je einem Kanal** = harmloser, dann ist *Unknown*
tatsächlich der Universal-Film von 2011. Ergebnis hierher, unabhängig vom Ausgang.

### Schritt E — den Cap für den nächsten Lauf setzen

Aus Schritt A die Zahl `duration_seconds` nehmen — die erste echte Messung statt einer
Schätzung.

| gemessene Laufzeit bei 2500 Posts | Empfehlung für nächste Woche |
|---|---|
| unter 60 Minuten | Cap auf **4000** anheben, Backlog früher leer |
| 60–150 Minuten | bei **2500** bleiben |
| über 150 Minuten | auf **1500** senken, Timeout-Risiko |

Restbacklog zur Kontrolle:

```sql
SELECT count(*) AS backlog_ohne_format
FROM creative_radar.post p
WHERE p.detected_at > now() - interval '90 days'
  AND NOT (COALESCE(p.analysis, '{}')::jsonb ? 'format');
```

Geteilt durch den Cap ergibt die Zahl der noch nötigen Wochen. **Muss vor dem 26.08. auf
null gehen** — bleiben mehr als zwei Läufe übrig, ist der Cap zu niedrig.

---

## Teil 3 — Danach: die Annotation *(einmal 20 Minuten, dann ein Nachmittag)*

Erst starten, wenn Schritt C mindestens 20 Zeilen geliefert hat.

### Schritt F — Kalibrieren *(einmalig, ~20 Minuten)*

Macht aus Annahmen über das Tippen Messwerte. **Nicht überspringen** — ohne diesen Schritt
sind die späteren Zahlen nicht belastbar.

```bash
git pull
brew install ffmpeg          # nur beim ersten Mal
cd backend
python scripts/make_calibration_clips.py --out kalibrierung --clips 3
cd ..
python3 -m http.server
```

Dann im Browser `http://localhost:8000/tools/tap_annotator.html`:

1. Modus **„Lokale Datei"**, `backend/kalibrierung/clip_01.mp4` laden
2. Kürzel eintragen (z. B. `WH`), `pair_key` = `kalib-01`, Klasse egal
3. Tempo **0,5×**, Start drücken
4. **Bei jedem Farbwechsel Leertaste.** Nicht vorausahnen — reagieren
5. „JSON exportieren", Datei merken
6. Für Clip 02 und 03 wiederholen

Auswerten:

```bash
cd backend
python scripts/evaluate_annotations.py calibrate \
    --annotation ~/Downloads/kalib-01_langform.json \
    --truth kalibrierung/clip_01.wahrheit.json
```

**Zielwerte:**

| Kennzahl | gut | Handlungsbedarf |
|---|---|---|
| Trefferquote | > 95 % | unter 90 % → langsamer abspielen (0,25×) |
| Präzision | > 95 % | unter 90 % → weniger nervös tippen |
| Latenz-Mittel | beliebig | **egal — kürzt sich heraus** |
| Latenz-Streuung | < 0,10 s | über 0,15 s → nochmal üben |
| ASL-Verhältnis | 0,95–1,10 | deutlich über 1,1 → Schnitte werden verpasst |

Alle drei Berichte hierher. Erst wenn sie stimmen, geht es an echtes Material.

### Schritt G — Die Paare annotieren *(3–4 Stunden, teilbar)*

Je Zeile aus der P4-Liste **zwei** Durchgänge, Langform und Kurzform:

1. Modus **„YouTube"**, `langform_url` einsetzen, „Laden"
2. Kürzel, **`pair_key` aus der Liste** (beide Seiten desselben Paars: identischer Wert!),
   Klasse `Langform`
3. Die zwei Checkboxen **ehrlich** setzen:
   - *„ist ein Trailer"* — abhaken, wenn es ein Featurette, Interview oder Clip ist
   - *„Kurzform ist erkennbar ein Cutdown"* — nur bei der Kurzform relevant: sieht man
     dieselben Szenen wie in der Langform?
4. Tempo 0,5×, Start, **Leertaste bei jedem Schnitt**
5. `M` beim Musikeinsatz, `T` bei jeder Titelkarte, `W` beim ersten gesprochenen Wort
6. Verklickt? `U` löscht den letzten Schnitt
7. Exportieren, **alle Dateien in einen gemeinsamen Ordner**
8. Dasselbe mit `kurzform_url`, Klasse `Kurzform`, **gleicher `pair_key`**

**Zwei Fallen:**

- **Titel wie „Personality", „Driven", „At Home", „The Greatest"** sind Allerweltswörter.
  Passt das Video wirklich zum Titel? Wenn nicht: „ist ein Trailer" abhaken, dann fliegt
  das Paar sauber heraus statt die Zahlen zu verfälschen.
- **Der `pair_key` muss auf beiden Seiten identisch sein.** Ein Tippfehler macht aus einem
  Paar zwei halbe, die stillschweigend übersprungen werden. Die Auswertung meldet das,
  aber besser gleich richtig.

### Schritt H — Auswerten

```bash
cd backend
python scripts/evaluate_annotations.py pairs --dir ~/annotationen/
```

Ausgabe hierher — das ist die erste echte Antwort auf die Stufe-3-Frage. Getrennt
ausgewiesen werden echte Cutdowns (der Kern-PoC) und eigenständige Kurzformate.

---

## Teil 4 — Was offen bleibt

| Punkt | Art | Dringlichkeit |
|---|---|---|
| Trailerhaus-Fragebögen | wartet auf Antwort | Plan A, nicht blockierend |
| Formatklasse `langform` auf YouTube ist inhomogen | offene Frage aus #353 | eigener Schritt, lohnt sich |
| Instagram als Kontrollplattform | **Entscheidung Wolf**, kein Code | offen, nicht dringend |
| Alembic-Kette läuft nicht von null | bekannt, im CI dokumentiert | nur bei Neuaufsetzen relevant |

Der zweite Punkt ist der interessanteste: Die P3-Auswertung hat gezeigt, dass rund ein
Drittel der vermeintlichen YouTube-Langform-Posts gar keine Trailer sind, sondern
Katalog-Clips und Featurettes. Für den Stufe-3-Befund ist das **weder Entwarnung noch
Widerlegung** — aber es ist eine Frage, die eine eigene Prüfung verdient.

---

## Spickzettel

```
MONTAG
  A  cron_run-Abfrage        →  lief die Stage? duration_seconds notieren
  B  Abdeckungs-Abfrage      →  Sprung auf ~40 %?
  C  Query P4                →  ≥ 20 Zeilen? Liste sichern
  D  Unknown-Abfrage         →  Sammelbecken oder echter Film?
  E  Cap-Entscheidung        →  Backlog / Cap < 2 Wochen bis 26.08.?

DANACH
  F  3 Kalibrier-Clips tippen und auswerten
  G  20 Paare annotieren, gleicher pair_key je Paar
  H  evaluate_annotations.py pairs

ALLES read-only. Ergebnisse aus A–D und F, H in den Chat.
```
