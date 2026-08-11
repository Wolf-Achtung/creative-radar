# Trailer-Intelligence Stufe 5, Plan B — Wege ohne Wartezeit

Stand: 07.08.2026. Das [Fundament](./TRAILER_INTELLIGENCE_STUFE5_FUNDAMENT.md) steht,
aber sein Abschnitt 9 endet mit einer Abhängigkeit: zwanzig Trailer-plus-Cutdown-Paare
aus dem Bestand des Trailerhauses. Dieses Dokument beantwortet die Anschlussfrage:
**Was geht, wenn das Trailerhaus-Material auf sich warten lässt?** Nicht als Ersatz für
Plan A, sondern damit die Wartezeit keine Leerzeit ist.

## 1. Die Einsicht, die alles ordnet

Das Fundament ist bewusst quellenagnostisch gebaut: `extract_features` nimmt eine
**Shot-Liste**, keine Videodatei. Damit lautet die eigentliche Frage nicht *„Woher kommen
Videos?"*, sondern:

> **Wer oder was erzeugt legal eine Shot-Liste?**

Das ist eine kleinere Frage, und sie hat mehr Antworten. Ein Detektor auf einer Datei ist
nur *eine* davon. Eine andere ist bisher übersehen worden: **ein Mensch, der zuschaut.**

## 2. Weg 1 — wilde Paare plus Tap-Along-Annotation *(Empfehlung)*

### 2.1 Die Paare existieren vermutlich schon in unserer Datenbank

Die Stärke des Trailerhaus-Materials war die Paarung: gleicher Titel, gleicher Kanal,
beide Längen. Aber diese Paarung ist kein Trailerhaus-Privileg — **sie kommt in freier
Wildbahn vor.** Studiokanäle stellen zum selben Film Langform und Cutdown auf denselben
Kanal; genau darauf baute schon die `title_match`-Schicht der Stufe 3
(`Asset.title_id` verbindet Post und Titel).

Zwei Abfragen, gegen das lokale Postgres-Schema syntaxgeprüft. P1 zählt, P2 listet:

```sql
-- P1: Wie viele wilde Paare gibt es, je Plattform?
WITH post_titel AS (
  SELECT DISTINCT p.id, p.channel_id, p.duration_seconds, a.title_id
  FROM creative_radar.post p
  JOIN creative_radar.asset a ON a.post_id = p.id
  WHERE a.title_id IS NOT NULL
    AND p.detected_at > now() - interval '90 days'
    AND p.duration_seconds IS NOT NULL
)
SELECT c.platform,
       count(*)          AS paare,
       sum(x.langformen) AS langform_posts,
       sum(x.kurzformen) AS kurzform_posts
FROM (
  SELECT channel_id, title_id,
         count(*) FILTER (WHERE duration_seconds >= 90) AS langformen,
         count(*) FILTER (WHERE duration_seconds < 60)  AS kurzformen
  FROM post_titel
  GROUP BY channel_id, title_id
  HAVING count(*) FILTER (WHERE duration_seconds >= 90) > 0
     AND count(*) FILTER (WHERE duration_seconds < 60) > 0
) x
JOIN creative_radar.channel c ON c.id = x.channel_id
GROUP BY 1
ORDER BY paare DESC;
```

```sql
-- P2: Die konkreten YouTube-Paare, als Arbeitsliste.
WITH post_titel AS (
  SELECT DISTINCT p.id, p.channel_id, p.duration_seconds, p.post_url, a.title_id
  FROM creative_radar.post p
  JOIN creative_radar.asset a ON a.post_id = p.id
  WHERE a.title_id IS NOT NULL
    AND p.detected_at > now() - interval '90 days'
    AND p.duration_seconds IS NOT NULL
),
paare AS (
  SELECT channel_id, title_id
  FROM post_titel
  GROUP BY channel_id, title_id
  HAVING count(*) FILTER (WHERE duration_seconds >= 90) > 0
     AND count(*) FILTER (WHERE duration_seconds < 60) > 0
)
SELECT c.name AS kanal,
       t.title_original AS titel,
       round(max(pt.duration_seconds) FILTER (WHERE pt.duration_seconds >= 90)) AS langform_s,
       round(min(pt.duration_seconds) FILTER (WHERE pt.duration_seconds < 60))  AS kurzform_s,
       count(*) AS posts_im_paar
FROM paare
JOIN post_titel pt ON pt.channel_id = paare.channel_id AND pt.title_id = paare.title_id
JOIN creative_radar.channel c ON c.id = paare.channel_id
JOIN creative_radar.title t   ON t.id = paare.title_id
WHERE c.platform = 'youtube'
GROUP BY 1, 2
ORDER BY 1, 2;
```

