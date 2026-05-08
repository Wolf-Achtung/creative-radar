# 02 — Insight-Engine-Audit

## Was wurde geprüft

- `backend/app/services/insight_engine.py` (1250 LOC, vollständig gelesen).
- `backend/app/schemas/insights.py` (Pydantic-Modelle).
- `backend/app/api/insights.py` (Endpoint).
- `grep -n "platform"` über `insight_engine.py` und Anrufer.
- Frontend-Render in `frontend/src/InsightWeekly.jsx`.

## Befunde

### PAIRS — vollständige Definition (`insight_engine.py:78-162`)

Sieben Tier-A-Pairs, **alle** mit `"platform": "tiktok"`. Sechs aktiv,
einer (`universalpictures`) per `enabled=False` mit strukturiertem `reason`
deaktiviert.

```python
PAIRS = {
  "warnerbros":        {label: "warnerbros DE+US",        platform: "tiktok", channels: [{warnerbros US}, {warnerbrosdeutschland DE}], enabled: True},
  "sonypictures":      {label: "sonypictures DE+US",      platform: "tiktok", channels: [{sonypictures US}, {sonypicturesgermany DE}], enabled: True},
  "primevideo":        {label: "primevideo DE+US",        platform: "tiktok", channels: [{primevideo US}, {primevideode DE}], enabled: True},
  "disney":            {label: "disney DE+US",            platform: "tiktok", channels: [{disney US}, {disneyde DE}], enabled: True},   # !! disney US
  "netflix":           {label: "netflix DE+US",           platform: "tiktok", channels: [{netflix US}, {netflixde DE}], enabled: True},
  "paramountpictures": {label: "paramountpictures DE+US", platform: "tiktok", channels: [{paramountpics US}, {paramountpicturesgermany DE}], enabled: True},
  "universalpictures": {label: "universalpictures DE+US", platform: "tiktok", channels: [{universalpictures US}, {universalpicturesde DE}], enabled: False, reason: "US-Channel @universalpictures has 0 posts/30d, ..."},
}
```

`disney` US erwartet Handle exakt `disney` — Whitelist hat aber
`disneystudios` und `disneyanimation`. Siehe `01-DATABASE-INVENTORY.md` und
`06-OPEN-QUESTIONS.md` Frage 2.

### `platform`-Hardkodierung — nur an einer Stelle

`grep -n "platform=\"\|platform='\|platform\s*==\b"` in `insight_engine.py`:

- `insight_engine.py:616` — `Channel.platform == platform` in `_find_channel`,
  und `platform` ist Funktions-Parameter (kommt aus `pair_def["platform"]`).
- Keine `if platform == "tiktok"` Verzweigungen. Keine TikTok-spezifische
  Aggregations-Logik.
- **TikTok-Bindung sitzt ausschließlich im PAIRS-Dict** (sieben Mal
  `"platform": "tiktok"`).

Damit ist der Schritt zu IG/YT konfigurations-, nicht code-getrieben — was
gut ist. Aber: `aggregate_pair` arbeitet pro Pair mit genau zwei Channels
(DE + US), nicht mit einer Plattform-übergreifenden Sicht. Multi-Plattform
V2a (TT + IG in einem Studio-Brief) braucht eine **dritte Achse**:
Pair × Plattform × Markt.

### SYSTEM_PROMPT (`insight_engine.py:207-537`, 330 Zeilen)

Sehr umfangreich — vollständiges Zitat im Quellfile. Strukturell:

1. **Persona-Anker** (Zeile 208-218): "älterer Creative Producer bei
   Trailerhaus", Sprache zum Cutter.
2. **Voice-Regeln** (Zeile 210-218): kein Marketing-Deutsch, konkrete Zahlen,
   Beobachtung statt Ansage.
3. **Glossar** (Zeile 220-221): erlaubte englische Begriffe (Hook, Beat,
   Cut, Cold-Open, L3, BTS, Texted, Textless, GSA, Tonalität, Trailer,
   Teaser, Spot, Establisher-Shot).
4. **Umlaut-Regel** (Zeile 223): explizit "echte Umlaute, nicht ae/oe/ue/ss".
   PR #78 (Trailerhaus Voice Prompt v1) gemerged. **Im Source-File sind
   einige Beispiel-Strings dennoch in Pseudo-Umlaut-Form** (z. B. Zeile 281
   "aendern", Zeile 282 "beruht") — Few-Shot enthält Mischformen, die
   Regel im Prompt überschreibt das aber explizit.
5. **Deutsche Alternativen** (Zeile 225-229): Totale, Anfangs-Einstellung,
   Aufschlag, Hauptaussage statt Establisher/Hook/Key-Message.
6. **Anti-Pattern-Block** (Zeile 231-245, 14 Zeilen): explizit verboten:
   `Pace*, Engagement-Rate*, CTA*, Catalog-*, Discovery-Cut, Cadence,
   Hook-Disziplin/-Architektur, Brand-*, Live-Event-Framing, Reminder-Modus,
   Fan-Service-Loop, Reaktions-Magnet, Hashtag-Treffer, Asset-Cuts,
   Tagging der Creators`.
