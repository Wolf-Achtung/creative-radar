# Trailer-Intelligence Stufe 5 — das Fundament

Stand: 07.08.2026. Die [Vorstufe](./TRAILER_INTELLIGENCE_STUFE5_VIDEOQUELLEN.md) endet mit
einem Satz, der den Umfang dieses Schritts festlegt:

> Solange sie offen ist, wäre es falsch, eine Download-Pipeline zu bauen. Vorbereitet
> werden kann alles, was quellenunabhängig ist: das Datenmodell für Video-Merkmale, die
> Shot-Detection-Auswertung und die Metrik, gegen die später verglichen wird.

Genau das ist gebaut. **Kein Download, kein Netzwerkzugriff, kein Verweis auf eine
Quelle.** Der Engpass ist die Rechtsfrage, und die beantwortet kein Code.

## 1. Was jetzt existiert

| Datei | Was drin ist |
|---|---|
| `backend/app/services/video_features.py` | Merkmalsberechnung aus einer Shot-Liste, zwei Rangtests |
| `backend/app/tests/test_video_features.py` | 33 Tests |
| `backend/app/models/entities.py` → `VideoFeature` | Tabelle für die Ergebnisse |
| `backend/migrations/versions/a9c3e7f21b58_…py` | additive Migration, gegen echtes Postgres geprüft |

Die Eingabe ist eine **fertige Shot-Liste** — Paare aus Start- und Endzeit in Sekunden.
Wer sie erzeugt, ist dem Modul gleichgültig: PySceneDetect auf eigenem Material, ein
Export aus dem Schnittsystem, eine EDL. Damit ist alles sofort nutzbar, sobald die erste
Datei vorliegt, und bis dahin vollständig testbar — was es auch ist.

## 2. Die Falle, um die es hier eigentlich geht

Dieses Projekt hatte drei Confounds hintereinander: die Plattform-Mischung, die Posts
ohne Views, den verkleideten Plattform-Vergleich beim Dauer-Bucket. Jedes Mal war die
Ursache dieselbe — eine Kennzahl, die etwas anderes maß, als ihr Name behauptete.

Hier droht derselbe Fehler ein viertes Mal, und diesmal besonders verführerisch:

> **Fast jede naheliegende Schnitt-Kennzahl hängt an der Laufzeit.**

Ein Zwei-Minuten-Trailer hat mehr Einstellungen als ein 30-Sekunden-Cutdown. Das ist
keine Erkenntnis über Handwerk, sondern die Definition von *länger*. Wer Langform und
Kurzform auf `shot_count` vergleicht, misst die Dauer und nennt es Rhythmus.

Das Modul trennt deshalb hart:

**Skalenfrei — vergleichbar** (`SCALE_FREE_FEATURES`, 11 Merkmale):
`asl_seconds`, `median_shot_seconds`, `shot_length_cv`, `asl_first_third_ratio`,
`asl_middle_third_ratio`, `asl_last_third_ratio`, `rhythm_ratio`,
`longest_shot_position`, `longest_shot_ratio`, `loudness_rise_position`,
`loudness_peak_position`

**Laufzeitabhängig — beschreiben, nicht vergleichen** (`DURATION_DEPENDENT_FEATURES`):
`duration_seconds`, `shot_count`

Und der entscheidende Teil: `compare_cohorts` **weigert sich**, ein laufzeitabhängiges
Merkmal zu vergleichen. Kein Kommentar, kein Hinweis in der Doku — ein `ValueError`:

```
'shot_count' hängt an der Laufzeit und darf nicht zwischen Formatklassen
verglichen werden — der Unterschied wäre die Dauer selbst, nicht das Handwerk.
```

Ein Kommentar hätte den vierten Confound nicht verhindert. Ein Fehler schon. Das ist die
Lehre aus drei Korrekturrunden, in Code gegossen.

### 2.1 Der Nachweis, dass die Trennung trägt

Derselbe Rhythmus, einmal auf 9 Sekunden, einmal auf 18 Sekunden gestreckt — Faktor zwei
in der Laufzeit:

```
Laufzeit               9,0 s   vs  18,0 s
rhythm_ratio          0,5000      0,5000
shot_length_cv        0,3333      0,3333
longest_shot_position 0,0556      0,0556
asl_first_third_ratio 1,3333      1,3333
```