Randnotiz zur Ehrlichkeit von P1: ein Post kann über mehrere Assets auf mehrere Titel
zeigen; die Zählung kann dadurch leicht überzeichnen. Für die Frage „reicht es für
zwanzig Paare?" ist das egal, für eine Veröffentlichung wäre es zu bereinigen.

### 2.2 Die Shot-Liste erzeugt ein Mensch — im eingebetteten Player, tippend

Das Abspielen von YouTube-Videos über den **IFrame-Player ist der von YouTube
vorgesehene, nutzungsbedingungskonforme Weg** — im Gegensatz zum Download. Ein kleines
lokales Annotations-Werkzeug (eine HTML-Seite, kein Deploy nötig):

- Video läuft im eingebetteten Player, auf **halber Geschwindigkeit** (die Player-API
  erlaubt 0,25×–2×). Halbe Geschwindigkeit verdoppelt die zeitliche Auflösung des
  Tippens; die Zeitstempel werden zurückskaliert.
- **Leertaste bei jedem Schnitt.** Die Tipp-Zeitpunkte sind die Shot-Grenzen — streng
  aufsteigend, per Konstruktion `ShotListError`-frei.
- Eigene Tasten für Einzelereignisse: **Musikeinsatz, Titelkarte, erstes gesprochenes
  Wort.** Hier ist der Mensch dem Algorithmus sogar überlegen: das Fundament musste sein
  Lautheitsmerkmal bewusst `loudness_rise_position` nennen, weil eine Kurve Musik nicht
  von Dialog unterscheiden kann. **Ein Ohr kann das.**
- Eine Checkbox je Kurzform: *„erkennbar ein Cutdown des Langform-Materials (gleiche
  Szenen)"* — siehe 2.4.

### 2.3 Warum das methodisch trägt und nicht bloß improvisiert ist

Drei Eigenschaften machen menschliches Tippen belastbarer, als es klingt:

1. **Konstante Reaktionslatenz verfälscht nichts.** Wer immer ~200 ms zu spät tippt,
   verschiebt *alle* Grenzen um 200 ms — die **Längen** der Einstellungen, und nur aus
   denen rechnen sich alle Merkmale, bleiben unverändert. Nur die *Streuung* der Latenz
   rauscht, und Rauschen mittelt sich über 40–60 Schnitte je Video heraus.
2. **Verpasste Schnitte wirken konservativ.** Sehr schnelle Passagen (ASL < 0,5 s)
   werden untertippt — und zwar stärker auf der schneller geschnittenen Seite. Das
   *staucht* gemessene Unterschiede, statt sie aufzublähen. Ein Effekt, der die
   Annotation überlebt, ist eher unter- als überschätzt.
3. **Die Fehlergrenzen sind messbar, pro Person.** Vor der echten Annotation laufen
   synthetische Kalibrier-Clips mit *bekannter* Schnittliste (Weg 2). Daraus kommen
   Latenz, Jitter und die Erkennungsgrenze der annotierenden Person — keine Annahmen,
   Messwerte.

Und der gepaarte Test tut sein Übriges: annotiert dieselbe Person beide Seiten eines
Paars, kürzen sich ihre systematischen Eigenheiten in der Differenz.

### 2.4 Die Grenze gegenüber Trailerhaus-Material — offen benannt

Wilde Paare garantieren gleichen Titel und Kanal, aber **nicht dasselbe
Ausgangsmaterial**: die Kurzform unter 60 Sekunden kann ein echter Cutdown sein — oder
ein eigenständig produziertes Kurzformat (Shorts-Logik, 9:16, eigene Szenen). Beides in
einen Topf zu werfen wäre der nächste Confound, diesmal selbstgebaut.

Deshalb die Checkbox in 2.2: beim Annotieren *sieht* man sofort, ob die Kurzform aus dem
Langform-Material geschnitten ist. Ausgewertet wird dann zweistufig — echte Cutdowns als
Kern-PoC, eigenständige Kurzformate als eigene, ebenfalls interessante Gruppe. Und genau
hier bleibt Plan A wertvoll: Trailerhaus-Paare sind *garantiert* gleiche Kampagne und
Originalfassung. **Plan B liefert die erste Antwort, Plan A die saubere Bestätigung.**

### 2.5 Aufwand