7. **Tonalitäts-Pool** (Zeile 247-248): 14 Adjektive zur Auswahl.
8. **Längen-Vorgabe** (Zeile 250): 1500-2000 Wörter Output.
9. **Output-Schema** (Zeile 252-340): JSON mit den Feldern
   `headline, tldr, aktuell_im_fokus, ganz_konkret, trends, actions,
   konkurrenz, cross_market_insight, risks, data_caveats, tonalität,
   watch_outs, für_cutter, für_motion_designer, für_creative_producer,
   vergleichbare_posts`.
10. **Befüllungs-Hinweise zu `aktuell_im_fokus`** (Zeile 342-351).
11. **Befüllungs-Hinweise zu `ganz_konkret`** (Zeile 354-373) — explizite
    Lern-Modus-Erklärung (v3.0): "Trailerhaus ist KEIN Inhouse-Studio".
12. **Few-Shot** (Zeile 375-536): vollständiges synthetisches Warner-Bros-Beispiel.
    Referenzierte URLs sind alle synthetisch (`https://tiktok.com/@warnerbros/video/us1` etc.) — **keine Plattform-Vermischung im Few-Shot**, alles TikTok.

### `headline` / `tldr` / `summary`

- `headline` und `tldr` werden im **selben** Prompt erzeugt (siehe Schema
  Zeile 254-256). Kein eigener Prompt-Block, kein zweiter LLM-Call.
- Es gibt **kein** `summary`-Feld. Die nahe verwandten Felder sind `tldr`
  (3 Sätze) und `headline` (max 90 Zeichen).
- `coverage_pct` und Caveat-Banner werden Frontend-seitig ausschließlich
  aus `aggregation.title_coverage.overall_coverage_pct` abgeleitet.

### LLM-Call (`generate_weekly_report`)

| Parameter | Wert |
|---|---|
| Modell | `claude-opus-4-7` (Alias) |
| Temperatur | nicht gesetzt → SDK-Default (Anthropic-Default) |
| `max_tokens` | 12000 (vorher 8000, in Sprint-Trailerhaus-Prompt-v1 gebumpt) |
| Calls pro Brief | **1** |
| System-Prompt | Konstante `SYSTEM_PROMPT` (~330 Zeilen) |
| User-Prompt | `_build_user_prompt`: framing-Text + `agg.model_dump(mode="json")` als JSON-Dump |
| Cost-Schätzung | $0.015/1k input + $0.075/1k output → **~$0.35-0.50 pro Brief** (Modul-Docstring), Endpoint sendet `cost_usd_estimate` an Frontend |
| `dry_run` | `?dry_run=true` skip LLM, gibt nur Aggregation zurück |
| Caching | Anthropic-prompt-caching **nicht aktiviert** (Kommentar-Hinweis: Sprint-2-Follow-up) |

### Aggregation (`aggregate_pair`)

`insight_engine.py:1027-1087`. Iteriert nicht über Plattformen — pro Aufruf
**genau ein Pair** (DE + US, eine Plattform). Aufgerufen aus:

- `backend/app/api/insights.py:73` (Endpoint, pro Request).

Keine Schleife über mehrere Pairs, kein Cron, keine DB-Persistenz nach Aufruf.

Pro Channel werden gerechnet (`_channel_stats:717-847`):

- `posts_count`, `assets_count`
- `coverage_pct` = Assets mit `title_id` / Assets gesamt
- `top_hashtags` (5)
- `avg_caption_length`, `avg_duration_seconds`
- `duration_buckets` (`<15s, 15-30s, 30-60s, >60s, unknown`)
- `top_posts` (3, sortiert nach `engagement_sum`)
- `historical_top_posts` (3, vorhergehende 180 Tage vor Window)
- `avg_engagement` = avg(`likes + comments + shares + bookmarks`)

Cross-Market-Match (`_cross_market_matches:850-958`): per
`asset.de_us_match_key`, mit `unknown`/`""` als ausgeschlossener Sentinel.

### Pydantic-Modelle (`schemas/insights.py`)

`PairAggregation` enthält: `pair_key, pair_label, platform, window_days,
window_start, window_end, iso_week, iso_year, de_channel: ChannelStats?,
us_channel: ChannelStats?, cross_market_matches: list[CrossMarketMatch],
title_coverage: TitleCoverage, notes: list[str]`.

`ChannelStats`: siehe oben + `historical_top_posts: list[TopPost]`.

`CrossMarketMatch`: `match_key, title?, de_engagement, us_engagement,
de/us_duration_seconds?, de/us_post_url?, de/us_caption_excerpt?`.

### Persistenz