Identisch bis auf die letzte Stelle. Ein skalenfreies Merkmal darf sich bei reiner
Streckung nicht bewegen, und es tut es nicht. Der Test dazu heißt
`test_same_rhythm_at_double_length_gives_identical_scale_free_values` und ist der
wichtigste in der Datei.

### 2.2 Eine Ausnahme, mit Ansage

`asl_seconds` — die mittlere Einstellungslänge — ist eine Rate: Laufzeit geteilt durch
Anzahl. Rechnerisch skalenfrei, zwei Filme verschiedener Länge können dieselbe ASL haben.
Sie ist zugleich die kanonische Kennzahl der Schnittforschung und zählt deshalb als
vergleichbar.

Dass Cutdowns typischerweise schneller geschnitten sind, ist dabei **kein Artefakt,
sondern die Sache selbst** — eine Entscheidung von Cutterinnen und Cuttern, keine Folge
der Laufzeit.

## 3. Warum die Paarung so viel wert ist

Die Vorstufe empfiehlt für den Machbarkeitsnachweis eigenes Material des Trailerhauses:
derselbe Film in beiden Längen, gleiches Team, gleiche Kampagne. Diese Paarung hält
Titel, Genre, Budget und Handschrift konstant und lässt nur die Länge variieren.

Wie viel das ausmacht, war bisher eine Behauptung. Hier ist sie gemessen — 2.000
Simulationsläufe je Zelle, mit einer realistischen Struktur: Filme unterscheiden sich
stark voneinander (σ = 0,40), der Langform/Kurzform-Unterschied innerhalb eines Films ist
konsistent (Rauschen σ = 0,10).

**Anteil der Läufe, in denen ein tatsächlich vorhandener Unterschied gefunden wird:**

| Videos je Arm | Effekt 0,10 | Effekt 0,20 | Effekt 0,30 |
|---:|---:|---:|---:|
| **10** gepaart | 45 % | 95 % | 100 % |
| 10 ungepaart | 6 % | 17 % | 30 % |
| **20** gepaart | **84 %** | 100 % | 100 % |
| 20 ungepaart | 10 % | 31 % | 58 % |
| **30** gepaart | 95 % | 100 % | 100 % |
| 30 ungepaart | 13 % | 42 % | 75 % |

Das ist kein Faktor vier, das ist eine andere Größenordnung. **Zwanzig Paare finden einen
schwachen Effekt in 84 % der Fälle; dreißig ungepaarte Videos je Arm schaffen 13 %.**
Die zwanzig Trailer-plus-Cutdown-Paare aus der Empfehlung der Vorstufe sind also nicht
knapp bemessen, sondern der Grund, warum der PoC überhaupt funktionieren kann.

Umgekehrt gilt: Mit ungepaartem Material in dieser Größenordnung wäre ein Nicht-Befund
bedeutungslos — er hieße nur, dass die Stichprobe zu klein war.

Gegenprobe, damit die Zahlen oben nicht bloß Optimismus sind — Fehlalarmquote, wenn gar
kein Unterschied existiert (Soll ≈ 4,6 % bei z ≥ 2,0):

| | gepaart | ungepaart |
|---|---:|---:|
| n = 10 | 3,7 % | 4,4 % |
| n = 20 | 4,0 % | 4,5 % |
| n = 30 | 4,4 % | 4,6 % |

Beide Tests sind kalibriert, bei kleinen Stichproben leicht konservativ. Genau so soll
sich ein Rangtest mit Normalapproximation verhalten.

## 4. Die Tests

Rangbasiert, ohne Verteilungsannahme — bei zwanzig Werten wäre jede Annahme über die
Verteilung ohnehin nicht prüfbar. Wie in den Stufen 1–3 von Hand gerechnet, weil scipy
nicht verfügbar ist.

| | `compare_paired` | `compare_unpaired` |
|---|---|---|
| Verfahren | Wilcoxon-Vorzeichen-Rangtest | Mann-Whitney-U |
| Eingabe | `(langform, kurzform)` desselben Titels | zwei unabhängige Kohorten |
| Effektstärke | rang-biseriale Korrelation | Cliffs Delta |
| Mindestgröße | 10 Paare | 10 je Arm |
| Bindungen | Durchschnittsränge + Varianzkorrektur | dito |

