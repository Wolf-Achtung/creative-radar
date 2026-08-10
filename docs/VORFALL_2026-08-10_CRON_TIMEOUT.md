# Vorfall 10.08.2026 — der Wochenlauf lief ins Gesamt-Timeout

Der Montagslauf brach nach 2,5 Stunden ab. Die Rohdaten waren da, aber **sämtliche
Auswertungen fehlten** — Briefs, Roundups, Cutter- und Designer-Wochenbriefing. Behoben
am selben Vormittag; die strukturelle Ursache ist mit dieser Änderung beseitigt.

Festgehalten, weil der Fehler lehrreich ist und der Auslöser eine Empfehlung war, die in
einem unserer eigenen Dokumente stand.

## 1. Zeitachse

| Zeit (UTC) | Ereignis |
|---|---|
| 03:00:49 | Scheduler löst planmäßig aus |
| bis 03:47 | Scrape fertig — 475 neue Posts (289 IG, 165 TT, 21 YT) |
| 03:47–05:30 | Vision, Vision-Backlog, **Post-Analyse** — hier blieb er |
| 05:30:49 | `total_run_timeout after 9000s`, Lauf hart beendet |
| 08:32 | manueller Neustart nach Konfigurationsänderung |
| 10:00 | `completed` nach 4.998 s, Kette vollständig durch |

## 2. Ursache

Die Grundlaufzeit lag seit Wochen bei rund **7.200 Sekunden**, die Obergrenze bei
**9.000**. Also gut 30 Minuten Luft.

| Datum | Laufzeit | Status |
|---|---:|---|
| 13.07. | 5.586 s | completed |
| 20.07. | 7.667 s | completed |
| 27.07. | 6.895 s | completed |
| 03.08. | 7.174 s | completed |
| **10.08.** | **9.000 s** | **abgeschnitten** |

Am 10.08. lief zum ersten Mal die Post-Analyse-Stage mit, konfiguriert auf
`CRON_POST_ANALYSIS_MAX_POSTS_PER_RUN = 2500`. Bei gemessenen **3,7 Sekunden pro Post**
sind das rund 2,5 Stunden zusätzlich — eine Verdopplung des Laufs. Die 30 Minuten Luft
reichten dafür nicht annähernd.

**Der eigentliche Konstruktionsfehler:** Ein Cap in *Posts* sagt nichts über *Zeit*. Er
begrenzt Kosten, aber nicht die Laufzeit. Und weil die Post-Analyse früh in der Kette
steht — nach Vision, vor `title_sync`, `rematch`, Briefs und Roundups — riss sie alles
mit, was hinter ihr liegt.

Nebenbefund derselben Rechnung: Auch der **Standardwert 800** hätte rund 2.960 Sekunden
gebraucht und damit ebenfalls über der Grenze gelegen. Die Stage hätte in dieser
Konstruktion nie zuverlässig laufen können. Es fiel nur nie auf, weil sie zuvor nie zum
Zug kam.

### Woher die Empfehlung kam

`TRAILER_INTELLIGENCE_STAGE1_ERGEBNISSE.md`, Abschnitt 14.3, empfahl den Wert 2500. Die
dortige Rechnung betrachtete **nur die Kosten** (~$7 pro Lauf), nicht die Laufzeit gegen
das Gesamt-Timeout. Ironischerweise stand die nötige Zahl im selben Abschnitt: „8.000
Posts wären sechs bis sieben Stunden" — daraus folgen 2,9 s/Post und für 2500 Posts rund
zwei Stunden. Die Verknüpfung fehlte.

## 3. Was der Vorfall *nicht* war

- **Kein Scheduler-Problem.** 03:00:49 ist punktgenau der erwartete Zeitpunkt.
- **Kein Datenverlust.** Der Scrape war vor dem Abbruch fertig und hatte committet.
- **Nicht einmal ein Verlust der Post-Analyse-Arbeit:** Die Stage committet pro Post. In
  den 103 Minuten vor dem Abbruch hat sie rund **1.100 Posts analysiert**, die alle
  erhalten blieben. Die Klassifikations-Abdeckung stieg dadurch von 13 % auf 28,3 %,
  obwohl der Lauf als gescheitert galt.

Verloren ging allein `summary_json` — es wird erst am Ende geschrieben. Deshalb stand
dort der JSON-Skalar `null` und die Diagnose sah zunächst schlimmer aus als die Lage.

## 4. Behebung

### Sofort (Konfiguration, 10.08.)

| Variable | vorher | nachher | Grund |
|---|---|---|---|
| `CRON_POST_ANALYSIS_MAX_POSTS_PER_RUN` | 2500 | 0 | Stage aus dem Lauf nehmen |
| `CRON_TOTAL_RUN_TIMEOUT_SECONDS` | 9000 | 12000 | Marge wiederherstellen |
| `CRON_RUN_TIMEOUT_MINUTES` | 180 | 210 | muss **über** dem Gesamt-Timeout liegen, sonst erklärt der Aufräumer einen noch arbeitenden Lauf für tot |