**Keine.** `generate_weekly_report` gibt `InsightReport` als HTTP-Response
zurück; nichts wird in DB geschrieben. `weeklyreport`-Tabelle ist Legacy
(Phase-4-Reports), wird vom neuen Pfad nicht beschrieben.

→ Bei jedem Frontend-Open desselben Pairs läuft ein neuer LLM-Call (außer
`dry_run`) und kostet erneut ~$0.40. **Implikation für Cadence-Sprint:
ohne Persistenz oder Cache wird jeder Wochenend-Sync × N-Pair-Briefings
= N×$0.40 — und jedes Reload des GF.**

## Inkonsistenzen / Lücken

1. **Disney-US-Handle**: PAIRS `disney`, Whitelist `disneystudios` /
   `disneyanimation`. Entweder PAIRS auf `disneystudios` ändern, oder
   einen Channel mit Handle `disney` aufsetzen.
2. **`universalpictures` als Disabled-Placeholder im PAIRS-Dict**: Frontend
   `FALLBACK_LABEL` (`InsightWeekly.jsx:10-18`) listet ihn — Anwender kann
   die Karte sehen und 503 bekommen. Bewusst designed (`reason`-Feld), aber
   verwirrend für den GF.
3. **Few-Shot-Beispiel in Prompt enthält Pseudo-Umlaute** (Zeile 281,
   "aendern", "beruht") obwohl Umlaut-Regel (Zeile 223) das verbietet.
   Prompt-Ordnung "Regel überschreibt Beispiel" funktioniert in der Praxis,
   ist aber eine schwache Verteidigung. Nicht-blockend.
4. **Kein Caching, keine Persistenz**: jeder Call erzeugt einen frischen
   Report — Ranking-Sprint braucht stabile Asset-Sortierung pro Woche, was
   ohne Persistenz nicht reproduzierbar ist.
5. **`platform` ist im PAIRS-Dict, aber `aggregate_pair` rechnet pro Pair
   eine Plattform**: Multi-Plattform V2a-Sprint kann nicht ohne Refactor
   einen Studio-Brief mit TT+IG erzeugen — entweder wird PAIRS auf
   `platforms: [tt, ig]` erweitert und die Aggregation zieht beide Channels
   pro Markt, oder es gibt zwei Pair-Keys (`disney_tt`, `disney_ig`) und
   das Frontend stellt sie kombiniert dar.
6. **`headline` und `tldr` ohne separate Validierung**: kein Längen-Check,
   kein Re-Try bei Verstoß gegen Anti-Pattern. Der Prompt setzt auf das
   Modell, ohne Sicherungsnetz. Voice-Korrektur-Sprint sollte hier
   andocken.
7. **Tokens für In/Out**: Modul-Docstring nennt "8-12k input + 3-4k output";
   max_tokens=12000 erlaubt sehr lange Outputs. Bei mehreren Pairs pro
   Wochenende wird das nicht trivial.

## Risiken

- **Cost-Drift**: ohne Persistenz ist die Kosten-Größenordnung an die
  Anzahl Frontend-Reloads gekoppelt — nicht an die Anzahl Briefings pro
  Woche. Ein einziger eifriger GF kann pro Tag 6× $0.40 = $2.40 erzeugen.
- **Voice-Drift**: Sieben Tier-A-Pairs auf einen einzigen 330-Zeilen-Prompt
  → kleine Änderungen wirken sich auf alle Pairs gleichzeitig aus. Ein
  Voice-Sprint, der nur warnerbros testet, verändert auch Disney.
- **Disney-Pair-Bug** könnte in Production schon eine leere US-Sektion
  produzieren, ohne dass es jemandem aufgefallen ist (404-fest, aber
  US-`channel_found=False` rendert "Channel nicht in DB" — siehe
  `InsightWeekly.jsx:73-79`).

## Empfehlung für Sprint-Planung

1. **Vor Voice-Sprint**: Disney-Handle-Frage klären (Wolf-Decision in
   `06-OPEN-QUESTIONS.md`), Few-Shot-Beispiel auf echte Umlaute
   normalisieren (1-Zeichen-Edits, kein Risiko).
2. **Ranking-Sprint** kann **vor** Multi-Plattform V2a starten — die
   Ranking-Aggregation arbeitet pro Channel/Pair und braucht den
   Multi-Plattform-Refactor nicht. Aber wenn Ranking sortiert wird **bevor**
   die Aggregation Multi-Plattform-fähig ist, sortieren wir nur
   TikTok-Posts. Das ist OK als V1, sollte aber dokumentiert sein.
3. **Persistenz vor Cadence-Sprint**: spätestens beim Wochenend-Cadence
   muss der Brief einmalig erzeugt und auf Reload aus Cache/DB geladen
   werden. Sonst Cost-Risiko (siehe oben). Zwei Optionen:
   (a) `weeklyreport`-Tabelle reaktivieren mit `pair_key`-Erweiterung.
   (b) Neue `insight_report`-Tabelle mit JSON-Spalten.
   → Wolf-Decision in `06`.
