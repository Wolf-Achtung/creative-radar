# Creative Radar Roadmap — Mai 2026

Stand 06.05.2026 nach Block 1 v2 Voice-Patch und Wolf-Feedback.

## Kontext

Wolf am 06.05. nach Lektüre der ersten 6 v1-Briefe:
- Sprache war Marketing-Manager-Sprech, nicht Cutter-Deutsch (gefixt mit Block 1 v2)
- DE/US-Vergleich erschlägt jeden Brief, generelle Branchen-Sicht fehlte (gefixt mit Block 1 v2 konkurrenz-Sektion)
- "Was generell fehlt: ein Learning aus den Analysen für die Kreativen, damit sie wissen, was die anderen tun und einen Weg erhalten, was für ihre Projekte 'state of the art' ist." — adressiert mit Block 7-9 unten

## Aktiver Production-Stand

- Block 1 v2 — Trailerhaus-Voice + Konkurrenz-Sektion (gemerged 06.05.)
- Block 2.5 — Async-Refactor mit Pool-Tuning (gemerged 06.05.)
- Pair-Briefe für 6 Tier-A-Studios laufen wöchentlich

## Geplante Reihenfolge

### Block 7 — State-of-the-Art-Brief (höchste Priorität)
Ein zusätzlicher Brief-Typ, der über alle 6 Pairs hinweg aggregiert. "Was musst du diese Woche gesehen haben?" Niedrigste Schwelle, größter unmittelbarer User-Wert. Vorbedingung für Block 8.

### Block 8 — Personalisiertes Cutter-Briefing
Cutter trägt sein Projekt-Profil ein, kriegt 5 Referenzen + Pattern + Hook-Empfehlungen für genau sein Vorhaben. Setzt Block 7 voraus (Aggregator-Reuse). Vorbedingung idealerweise Block 4 (Templating) oder parallel.

### Block 9 — Hall of Fame Dashboard
"Currently Trending" (algorithmisch, letzte 7 Tage) plus "Hall of Fame" (hybrid kuratiert, letzte 6 Monate). Persistenter Niveau-Anker, kein wöchentlicher Refresh nötig. Setzt Block 7 voraus.

### Block 3 — Producer-Quality-Pass (Wolf-Aufgabe, 07.05. im Zug)
Lese-Session über die 6 v2-Briefe nach dem heutigen Voice-Patch. Output: Markdown-Notiz docs/feedback/PRODUCER_QUALITY_PASS_2026-05-06.md. Vorbedingung für Block 4.

### Block 4 — Briefing-Templating
Multi-View-Selektoren (Insight-Standard, Disney+-Format, Marvel-Format, etc.) im Frontend. Wartet auf Block 3-Erkenntnisse plus Trailerhaus-Tag-Feedback (08.05.).

### YouTube-Integration (eigener Sprint, kein Block-Nummer)
Wolf-Beobachtung 06.05.: aus Trailerhaus-Sicht möglicherweise wichtiger als TikTok, weil dort offizielle Trailer landen. Eigener mehrtägiger Sprint, nach Block 7-9.

### Block 5 — Title-Tagging-Sprint
Wartet auf finales Briefing von Wolf. Möglicherweise Vorbedingung für Block 8 (Genre-Annotation).

### Block 6 — Cron-Plattform-Parallelisierung (im Backlog)
IG- und TT-Polling parallel via asyncio.gather. ~5 min Latenz-Einsparung. Niedrige Priorität — User spüren das nur indirekt. Wartet auf YouTube-Integration (dann lohnt sich der Refactor mit dritter Plattform).

## Entscheidungen für 08.05. bei Trailerhaus

Mit Block 1 v2 + den drei Briefings (7-9) committed kannst du sagen:

"Heute zeige ich euch v2 der Briefe, die ich euch beim letzten Mal gezeigt habe — neue Sprache nach eurem Feedback. Plus drei Sprints, die ich danach in dieser Reihenfolge angehe: ein Branchen-Brief über alle Studios hinweg, ein personalisiertes Cutter-Briefing wo ihr euer Projekt eintragt, und ein dauerhaftes Hall-of-Fame-Dashboard. Welcher davon hilft euch zuerst?"

Die Antwort dort entscheidet, ob die Reihenfolge 7-8-9 bleibt oder kippt.
