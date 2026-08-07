# Trailer-Intelligence Stufe 5, Vorstufe — kommen wir an das Videomaterial?

Stand: 07.08.2026. Bestandsaufnahme vor dem Bau. Sie beantwortet eine einzige Frage:
**Ist die Video-Erfassung überhaupt machbar, und zu welchem Preis?** Ohne diese Antwort
ist die Diskussion über Hook-Typen und Schnittfrequenz müßig.

## 1. Warum die Frage jetzt gestellt wird

[Stufe 3](./TRAILER_INTELLIGENCE_STUFE3_LANGFORM.md) hat den Suchraum festgelegt: *Was
unterscheidet ein YouTube-Langformat ab 90 Sekunden handwerklich von einem Cutdown
unter 60 — bei gleichem Kanal, gleichem Titel und gleicher Produktionsroutine?*

Vier konkurrierende Erklärungen sind ausgeschlossen. Übrig bleibt das Handwerk, und das
steckt im Bewegtbild. Creative Radar erhebt bisher **nur Thumbnails**.

## 2. Was an Kennungen bereits in der Datenbank liegt

Aus dem Code der Connectoren gelesen, nicht geraten:

| Plattform | `external_id` | `post_url` | dauerhaft? |
|---|---|---|---|
| YouTube | Video-ID (`normalize_youtube_video`) | `watch?v=…` | **ja** |
| TikTok | Item-ID | `webVideoUrl` | ja |
| Instagram | **wird nicht gesetzt** | `instagram.com/p/<shortCode>` | ja, über den Shortcode |

**Das Wichtigste daran: Videodateien liegen nirgends, aber eine dauerhafte Kennung
liegt für jeden Post vor.** Die Erfassung muss also nichts nachholen, was verpasst wurde
— sie kann jederzeit ansetzen.

Der Instagram-Fall ist eine Lücke, aber keine blockierende: `normalize_public_item`
setzt kein `external_id`, der Shortcode steckt aber in der `post_url`. Wer ihn braucht,
parst ihn heraus oder ergänzt den Normalizer.

`raw_payload` speichert den vollständigen Apify-Datensatz (`raw_payload = dict(item)`,
ungekürzt). Dort stehen bei TikTok und Instagram auch direkte Medien-URLs — die sind
allerdings **zeitlich befristete CDN-Links** und nach Wochen tot. Als Quelle taugen sie
nicht, als Beleg dafür, was Apify liefert, schon.

## 3. Die entscheidende Zahl: die Kohorte ist klein

Der Befund lebt auf YouTube. Was tatsächlich verarbeitet werden müsste:

| | Posts im 90-Tage-Fenster |
|---|---:|
| YouTube Langform (≥ 90 s) | **288** |
| YouTube Kurzform (< 60 s), als Kontrolle | **545** |
| **Summe** | **833** |

Nicht 7.000 Posts, sondern rund 830 Videos. Das verändert die Machbarkeit
grundlegend — es ist eine Größenordnung, die auf einer einzelnen Maschine an einem
Wochenende durchläuft.

## 4. Die erste Kennzahl kostet nichts

Ein Teil der Frage lässt sich **ohne jedes Modell** beantworten:

| Merkmal | wie | Kosten |
|---|---|---|
| Schnittfrequenz, mittlere Einstellungslänge | Shot-Detection auf der Datei | nur Rechenzeit |
| Verlauf der Einstellungslängen über die Laufzeit | dieselbe Shot-Liste | nur Rechenzeit |
| Position und Länge der längsten Einstellung | dieselbe Shot-Liste | nur Rechenzeit |
| Lautheitsverlauf, Musikeinsatz-Zeitpunkt | Audiospur | nur Rechenzeit |
| Hook-Typ, Bildinhalt, Titelkarten | Vision-Modell auf Keyframes | ~$0,0073 je Bild |

**Der Schnitt-Rhythmus ist genau das Merkmal, das einen Zwei-Minuten-Trailer strukturell
von einem 30-Sekunden-Cutdown trennt** — und er ist gratis zu haben. Die teure
semantische Ebene wäre erst der zweite Schritt.

Grobe Rechnung für die semantische Ebene: 833 Videos × 12 Keyframes ≈ 10.000 Bilder ×
$0,0073 ≈ **$73** einmalig. Das ist nicht der Engpass.

## 5. Der eigentliche Engpass ist nicht technisch

