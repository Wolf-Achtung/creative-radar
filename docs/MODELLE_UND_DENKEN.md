# Welches Modell denkt — und was das kostet

Kurzfassung: Denk-Tokens teilen sich `max_tokens` mit der Antwort und
werden wie Ausgabe abgerechnet. Ob ein Modell ohne `thinking`-Parameter
denkt, hängt am Modell. Deshalb kann ein Modellwechsel das Verhalten
ändern, ohne dass eine Zeile Code sich ändert.

Genau das ist am 18.08.2026 passiert.

## 1. Die Tabelle

| Modell | denkt ohne Parameter? | kennt `effort`? |
|---|---|---|
| Haiku 4.5 | nein | **nein** — schickt man ihn mit, kommt ein 400er |
| Sonnet 4.6 | nein | ja |
| **Sonnet 5** | **ja** | ja |
| Opus 4.6 / 4.7 / 4.8 | nein | ja |
| **Opus 5** | **ja** | ja |
| **Fable 5** | **ja, immer** | ja |

Das feste Denk-Budget (`budget_tokens`) gibt es nicht mehr. Auf allen
aktuellen Modellen liefert es einen 400er. An seiner Stelle steht
`output_config.effort` mit den Stufen `low` bis `max`.

Stand der Tabelle: 18.08.2026. Sie steht ein zweites Mal als Code in
`backend/app/tests/test_model_defaults_drift.py` — dort ist sie
ausführbar.

## 2. Was bei uns läuft

| Aufrufort | Modell | `max_tokens` | `effort` |
|---|---|---:|---|
| Format / Tonalität | Haiku 4.5 | 2.000 | keiner (verträgt ihn nicht) |
| Zweck / Kampagnenphase | Sonnet 5 | 2.000 | `low` |
| Bildbeschreibung | Sonnet 5 | 2.000 | `low` |
| Forecast-Einordnung | Opus 4.8 | 400 | keiner |
| Briefs, Roundups, Weeklies | Opus 4.8 | 20.000 | keiner |

Die Opus-Pfade sind bewusst unverändert: Opus 4.8 denkt ohne Angabe
nicht, und ihre Textextraktion iteriert bereits über alle Blöcke.
**Beim Wechsel auf Opus 5 ist das neu zu entscheiden** — dort denkt das
Modell in der Voreinstellung. Der Wächter aus Abschnitt 4 erzwingt diese
Entscheidung.

## 3. Der Fehler vom 18.08.

Zwei Dinge, beide vom Denken ausgelöst, beide vorher unsichtbar.

**Die Antwort verschwand.** `_message_text` nahm `content[0]`. Denkt ein
Modell, steht ein `thinking`-Block an erster Stelle — der Zugriff
lieferte einen leeren String, die Antwort dahinter fiel unter den Tisch.
Kein Abschneiden, sondern ein harter Ausfall für den betroffenen Post.
`insight_engine` und `forecast` iterieren aus genau diesem Grund über
alle Blöcke; im `post_analyzer` war die Stelle übersehen worden.

**Ein Fragment wurde gespeichert.** Der Vision-Pfad gab zurück, was
ankam. Eine mitten im Satz endende Beschreibung sah aus wie eine kurze,
wanderte unbemerkt in die Auswertung, und der Idempotenz-Sprung hielt
sie für erledigt.

Warum es trotzdem kaum weh tat: Adaptives Denken ist adaptiv. Vier
Felder aus einer Bildunterschrift zu ziehen ist trivial, also dachte das
Modell meist gar nicht. Der Backfill über 2.780 Posts blieb unter einem
Prozent Fehler. Das alte feste Budget hätte einen Block reserviert,
egal wie leicht die Aufgabe war — adaptives Denken tut das nicht.

Es war also kein Schaden, sondern ein Münzwurf, dessen Ausgang wir nicht
steuern.

## 4. Was jetzt dagegen steht

`backend/app/tests/test_model_defaults_drift.py`, zehn Prüfungen, ohne
Datenbank und ohne Schlüssel, Laufzeit unter einer Sekunde. Sie liest
die Quelltexte statt sie auszuführen:

- Jedes eingesetzte Modell muss in der Tabelle stehen. Wer eines
  tauscht, stolpert hier und nicht in Produktion.
- Denkt ein Modell per Voreinstellung, muss an jedem Aufrufort `effort`
  **und** `max_tokens` hingeschrieben sein.
- Modelle ohne `effort`-Unterstützung dürfen ihn nicht bekommen.
- Kein `budget_tokens`, kein `output_format`, keine Sampling-Parameter
  an Anthropic.
- Kein blinder `content[0]`-Zugriff in Dateien mit Anthropic-Aufrufen.

Jede dieser Prüfungen wurde gegen eine Mutation gehalten: Modell
getauscht, `effort` entfernt, Limit gesenkt, alter Zugriff
wiederhergestellt — fünf von fünf schlagen an.

`.github/workflows/model-drift.yml` führt das täglich um 05:20 UTC aus,
dazu bei jeder Änderung an Konfiguration, Diensten oder
Abhängigkeiten. Ein zweiter, rein berichtender Schritt hält die
wichtigsten Pakete gegen PyPI und schreibt das Ergebnis in die
Lauf-Zusammenfassung.

**Ein roter Lauf heißt nicht „kaputt", sondern „es hat sich etwas
verschoben, sieh hin".** Meist genügt ein Eintrag in der Tabelle —
nachdem geklärt ist, ob das neue Modell ohne Angabe denkt.

## 5. Was daraus zu lernen war

**Eine Voreinstellung ist keine Entscheidung.** Der Code sagte nicht,
was er wollte. Dass es funktionierte, lag am Modell.

**Ein Mengenlimit ist kein Zeitlimit — und ein Deckel ist keine
Reservierung.** `max_tokens` anzuheben kostet nichts, solange der Raum
ungenutzt bleibt. Knapp bemessen ist er trotzdem gefährlich, weil sich
Denken und Antwort denselben Topf teilen. Die Länge geben die Prompts
vor, nicht das Limit.

**Stille Degradierung ist teurer als ein Fehlschlag.** Die
Klassifikation warf einen Fehler und war damit sichtbar. Die
Bildbeschreibung speicherte ein Fragment und war es nicht.
