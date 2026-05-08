# Disney-Brief Snapshot — vor Sprint 3 Voice-Refactor

**Stand:** _<Wolf füllt Datum + ISO-Woche ein, nach Live-Pull>_
**Quelle:** `GET /api/insights/weekly?pair=disney` (vor PR-Merge, ohne `force`)

> Diese Datei ist das Diffing-Vehikel für die Sprint-3-Voice-Validierung.
> Wolf füllt den "vorher"-Block vor dem PR-Merge, den "nachher"-Block nach
> dem Force-Regenerate gegen den deployten Sprint-3-Stand. Die "Bewertung"-
> Items sind Ja/Nein-Checkboxen, damit der Vergleich auf einen Blick lesbar
> bleibt.

---

## Headline (vorher)

```
<Wolf füllt aus Live-API>
```

## TLDR (vorher)

```
<Wolf füllt aus Live-API>
```

## Bewertung gegen Begriffe-Inventar

- Coverage-Erwähnung in Headline/TLDR? `[ja/nein]`
- Cross-Market Match-Erwähnung? `[ja/nein]`
- Längen-/Duration-Bucket? `[ja/nein]`
- Engagement-Sum? `[ja/nein]`
- Zahlen ohne Einordnung? `[ja/nein]`
- GF-tauglich (subjektiv)? `[ja/nein/teilweise]`

---

## Headline (nachher)

```
<Wolf füllt nach Force-Regenerate>
```

## TLDR (nachher)

```
<Wolf füllt nach Force-Regenerate>
```

## Bewertung

- Verbotene Begriffe vermieden? `[ja/nein]`
- Eine Aussage in Headline (kein "und"-Konstrukt)? `[ja/nein]`
- TLDR-Pyramide (Hauptaussage zuerst, Beleg dahinter)? `[ja/nein]`
- Jede Zahl mit Einordnung? `[ja/nein]`
- GF-tauglich? `[ja/nein]`

---

## Cutter-Sektionen — Stichprobe (nachher)

> Pre-Commitment 3: Detail-Sektionen behalten Cutter-Voice, das ist
> Absicht. Hier nur prüfen, dass die Sektionen nicht versehentlich
> mit-refactored wurden.

- `fuer_cutter.schnitt_pace` enthält weiterhin Cutter-Vokabular
  (Cold-Open, Hook, Cut, Beat, etc.)? `[ja/nein]`
- `tonalitaet[].begruendung` mit Daten-Anker? `[ja/nein]`
- `vergleichbare_posts` rendert? `[ja/nein]`