**Das Herunterladen von YouTube-Videos verstößt gegen die Nutzungsbedingungen der
Plattform.** Das ist keine Randnotiz und keine Frage, die ich still im Code entscheide.
Die YouTube Data API liefert Metadaten und Thumbnails, aber keinen Videostream.

Drei gangbare Wege, in der Reihenfolge, in der ich sie empfehlen würde:

**a) Eigenes Material des Trailerhauses.** Die saubere Variante für einen
Machbarkeitsnachweis: das Haus besitzt seine eigenen Trailer und Cutdowns, oft in beiden
Längen zum selben Titel. Das ist die ideale Paarung — gleicher Film, gleiches Team,
gleiche Kampagne, nur andere Länge. Damit lässt sich die Frage sauberer beantworten als
mit fremdem Material, und rechtlich ist nichts zu klären.

**b) Lizenzierter Zugang.** Für den Dauerbetrieb über fremde Kanäle bräuchte es eine
vertragliche Grundlage. Aufwand und Kosten unklar, vermutlich unverhältnismäßig für den
aktuellen Zweck.

**c) Verzicht auf das Bewegtbild.** Aus Thumbnails, Titeln und Beschreibungen lässt sich
die Handwerksfrage nicht beantworten. Dann bliebe der Befund aus Stufe 3 stehen, ohne
Erklärung — auch ein vertretbares Ergebnis, wenn a) und b) nicht gehen.

**Empfehlung: a).** Der PoC braucht keine 833 Videos. Zwanzig Trailer-plus-Cutdown-Paare
aus dem eigenen Bestand reichen, um zu sehen, ob sich Schnittfrequenz und Aufbau
überhaupt messbar unterscheiden. Erst wenn das trägt, stellt sich die Frage nach Skalierung
und Rechtsgrundlage.

## 6. Was noch zu messen ist

Zwei Queries, gegen ein lokales Postgres 16 geprüft. Sie schließen die Lücken, die sich
aus dem Code allein nicht schließen lassen.

### 6.1 Welche Medien-Schlüssel liegen tatsächlich im `raw_payload`?

Zeigt, was die Connectoren wirklich mitgeliefert haben — im Code steht nur, dass alles
gespeichert wird, nicht was drin ist.

```sql
SELECT c.platform, k.key AS schluessel, count(*) AS posts
FROM creative_radar.post p
JOIN creative_radar.channel c ON c.id = p.channel_id
CROSS JOIN LATERAL jsonb_object_keys(p.raw_payload::jsonb) AS k(key)
WHERE p.detected_at > now() - interval '90 days'
  AND p.duration_seconds >= 90
  AND jsonb_typeof(p.raw_payload::jsonb) = 'object'
  AND (k.key ILIKE '%video%' OR k.key ILIKE '%url%' OR k.key ILIKE '%media%'
       OR k.key ILIKE '%download%' OR k.key ILIKE '%play%' OR k.key ILIKE '%addr%')
GROUP BY 1, 2
ORDER BY 1, posts DESC, 2;
```

### 6.2 Kohortengrößen und Vollständigkeit der Kennungen

```sql
SELECT c.platform,
       CASE WHEN p.duration_seconds >= 90 THEN '1_langform'
            WHEN p.duration_seconds <  60 THEN '3_kurzform'
            WHEN p.duration_seconds IS NULL THEN '4_ohne_dauer'
            ELSE '2_uebergang' END AS klasse,
       count(*)                                                            AS posts,
       count(*) FILTER (WHERE p.external_id IS NOT NULL)                   AS mit_external_id,
       count(*) FILTER (WHERE p.post_url IS NOT NULL AND p.post_url <> '') AS mit_post_url,
       count(*) FILTER (WHERE jsonb_typeof(p.raw_payload::jsonb)='object') AS mit_raw_payload
FROM creative_radar.post p
JOIN creative_radar.channel c ON c.id = p.channel_id
WHERE p.detected_at > now() - interval '90 days'
GROUP BY 1, 2
ORDER BY 1, 2;
```

Erwartet: bei YouTube `mit_external_id` = `posts`, bei Instagram 0. Weicht YouTube davon
ab, gibt es Posts ohne Video-ID, und die Kohorte schrumpft entsprechend.

## 7. Bewertung

| Frage | Antwort |
|---|---|
| Liegen Videodateien vor? | Nein |
| Liegen dauerhafte Kennungen vor? | Ja, für alle drei Plattformen |
| Wie groß ist die relevante Kohorte? | ~833 YouTube-Videos, nicht 7.000 |
| Was kostet die Rechnung? | Schnitt-Analyse gratis, semantische Ebene ~$73 |
| Was ist der Engpass? | **Nicht Technik, sondern Rechtsgrundlage** |

