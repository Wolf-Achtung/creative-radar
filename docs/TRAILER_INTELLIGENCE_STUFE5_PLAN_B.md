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