Beide melden `insufficient` statt eines p-Werts, wenn die Stichprobe zu klein ist. Das
ist die dritte Antwortmöglichkeit neben *unterscheidet sich* und *unterscheidet sich
nicht* — dieselbe Lehre wie `localized_in` in Stufe 3: eine binäre Antwort auf eine
dreiwertige Frage ist eine falsche Antwort.

Bewusst ein drittes Verfahren, nach dem Ein-Stichproben-z (Stufe 1) und dem
Zwei-Anteils-z (Stufe 3): hier sind die Werte stetig und die Stichprobe klein, da ist ein
Rangtest das richtige Werkzeug.

## 5. Was gemessen wird

Aus der Shot-Liste, ohne jedes Modell, ohne Kosten:

| Merkmal | Bedeutung |
|---|---|
| `asl_seconds` | mittlere Einstellungslänge |
| `median_shot_seconds` | Median — robust gegen die eine lange Schlusseinstellung |
| `shot_length_cv` | Gleichmäßigkeit des Schnitts (Variationskoeffizient) |
| `asl_*_third_ratio` | Einstellungslänge je Drittel, relativ zur mittleren |
| `rhythm_ratio` | letztes Drittel geteilt durch erstes — die Beschleunigungskurve |
| `longest_shot_position` | wo die längste Einstellung liegt (0 = Anfang, 1 = Ende) |
| `longest_shot_ratio` | wie lang sie im Verhältnis zur mittleren ist |

Aus der Audiospur, falls vorhanden:

| Merkmal | Bedeutung |
|---|---|
| `loudness_rise_position` | wo die Lautheit erstmals die halbe Spannweite überschreitet |
| `loudness_peak_position` | wo der lauteste Punkt liegt |

### 5.1 Warum das Merkmal nicht „Musikeinsatz" heißt

Es hieß in der Vorstufe (Abschnitt 4) noch so. Das war zu großzügig: **aus einer
Lautheitskurve allein lässt sich Musik nicht von Dialog oder Effekt unterscheiden.** Das
Merkmal heißt jetzt, was es misst — der Punkt, an dem die Lautheit erstmals die Hälfte
ihrer Spannweite überschreitet. Wer daraus „hier setzt die Musik ein" liest,
interpretiert über die Messung hinaus.

Die Halb-Spannweiten-Schwelle ist gegenüber der absoluten Lautheit invariant: ein leise
gemasterter Trailer und ein lauter liefern denselben Wert, solange die Kurvenform gleich
ist. Ohne diese Eigenschaft wäre das Merkmal ein Maß für die Mastering-Entscheidung, nicht
für den Aufbau.

### 5.2 `None` heißt nie null

Merkmale, die nicht messbar waren, bleiben leer und tragen eine Begründung in `notes`:

- Weniger als 9 Einstellungen → keine Drittel-Aufteilung. Bei fünf Shots hätte ein Drittel
  ein oder zwei davon, und der „Rhythmus" wäre ein Einzelwert mit Etikett.
- Ein Drittel ohne eigene Einstellung → meist eine sehr lange Einstellung, die ein ganzes
  Drittel überspannt.
- Keine Audiospur übergeben → Lautheitsmerkmale leer.

Im Kohortenvergleich fällt ein fehlender Wert für *dieses Video und dieses Merkmal*
heraus, nicht für die ganze Kohorte.

### 5.3 Kaputte Shot-Listen werden nicht repariert

Überlappende, unsortierte oder null-lange Einstellungen lösen `ShotListError` aus. Das ist
bewusst hart: eine solche Liste deutet auf einen Fehler im erzeugenden Werkzeug hin. Sie
stillschweigend zurechtzubiegen würde den Fehler verschleiern und die Zahlen unbrauchbar
machen, ohne dass es jemand merkt.

## 6. Die Tabelle

`creative_radar.video_feature`, 23 Spalten, fünf Indizes. Beim Deploy leer und bleibt es,
bis Material vorliegt. Sie wird jetzt angelegt, weil die Merkmalsdefinition steht — das
Schema muss nicht mehr raten.

Zwei Entscheidungen, die Erklärung verdienen:

**`post_id` ist NULL-bar.** Der empfohlene PoC läuft auf eigenem Material des
Trailerhauses, das nie gescraped wurde und deshalb keine Post-Zeile hat. Ein
NOT-NULL-Fremdschlüssel hätte genau den Weg verbaut, den die Vorstufe empfiehlt. Gegen
echtes Postgres geprüft: eine Zeile ohne `post_id` wird angenommen, eine mit erfundener
`post_id` weist der Fremdschlüssel ab.