Zwanzig Paare ≈ 20 × (120 s + 30 s) = 50 Minuten Material. Bei halber Geschwindigkeit
und einem Übungsdurchgang: **ein Nachmittag, etwa 3–4 Stunden** Annotationszeit. Plus
der Werkzeugbau (Annotator + Kalibrier-Harness): ein bis zwei Arbeitstage, einmalig.

## 3. Weg 2 — das Kalibrier-Harness *(nötig für jeden Weg, also zuerst)*

Mit ffmpeg lassen sich Clips mit **exakt bekannter Schnittliste** erzeugen — Farbige
Flächen, Testmuster, variierende Schnittfrequenz. Kostenlos, offline, ohne jede
Rechtsfrage. Daran wird gemessen:

- **PySceneDetect**: Präzision/Trefferquote der Schnitterkennung, ASL-Fehler, die beste
  Schwellen-Einstellung — *bevor* echtes Material da ist, nicht danach.
- **Der Mensch am Annotator**: Latenz, Jitter, Erkennungsgrenze (ab welcher ASL wird
  untertippt?), auf denselben Clips.

Als Integrationstest mit echten, frei nutzbaren Dateien: die **Open Movies der Blender
Foundation** (CC-lizenziert, samt Trailern) und **Public-Domain-Trailer auf
archive.org** (mehrere Sammlungen, ausdrücklich als Public Domain markiert, direkt
herunterladbar). Historische Trailer beantworten die moderne Frage nicht — aber sie
prüfen die Werkzeugkette an echtem Bewegtbild, legal.

Dieses Harness ist keine Plan-B-Sonderlocke: auch Plan A braucht es, sonst laufen die
Trailerhaus-Dateien durch einen unkalibrierten Detektor.

## 4. Weg 3 — Instagram als Kontrollplattform *(Entscheidungsbedarf)*

Die Vorstufe (Abschnitt 8.2) hat es festgehalten: Apify liefert für Instagram
`videoUrl` (394/394 Langform-Posts) und für TikTok `mediaUrls` — **zeitlich befristete
CDN-Links, die zur Cron-Zeit frisch sind.** Technisch möglich wäre eine Cron-Stufe, die
zur Ingestion-Zeit flüchtig analysiert: Shot-Detection im Speicher, **nur die Merkmale
in `video_feature` gespeichert, keine Videodatei behalten.**

Wissenschaftlich wäre das eine echte Kontrolle: Der Stufe-3-Befund sitzt auf YouTube.
Wenn sich das Handwerk (Rhythmus, Aufbau) zwischen Langform und Kurzform auf Instagram
*genauso* unterscheidet, dort aber kein Langform-Vorteil existiert, dann erklärt das
Handwerk allein den Vorteil nicht. Unterscheidet es sich *nicht*, schneiden die
Plattformen unterschiedlich — auch ein Befund. Beides schärft die YouTube-Frage, ohne
YouTube anzufassen.

**Aber:** Auch Instagram und TikTok untersagen in ihren Nutzungsbedingungen das
automatisierte Abrufen von Inhalten. Dass die URLs ohnehin im bereits gespeicherten
`raw_payload` liegen und nichts dauerhaft gespeichert würde, ändert die Grundsatzfrage
nicht. **Das ist dieselbe Kategorie Entscheidung wie in der Vorstufe, Abschnitt 5 — sie
gehört zu Wolf, nicht in den Code.** Hier wird nichts davon gebaut, solange sie offen
ist. Der Unterschied zu Plan A: diese Entscheidung hängt nicht am Trailerhaus.

## 5. Weg 4 — Forschungsdatensätze *(Referenz, nicht Antwort)*

Kurz geprüft am 07.08.2026:

- **Trailers12k**: 12.000 Kinotrailer, manuell verifizierte YouTube-Zuordnungen,
  Shot-Segmentierung im Erhebungsverfahren, vorberechnete Repräsentationen auf Zenodo.
  **Lizenz CC BY-NC-SA — non-commercial.** Creative Radar ist ein kommerzielles
  Produkt; als Baustein *im Produkt* scheidet der Datensatz damit aus. Als
  Referenzverteilung für interne Methodenvalidierung („liegen unsere `rhythm_ratio`-Werte
  im plausiblen Bereich professioneller Trailer?") wäre er diskutabel — ob die
  Shot-Zeitstempel selbst im Release liegen oder nur die daraus gerechneten Embeddings,
  ließ sich nicht abschließend klären und wäre vor jeder Nutzung zu prüfen.
- **MovieNet**: 1.100 Filme mit Trailern und 42k Szenengrenzen, akademische Lizenzlage,
  Fokus auf Filmen statt Trailer-Paaren.

Nüchternes Fazit: Kein gefundener Datensatz enthält Langform/Cutdown-*Paare*, und die
Lizenzen passen nicht zum kommerziellen Einsatz. Das ist ein Seitenpfad für
Methodenvergleiche, kein Weg zum Befund.

## 6. Weg 5 — Presse- und EPK-Portale *(parallel anstoßen, nicht darauf bauen)*

Studios stellen Trailer für redaktionelle Nutzung über Pressebereiche und EPK-Portale
bereit; Zugang gegen Akkreditierung. Rechtlich der sauberste Fremdmaterial-Weg — aber
er tauscht das Warten auf das Trailerhaus gegen das Warten auf Akkreditierungen. Eine
E-Mail wert, als Plan ungeeignet. Falls die Akkreditierung irgendwann da ist, trifft sie
auf eine dann fertige, kalibrierte Werkzeugkette.

## 7. Was ausdrücklich *nicht* vorgeschlagen wird

- **YouTube-Download** (yt-dlp und Verwandte): verstößt gegen die Nutzungsbedingungen.
  Daran ändert auch Ungeduld nichts. Bleibt draußen.
- **Storyboard-/Vorschaubild-Scraping** (die Seek-Vorschau des Players): rechtlich
  dieselbe Grauzone, und mit Bildabständen von mehreren Sekunden zu grob für
  Trailer-ASL von unter zwei Sekunden. Doppelt untauglich.

## 8. Empfehlung und Reihenfolge

| # | Was | Wer | Aufwand | Rechtsfrage? |
|---|---|---|---:|---|
| 1 | P1/P2 laufen lassen: Wie viele wilde Paare haben wir? | Wolf | 5 min | nein |
| 2 | Kalibrier-Harness (ffmpeg-Clips, PySceneDetect, freies Testmaterial) | Code | ~1 Tag | nein |
| 3 | Tap-Along-Annotator (IFrame-Player, lokal) | Code | ~1 Tag | nein |
| 4 | 20 wilde YouTube-Paare annotieren, `compare_pairs` rechnen | Wolf + Code | 1 Nachmittag | nein |
| 5 | Instagram-Kontrollplattform | — | — | **ja — Wolfs Entscheidung** |
| 6 | EPK-Akkreditierung anstoßen | Wolf | 1 E-Mail | nein |

Die Schritte 1–4 führen **ohne jede offene Rechtsfrage und ohne Trailerhaus** zu einer
ersten echten Antwort auf die Stufe-3-Frage. Ein technisches Detail fällt dabei an:
menschlich annotierte Ereignisse wie der Musikeinsatz brauchen im Fundament ein eigenes
Merkmal (z. B. `music_entry_position` in `SCALE_FREE_FEATURES`) — bewusst getrennt von
`loudness_rise_position`, die Trennung von Messung und Interpretation bleibt.

Und wenn das Trailerhaus-Material dann kommt, ist es nicht entwertet, sondern
aufgewertet: es trifft auf eine kalibrierte Werkzeugkette und einen bestehenden Befund,
den es mit garantiert identischem Ausgangsmaterial bestätigen — oder korrigieren — kann.

## 9. Messung vom 07.08.2026 — P1 bestätigt den Weg, die Werkzeuge sind gebaut

### 9.1 Die wilden Paare existieren, und zwar reichlich

| Plattform | Paare | Langform-Posts | Kurzform-Posts |
|---|---:|---:|---:|
| Instagram | 81 | 102 | 318 |
| **YouTube** | **47** | 80 | 152 |
| TikTok | 45 | 65 | 303 |

**47 YouTube-Paare — mehr als das Doppelte der zwanzig, die der PoC braucht.** Selbst
wenn die Überzählungs-Randnotiz aus Abschnitt 2.1 und die Sichtprüfung (Featurettes,
eigenständige Kurzformate) die Liste deutlich ausdünnen, bleibt Luft. Der Weg trägt.

Randbeobachtung: Instagram hat mit 81 Paaren die größte Auswahl — relevant, falls die
Kontrollplattform-Entscheidung aus Abschnitt 4 je positiv ausfällt. Und TikToks
Verhältnis (303 Kurzform-Posts auf 45 Paare) zeigt die Shorts-Logik in Reinform.

### 9.2 Query P3 — die Arbeitsliste mit URLs

P2 aggregiert und hat keine URLs; zum Annotieren braucht es sie. P3 wählt je Paar die
**kürzeste Langform** (am ehesten der eigentliche Trailer statt eines Featurettes) und
die **längste Kurzform** (der substanziellste Cutdown):

```sql
WITH post_titel AS (
  SELECT DISTINCT p.id, p.channel_id, p.duration_seconds, p.post_url, a.title_id
  FROM creative_radar.post p
  JOIN creative_radar.asset a ON a.post_id = p.id
  WHERE a.title_id IS NOT NULL
    AND p.detected_at > now() - interval '90 days'
    AND p.duration_seconds IS NOT NULL
),
paare AS (
  SELECT pt.channel_id, pt.title_id
  FROM post_titel pt
  JOIN creative_radar.channel c ON c.id = pt.channel_id
  WHERE c.platform = 'youtube'
  GROUP BY 1, 2
  HAVING count(*) FILTER (WHERE pt.duration_seconds >= 90) > 0
     AND count(*) FILTER (WHERE pt.duration_seconds < 60) > 0
),
lang AS (
  SELECT DISTINCT ON (channel_id, title_id)
         channel_id, title_id, post_url, duration_seconds
  FROM post_titel WHERE duration_seconds >= 90
  ORDER BY channel_id, title_id, duration_seconds ASC
),
kurz AS (
  SELECT DISTINCT ON (channel_id, title_id)
         channel_id, title_id, post_url, duration_seconds
  FROM post_titel WHERE duration_seconds < 60
  ORDER BY channel_id, title_id, duration_seconds DESC
)
SELECT c.name AS kanal,
       t.title_original AS titel,
       round(l.duration_seconds) AS lang_s,
       l.post_url                AS langform_url,
       round(k.duration_seconds) AS kurz_s,
       k.post_url                AS kurzform_url
FROM paare
JOIN lang l ON l.channel_id = paare.channel_id AND l.title_id = paare.title_id
JOIN kurz k ON k.channel_id = paare.channel_id AND k.title_id = paare.title_id
JOIN creative_radar.channel c ON c.id = paare.channel_id
JOIN creative_radar.title t   ON t.id = paare.title_id
ORDER BY 1, 2;
```

### 9.3 Die Werkzeuge aus Abschnitt 8, Zeilen 2–3, existieren jetzt

| Werkzeug | Wo | Zweck |
|---|---|---|
| `tools/tap_annotator.html` | Browser, lokal | Tippen im IFrame-Player oder auf lokalen Dateien |
| `backend/scripts/make_calibration_clips.py` | ffmpeg, lokal | Kalibrier-Clips mit bekannter Schnittliste |
| `backend/scripts/evaluate_annotations.py` | Python, lokal | Kalibrier-Bericht und Paar-Vergleich |
| `music_entry_position` | `video_features.py` | das angekündigte, nur menschlich setzbare Merkmal |

Die drei methodischen Behauptungen aus Abschnitt 2.3 sind dabei zu Tests geworden
(`test_annotation_eval.py`): konstante Latenz lässt die Einstellungslängen nachweislich
unverändert, verpasste Schnitte ziehen die getippte ASL nachweislich nach oben
(konservative Richtung), und Latenz-Mittel und -Streuung sind getrennte Messwerte.

### 9.4 Ablauf für die Annotation — Schritt für Schritt

1. **P3 laufen lassen**, Ergebnis als Liste sichern. Zwanzig Paare daraus reichen;
   bei 47 darf großzügig aussortiert werden.
2. **Einmalig kalibrieren** (braucht ffmpeg, `brew install ffmpeg`):
   ```
   cd backend
   python scripts/make_calibration_clips.py --out kalibrierung --clips 3
   cd .. && python3 -m http.server   # dann http://localhost:8000/tools/tap_annotator.html
   ```
   Im Annotator: Modus „Lokale Datei", Clip laden, Tempo 0,5×, bei jedem Farbwechsel
   Leertaste. Exportieren, dann:
   ```
   cd backend && python scripts/evaluate_annotations.py calibrate \
       --annotation ../IHRE_DATEI.json --truth kalibrierung/clip_01.wahrheit.json
   ```
   Sehenswert sind Trefferquote (sollte nahe 100 % liegen) und Latenz-Streuung.
3. **Paare annotieren**: Modus „YouTube", URL aus der P3-Liste einsetzen. Je Video:
   Kürzel, `pair_key` (gleicher Wert für beide Seiten eines Paars!), Klasse wählen,
   die beiden Checkboxen ehrlich setzen. Leertaste je Schnitt, `M` beim Musikeinsatz,
   `T` je Titelkarte, `W` beim ersten gesprochenen Wort. Export in einen
   gemeinsamen Ordner.
4. **Auswerten**:
   ```
   cd backend && python scripts/evaluate_annotations.py pairs --dir ../annotationen/
   ```
   Heraus kommt der Wilcoxon-Vergleich über alle skalenfreien Merkmale, getrennt nach
   echten Cutdowns und eigenständigen Kurzformaten.

## 10. P3-Auswertung vom 07.08.2026 — aus 47 werden 20

Die vollständige Liste (47 Zeilen, 10 Seiten) liegt vor. Sie bestätigt, dass der Weg
trägt — aber der Puffer, den Abschnitt 9.1 noch sah, ist beim Hinsehen aufgebraucht.
**Die 47 sind nicht 47 verwertbare Paare.** Drei Filter greifen nacheinander.

### 10.1 Filter 1: Was P3 „Langform" nennt, ist oft kein Trailer (−14)

P3 wählt je Paar die **kürzeste** Langform ab 90 Sekunden. Genau deshalb ist dieser
Befund hart: Steht dort 358 s, dann gibt es im Fenster **keinen** Post zwischen 90 und
358 Sekunden. Der Kanal hat zu diesem Titel schlicht keinen Trailer in Trailerlänge
gepostet.

Vierzehn Zeilen liegen über 180 Sekunden, die Spitzenreiter deutlich:

| Kanal | Titel | „Langform" | was es vermutlich ist |
|---|---|---:|---|
| Pixar | Anniversary | **36.011 s** | 10 Stunden — Livestream oder Ambience-Loop |
| DC | Green Lantern | 1.445 s | 24 Minuten — Featurette oder Zusammenschnitt |
| Pixar | Toy Story 5 | 908 s | 15 Minuten |
| Disney UK | Lady and the Tramp | 701 s | Katalog-Clip |
| Disney UK | The Kitchen | 624 s | Katalog-Clip |
| Universal | Zodiac / Expendables / Bourne / American Gangster | 509–609 s | „Best Scenes"-Format |
| Disney | Maleficent | 358 s | Katalog-Clip |
| Disney UK | Toy Story | 277 s | Katalog-Clip |
| Prime Video | The Boys / Off Campus | 219–232 s | Grenzfall |
| Amazon MGM | The War | 210 s | Grenzfall |

Auffällig und stimmig: Es sind fast durchweg **Katalogtitel** (Zodiac 2007, American
Gangster 2007, Bourne 2004) — Throwback-Content, kein Kampagnenmaterial. Der
Laufzeit-Filter trennt hier nicht nur nach Länge, sondern faktisch nach Anlass.

**Die 180-Sekunden-Grenze ist eine Setzung, keine Naturkonstante.** Drei Zeilen liegen
knapp darüber (The War 210 s, Off Campus 219 s, The Boys 232 s) und könnten echte
Langtrailer sein. Sie sind in zwei Minuten per Augenschein zu klären und wären der
naheliegendste Weg, den Puffer zurückzugewinnen.

### 10.2 Filter 2: Titel-Platzhalter (−7)

Sechs Zeilen paaren auf einem Titel namens **„Unknown"** — bei Disney, Netflix
Deutschland, Paramount+ DE, Universal Pictures, Universal Pictures UK und Warner Bros.
Deutschland. Dazu kommt „Star Wars | Star Wars" (Franchise statt Film).

*Unknown* ist zwar ein realer Universal-Film von 2011 — die Universal-Zeile könnte also
echt sein. Dass aber Disney, Netflix und Paramount denselben Titel bespielen, ist
unplausibel. Wahrscheinlicher ist ein Zuordnungsartefakt: ein Titel, dessen Name ein
Allerweltswort ist, sammelt Posts ein, die nichts mit ihm zu tun haben. **Dann wären das
keine Paare, sondern zwei beliebige Videos desselben Kanals.**

Prüfabfrage (syntaxgeprüft) — teilen sich alle sechs *eine* `title_id`, ist es ein
Sammelbecken; sind es getrennte Zeilen, liegt der Fall anders:

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

Unabhängig vom Ausgang: für den PoC bleiben diese sieben Zeilen draußen. Ein Paar, dessen
Zusammengehörigkeit selbst der Verdachtsfall ist, gehört nicht in den ersten
Machbarkeitsnachweis.

### 10.3 Filter 3: Derselbe Film auf mehreren Kanälen (−6)

Der Wilcoxon-Test setzt **unabhängige Paare** voraus. Vier Filme treten mehrfach auf:

| Film | Kanäle |
|---|---|
| Masters of the Universe | Amazon MGM, Sony Pictures Deutschland, Sony Pictures Releasing UK |
| Supergirl | DC, Warner Bros. Deutschland, Warner Bros UK |
| PAW Patrol: The Dino Movie | Paramount Pictures Germany, Paramount Pictures UK |
| Disclosure Day | Universal Pictures, Universal Pictures UK |

Ein deutscher und ein britischer Trailer desselben Films sind meist **derselbe Schnitt
mit anderer Sprachfassung** — die Schnittrhythmen wären fast identisch. Sie als drei
Beobachtungen zu zählen würde die Stichprobe künstlich aufblähen und den z-Wert
schönrechnen. Genau die Sorte Fehler, die dieses Projekt schon dreimal hatte.

### 10.4 Das Ergebnis: exakt 20

| Schritt | Paare |
|---|---:|
| P3-Rohliste | 47 |
| − Langform über 180 s (kein Trailer) | −14 |
| − Titel-Platzhalter „Unknown"/„Star Wars" | −7 |
| − regionale Doppelungen desselben Films | −6 |
| **verwertbar, unabhängig** | **20** |

**Genau die zwanzig aus der Empfehlung — ohne einen einzigen Ersatz.** Die Simulation aus
Abschnitt 3 sagt dafür 84 % Trefferwahrscheinlichkeit bei schwachem Effekt. Das trägt,
aber es verträgt keinen Ausfall: Erweist sich beim Annotieren ein Paar als Featurette
oder als eigenständiges Kurzformat, sinkt die Zahl unter die Schwelle.

Zwei kleinere Einschränkungen der zwanzig, offen benannt:

- **Drei Kurzformen liegen bei 15–16 Sekunden.** Bei typischer Trailer-Schnittfrequenz
  sind das rund zehn Einstellungen — knapp an `MIN_SHOTS_FOR_THIRDS = 9`. Für diese Paare
  können die Drittel- und Rhythmus-Merkmale leer bleiben. Sie zählen weiterhin für
  `asl_seconds`, `shot_length_cv` und die Merkmale zur längsten Einstellung; nur bei
  `rhythm_ratio` sinkt das effektive *n* auf etwa 17.
- **Titel wie „Personality", „Driven", „At Home", „The Greatest"** sind ebenfalls
  Allerweltswörter. Sie sind nicht so auffällig wie „Unknown", verdienen beim Annotieren
  aber einen prüfenden Blick: Passt das Video zum Titel?

### 10.5 Woher der Puffer kommt

Drei Wege, in dieser Reihenfolge:

1. **Montag, 10.08.** läuft die Post-Analyse erstmals mit dem 2500er-Cap. `P3`/`P4`
   filtern auf `duration_seconds IS NOT NULL` — mit steigender Abdeckung wachsen auch die
   Paare. Die zwanzig sind ein Boden, keine Decke. **P4 danach neu laufen lassen.**
2. **Die drei Grenzfälle** aus 10.1 (210–232 s) per Augenschein klären: echte Langtrailer
   → zurück in den Topf, macht 23.
3. **Die regionalen Doppelungen** sind nicht wertlos. Sie eignen sich als
   *Kontrollmessung*: Zwei Fassungen desselben Trailers müssen nahezu identische
   Schnittmerkmale liefern. Tun sie das nicht, stimmt etwas mit der Annotation nicht —
   eine kostenlose Qualitätskontrolle, die kein anderes Material bietet.

### 10.6 Query P4 — die bereinigte Arbeitsliste

Setzt alle drei Filter um und liefert direkt den `pair_key` für den Annotator. Gegen das
Schema syntaxgeprüft:

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
-- Ein Film, ein Paar. Bevorzugt der Kanal mit der laengsten Kurzform:
-- mehr Einstellungen zum Tippen, hoehere Chance auf Rhythmus-Merkmale.
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

Die Spalte `pair_key` ist so gebaut, dass sie direkt in den Annotator kopiert werden
kann — beide Seiten eines Paars brauchen denselben Wert.

### 10.7 Bewertung

Der Plan-B-Weg steht, aber ohne Reserve. **Die ehrliche Zahl ist 20, nicht 47** — und sie
liegt exakt auf der Schwelle, die die Simulation als tragfähig ausweist. Das ist kein
Grund umzuplanen, aber ein Grund, vor dem Annotieren P4 statt P3 zu benutzen und Montags
Abdeckungssprung mitzunehmen.

Bemerkenswert bleibt, was der Filter nebenbei gezeigt hat: **Rund ein Drittel der
vermeintlichen „Langform-Posts" auf YouTube sind gar keine Trailer**, sondern
Katalog-Clips, Featurettes und in einem Fall ein zehnstündiger Stream. Das betrifft nicht
nur Stufe 5 — es ist ein Hinweis darauf, dass die Formatklasse `langform` aus Stufe 2 auf
YouTube weniger homogen ist als angenommen. Für den Stufe-3-Befund selbst ist das keine
Entwarnung und keine Widerlegung; es ist eine offene Frage, die eine eigene Prüfung
verdient und hier nur festgehalten wird.

## 11. Kalibrierung vom 10.08.2026 — die Annahmen sind belegt

Zwei Kalibrier-Clips getippt (Wolf, 0,5× Abspielgeschwindigkeit, 34 Schnitte insgesamt).
Damit ist Abschnitt 2.3 keine Argumentation mehr, sondern eine Messung.

| | Zielwert | Clip 1 (ASL 1,89 s) | Clip 2 (ASL 1,56 s, beschleunigend) |
|---|---|---:|---:|
| Trefferquote | > 95 % | **100 %** | **100 %** |
| Präzision | > 95 % | **100 %** | **100 %** |
| Latenz, Mittel | (gleichgültig) | +0,241 s | +0,220 s |
| Latenz, Streuung | < 0,10 s | **0,038 s** | **0,052 s** |
| ASL-Verhältnis | 0,95–1,10 | **1,000** | **1,000** |

Was daran zählt:

**Kein verpasster und kein überzähliger Schnitt** bei 34 Gelegenheiten. Der konservative
Effekt aus Abschnitt 2.3 (Untertippen staucht Unterschiede) tritt hier gar nicht erst
auf — es gibt nichts zu stauchen.

**Die Latenz ist konstant, nicht bloß klein.** +0,23 s im Mittel, aber mit einer Streuung
von 0,04–0,05 s. Das Mittel verschiebt alle Grenzen gleich und kürzt sich in der
Paardifferenz vollständig heraus; die Streuung ist der einzige Störfaktor, und sie liegt
bei einem Drittel bis der Hälfte des Grenzwerts. Auch im schwierigeren Clip 2
(beschleunigendes Profil, kürzere Einstellungen) bleibt sie stabil.

**Das ASL-Verhältnis ist exakt 1,000** in beiden Clips — die gemessene mittlere
Einstellungslänge trifft die wahre auf drei Nachkommastellen.

Ein dritter Clip wurde nicht mehr gebraucht: Zwei Läufe mit vollständiger Trefferquote
und stabiler Latenz ändern die Entscheidung nicht mehr. Und 0,5× reicht — auf 0,25×
herunterzugehen würde die Sitzung verlängern, ohne die Messung zu verbessern.

### 11.1 Zwei Bedienfehler, die dabei aufgefallen sind — beide behoben

**Der Fokus blieb im Textfeld.** Wer nach dem Ausfüllen von `pair_key` direkt die
Leertaste drückt, tippt ein Leerzeichen ins Feld statt einen Schnitt zu setzen — der
Tastatur-Handler ignoriert Eingabefelder absichtlich. Der Start-Knopf gibt den Fokus
jetzt selbst frei.

**Ein Videowechsel behielt die alten Taps.** Beim zweiten Clip zählte der Annotator
stillschweigend weiter, beide Clips landeten in einer Liste. Jetzt fragt er beim Wechsel
nach, wenn ungesicherte Schnitte vorliegen — und setzt nach Bestätigung zurück. Bewusst
mit Rückfrage statt stumm: stilles Zurücksetzen hätte den umgekehrten Fehler erzeugt,
den Verlust noch nicht exportierter Arbeit. Zusätzlich steht der Name des geladenen
Videos jetzt über der Tap-Liste, damit eine Verwechslung sofort sichtbar wäre.

Beide Korrekturen sind im Browser geprüft (Playwright, fünf Prüfungen: Fokusfreigabe,
Tap-Erfassung, Abbruch erhält die Taps, Bestätigung setzt zurück, Anzeige folgt dem
geladenen Video).

**Die Rohdaten der Kalibrierung sind nicht verloren gegangen** — die zusammengelaufene
Tap-Liste war eindeutig trennbar, weil die Zeitstempel beim zweiten Clip wieder von vorn
beginnen. Ausgewertet wurde die erste Aufnahme, nicht eine Wiederholung; das ist auch
methodisch besser, weil ein zweiter Durchgang desselben Clips einen Lerneffekt hätte.