**Technisch ist Stufe 5 machbar und günstiger als gedacht. Die offene Frage ist, woher
das Material rechtlich sauber kommt** — und die beantwortet kein Code, sondern eine
Entscheidung.

Solange sie offen ist, wäre es falsch, eine Download-Pipeline zu bauen. Vorbereitet
werden kann alles, was quellenunabhängig ist: das Datenmodell für Video-Merkmale, die
Shot-Detection-Auswertung und die Metrik, gegen die später verglichen wird.

## 8. Messung vom 07.08.2026 — beide Queries bestätigt, ein Zusatzfund

### 8.1 Kennungen sind lückenlos, wie erwartet

| Plattform | Klasse | Posts | `external_id` | `post_url` | `raw_payload` |
|---|---|---:|---:|---:|---:|
| YouTube | Langform | 288 | 288 | 288 | 288 |
| TikTok | Langform | 193 | 193 | 193 | 193 |
| Instagram | Langform | 394 | **0** | 394 | 394 |

Exakt das vorhergesagte Muster: YouTube und TikTok zu 100 % über `external_id`
identifizierbar, Instagram zu 0 % — aber zu 100 % über `post_url`. Die Lücke aus
Abschnitt 2 ist bestätigt, nicht überraschend, und weiterhin nicht blockierend.

Randnotiz: Instagram hat mit 394 Langform-Posts sogar mehr als YouTube (288) — für
Stufe 5 zunächst irrelevant, weil der Befund aus Stufe 3 auf YouTube sitzt, aber
relevant, falls Instagram später als zweite Plattform geprüft wird.

### 8.2 Zusatzfund: Instagram trägt eine direkte Video-URL

Die Medien-Schlüssel-Suche hat mehr gefunden, als der Code allein vermuten ließ.
Instagram liefert über Apify:

| Schlüssel | Posts (von 394 Langform) | Bedeutung |
|---|---:|---|
| `videoUrl` | 394 (100 %) | direkte Videodatei-URL |
| `videoViewCount` / `videoPlayCount` | 394 | Video-Statistiken |
| `audioUrl` | 394 | separate Audiospur |

TikTok liefert entsprechend `mediaUrls` (193/193) und `videoMeta` mit `downloadAddr`
darin.

**Das ändert Abschnitt 5 nicht**, weil der Befund weiterhin auf YouTube sitzt und
YouTube keinen Stream über die Data API liefert. Es ist aber gut zu wissen: Für die
zweite Plattform in einer möglichen Ausweitung (Instagram) läge das Material technisch
näher als angenommen. Ob diese URLs zum Abrufzeitpunkt noch gültig sind — Apify-typisch
sind CDN-Links befristet — ist damit noch nicht geklärt und wäre vor einer Nutzung zu
prüfen.

### 8.3 Bewertung bleibt unverändert

Die Empfehlung aus Abschnitt 5 (eigenes Material des Trailerhauses für den PoC) steht.
Dieser Fund ist eine Werkzeugkiste für später, keine Abkürzung für den YouTube-Befund
aus Stufe 3 — der bleibt an die Rechtsfrage aus Abschnitt 5 gebunden.

## 9. Weiter geht es im Fundament-Dokument

Der quellenunabhängige Teil aus Abschnitt 7 ist inzwischen gebaut: Merkmalsberechnung,
Kohortenvergleich, Datenmodell und Migration. Beschrieben in
**[TRAILER_INTELLIGENCE_STUFE5_FUNDAMENT.md](./TRAILER_INTELLIGENCE_STUFE5_FUNDAMENT.md)**.

Zwei Dinge daraus korrigieren bzw. schärfen dieses Dokument:

- Das Merkmal aus Abschnitt 4, hier noch „Musikeinsatz-Zeitpunkt" genannt, heißt jetzt
  `loudness_rise_position`. Aus einer Lautheitskurve allein lässt sich Musik nicht von
  Dialog unterscheiden — der alte Name versprach mehr, als die Messung hergibt.
- Die zwanzig Paare aus Abschnitt 5 sind nachgerechnet, nicht geschätzt: bei zwanzig
  gepaarten Titeln wird ein schwacher Effekt in 84 % der Fälle gefunden, bei dreißig
  **ungepaarten** Videos je Arm nur in 13 %. Die Paarung ist nicht ein Vorteil des
  Trailerhaus-Materials, sie ist der Grund, warum der PoC in dieser Größe funktioniert.