Danach manueller Neustart über die GitHub-Action „Cron Sync" (`workflow_dispatch`) — in
88 Minuten sauber durch, alle vier Artefaktarten frisch erzeugt.

### Backlog, entkoppelt

Der Rückstand wurde außerhalb des Cron abgearbeitet, über `backfill_post_analyzer` im
Railway-Container, Pair für Pair. Ergebnis: **2.780 Posts, Abdeckung 28,3 % → 64,1 %**,
Fehlerquote unter 1 %, Kosten rund $8.

Dabei fiel die belastbare Messung an, die dieses Dokument durchzieht: **3,7 Sekunden pro
Post** mit `--skip-vision`.

### Strukturell (Code, dieser Änderungssatz)

`_run_post_analysis_backlog` bekommt ein **Zeitbudget** (`budget_seconds`, ENV
`CRON_POST_ANALYSIS_STAGE_TIMEOUT_SECONDS`, Default 1800 s wie `title_sync` und
`rematch`). Ist es aufgebraucht, steigt die Schleife **zwischen zwei Posts** aus, meldet
`timed_out` und `remaining` — und der Cron läuft zu Briefs und Roundups weiter.

Die Prüfung sitzt bewusst *in* der Schleife und nicht in einem `asyncio.wait_for` um den
`to_thread`-Aufruf: **ein Python-Thread lässt sich nicht abbrechen.** `wait_for` würde
nur die wartende Coroutine aufgeben, während der Thread im Hintergrund weiter API-Aufrufe
absetzt. Kooperativ auszusteigen ist hier sauber möglich, weil nach jedem Post committet
wird.

Der Default von 1800 s ist so gewählt, dass er **auch beim Code-Default von 9.000 s**
passt (7.200 + 1.800 = 9.000) — eine Umgebung, die den Gesamtwert nie angehoben hat, ist
damit ebenfalls geschützt.

## 5. Warum das dringend war

Mit `CRON_POST_ANALYSIS_MAX_POSTS_PER_RUN = 0` als Dauerlösung wäre die Abdeckung wieder
gefallen: Wöchentlich kommen rund 500 Posts herein, die niemand analysiert, während
analysierte hinten aus dem 90-Tage-Fenster fallen. Überschlagen **rund vier
Prozentpunkte pro Woche** — in vier Wochen zurück unter 50 %.

Der Backfill war eine Momentaufnahme. Erst das Zeitbudget macht den Cap wieder
gefahrlos setzbar.

**Empfohlener Wert nach diesem Deploy:** `CRON_POST_ANALYSIS_MAX_POSTS_PER_RUN = 800`.
Rechnung: 800 × 3,7 s = 2.960 s. Das übersteigt zwar den Stage-Default von 1.800 s — die
Stage steigt dann nach rund 485 Posts aus und holt den Rest in der Folgewoche. Genau so
soll es sein: **der Zeitdeckel gewinnt, der Cap ist nur noch die Kostenbremse.** Wer
beides ausschöpfen will, setzt zusätzlich
`CRON_POST_ANALYSIS_STAGE_TIMEOUT_SECONDS = 3000` — das passt unter die produktiven
12.000 s.

## 6. Was wir daraus mitnehmen

**Ein Mengenlimit ist kein Zeitlimit.** Caps in Stück begrenzen Kosten. Gegen Timeouts
hilft nur ein Limit in Sekunden. Wo beides gebraucht wird, müssen beide da sein.

**Eine Empfehlung braucht die Rechnung, gegen die sie sich richtet.** Abschnitt 14.3
rechnete Kosten und übersah die Laufzeitgrenze, obwohl die nötigen Zahlen daneben
standen.

**Marge ist keine Sicherheit, wenn sie nicht überwacht wird.** Die Laufzeit lag seit
Wochen bei 80 % der Grenze. Niemand hat hingesehen, weil nichts fehlschlug — der
Statusbericht (`scripts/plan_b_status.py`) zeigt sie jetzt bei jedem Aufruf.

**Diagnose braucht die richtige Tabelle.** Zwei Fehlgriffe kosteten an diesem Vormittag
Zeit: ein fehlender `::jsonb`-Cast (`analysis` ist eine `json`-Spalte, den `?`-Operator
gibt es nur für `jsonb`) und ein Blick in `weeklyreport` statt `insight_report`. Beides
steht jetzt einmal richtig im Statusskript.

## 7. Offen

- Die Artefakt-Zeitstempel liegen etwa zwei Stunden neben der `cron_run`-Startzeit —
  vermutlich uneinheitliche Zeitzonenbehandlung zwischen den Tabellen. Kosmetisch, aber
  einen eigenen Blick wert.
- Der Rest-Backlog von rund 2.700 Posts betrifft Kanäle außerhalb der neun aktiven Pairs.
  Für die Auswertungen nicht nötig, für Vollständigkeit offen.