**`pair_key`** trägt den Titel-Schlüssel, der Langform und Kurzform desselben Films
zusammenbindet — die Spalte, an der Abschnitt 3 hängt. `tool` und `tool_version` halten
fest, womit die Shots erzeugt wurden; zwei Detektoren liefern nicht dieselbe Liste, und
das muss später nachvollziehbar sein.

### 6.1 Migration gegen echtes Postgres geprüft

Nicht nur gegen SQLite. Gegen ein lokales Postgres 16 mit `creative_radar`-Schema:

- `upgrade` legt Tabelle, fünf Indizes und den Fremdschlüssel auf `post.id` an
- Zeile ohne `post_id` → angenommen
- Zeile mit erfundener `post_id` → `violates foreign key constraint`
- `downgrade` entfernt alles rückstandsfrei, auch mit Daten in der Tabelle
- erneutes `upgrade` läuft durch, ein zweites Mal ebenfalls (`if_not_exists`)

## 7. Ein Fund am Rande: die Alembic-Kette läuft nicht von null durch

Beim Aufsetzen des Prüf-Postgres ist aufgefallen, dass `alembic upgrade head` auf einer
**frischen, leeren** Datenbank abbricht:

```
sqlalchemy.exc.ProgrammingError: (psycopg.errors.UndefinedObject)
type "public.market" does not exist
[SQL: ALTER TYPE public.market ADD VALUE IF NOT EXISTS 'UK']
```

**Das liegt nicht an dieser Änderung.** Gegengeprüft, indem die Kette nur bis zum
vorherigen Head (`b2e8f4a6c1d7`) laufen gelassen wurde — identischer Abbruch, an
derselben Stelle, ohne dass die neue Migration überhaupt beteiligt ist.

Nicht dringend: Produktion und Staging sind längst migriert und laufen weiter, die Kette
wird dort nie wieder von null gestartet. Es trifft nur den, der eine Datenbank neu
aufsetzt. Wird hier festgehalten statt still repariert — die Ursache liegt in einer
älteren Migration und gehört in einen eigenen, kleinen Schritt, nicht als Beifang in
diesen.

Der Prüfweg für die neue Migration war deshalb: auf `b2e8f4a6c1d7` stempeln, dann
`upgrade head` — damit wird genau die eine Migration ausgeführt und sonst nichts.

## 8. Was das Fundament nicht kann

Ehrlichkeitshalber, damit später niemand mehr erwartet, als da ist:

- **Es lädt nichts herunter.** Die Shot-Liste muss von außen kommen.
- **Es erkennt keine Schnitte.** Die Shot-Detection selbst (PySceneDetect o. ä.) ist nicht
  Teil davon — sie braucht eine Datei, und die Dateifrage ist offen.
- **Es sagt nichts über Bildinhalt.** Hook-Typ, Titelkarten, Gesichter — das wäre die
  semantische Ebene mit einem Vision-Modell (~$73 für die ganze Kohorte, siehe Vorstufe
  Abschnitt 4). Erst sinnvoll, wenn der Schnitt-Rhythmus etwas gezeigt hat.
- **Es beantwortet die Frage aus Stufe 3 noch nicht.** Es macht sie beantwortbar, sobald
  Material da ist.

## 9. Der nächste Schritt gehört nicht mir

Alles Quellenunabhängige ist gebaut. Was jetzt fehlt, ist eine Entscheidung, kein Code:

**Zwanzig Trailer-plus-Cutdown-Paare aus dem eigenen Bestand des Trailerhauses** —
derselbe Film in Langform und als Cutdown. Mehr braucht der Machbarkeitsnachweis nicht;
Abschnitt 3 zeigt, dass zwanzig Paare für einen schwachen Effekt 84 % Trefferwahrschein-
lichkeit haben.

Liegen die Dateien vor, ist der Weg kurz: Shot-Detection darüberlaufen lassen,
`extract_features` je Video, `compare_pairs` über die Paare. Das ist ein Nachmittag, kein
Sprint.

Kommt das Material nicht zustande, bleibt die Möglichkeit aus Abschnitt 5c der Vorstufe:
der Befund aus Stufe 3 steht, ohne handwerkliche Erklärung. Auch ein vertretbares
Ergebnis — nur eben ein kleineres.
