# Test-Failures-Diagnose 2026-05-12

**Operating-Mode:** Diagnose-only, keine Code-Änderungen
**Branch:** `docs/test-failures-diagnose-2026-05-12`
**Vorgänger-Sprints:** #129, #131, #132, #134 (alle gegen 540-549 Passing-Baseline gefahren, 9 Failures als "pre-existing" durchgewunken)

---

## TL;DR

**Alle 9 "Failures" haben dieselbe Wurzelursache:** `pytest-asyncio` ist weder in `backend/requirements.txt` aufgeführt noch via `pytest.ini` konfiguriert. Die 9 betroffenen Tests sind `@pytest.mark.asyncio`-dekoriert, pytest erkennt den Marker nicht (`PytestUnknownMarkWarning`), und die `async def`-Test-Funktionen werden mit der Standard-Meldung *"async def functions are not natively supported"* abgebrochen.

**Verifiziert:** lokaler `pip install pytest-asyncio` + `pytest --asyncio-mode=auto` → alle 12 Tests in den betroffenen Dateien grün (5 tmdb + 4 async_refactor + 3 sync-Tests im selben File). **0 echte Test-Bugs.**

**Klassifikation:** alle 9 fallen in Kategorie **D** (Environment-Abhängigkeit / Missing Dev-Dependency). Keine A/B/C/E-Cases.

**Fix-Aufwand:** **~5 Min**. Eine Zeile in `requirements.txt`, zwei Zeilen in `pytest.ini`.

---

## Phase 1: Failure-Inventar

Test-Run-Befehl: `DATABASE_URL="sqlite:///:memory:" python -m pytest app/tests/test_tmdb_client.py app/tests/test_async_refactor_per_channel.py -v --tb=short`

Ergebnis: `9 failed, 3 passed, 9 warnings in 4.82s`

| Datei | Test-Name | Fehler-Typ | Fehler-Message (gekürzt) |
|---|---|---|---|
| `test_tmdb_client.py` | `test_discover_movies_uses_expanded_release_type_mask` | `Failed (async)` | "async def functions are not natively supported. … pytest-asyncio …" |
| `test_tmdb_client.py` | `test_discover_movies_includes_digital_release_payload` | `Failed (async)` | dito |
| `test_tmdb_client.py` | `test_discover_movies_includes_tv_release_payload` | `Failed (async)` | dito |
| `test_tmdb_client.py` | `test_discover_movies_does_not_include_premiere_or_physical_in_mask` | `Failed (async)` | dito |
| `test_tmdb_client.py` | `test_discover_movies_still_includes_theatrical_types` | `Failed (async)` | dito |
| `test_async_refactor_per_channel.py` | `test_one_channel_of_three_raises_others_succeed` | `Failed (async)` | dito |
| `test_async_refactor_per_channel.py` | `test_all_three_channels_raise_status_completed_failed_channels_three` | `Failed (async)` | dito |
| `test_async_refactor_per_channel.py` | `test_async_asset_creation_runs_in_parallel` | `Failed (async)` | dito |
| `test_async_refactor_per_channel.py` | `test_semaphore_limits_concurrent_openai_calls` | `Failed (async)` | dito |

**Zusätzliche Warnings (alle 9 Tests):**
```
PytestUnknownMarkWarning: Unknown pytest.mark.asyncio - is this a typo?
You can register custom marks to avoid this warning - for details,
see https://docs.pytest.org/en/stable/how-to/mark.html
```

Der Marker wird also gar nicht erkannt — pytest kennt ihn nicht als async-Trigger, weil das pytest-asyncio-Plugin fehlt.

---

## Phase 2: Failure-Klassifikation

Alle 9 → **Kategorie D (Environment-Abhängigkeit)**.

Begründung:
- **Kein A (echter Bug):** der Production-Code lässt sich nicht falsch testen — er wird gar nicht ausgeführt. Die Test-Funktionen werden mit `Failed` markiert, bevor irgendeine Assertion läuft.
- **Kein B (veraltet):** die Tests referenzieren Methoden/Konstanten, die ALLE im aktuellen Production-Code existieren (siehe Phase 3 + Phase 4).
- **Kein C (PR #79-Erbe):** der Brief-Verdacht greift nicht — siehe Phase 4.
- **Kein E (flaky):** Failures sind 100% deterministisch reproduzierbar mit oder ohne `--count`.

Verifiziert via:
```bash
pip install pytest-asyncio
DATABASE_URL="sqlite:///:memory:" python -m pytest \
  app/tests/test_tmdb_client.py app/tests/test_async_refactor_per_channel.py \
  --asyncio-mode=auto -q
# → 12 passed in 3.79s
```

---

## Phase 3: Code-Lesen pro Failure

Pro Datei (statt pro Test, weil alle Tests im File dieselbe Ursache + denselben Production-Code-Pfad haben):

### `test_tmdb_client.py` (5 Failures)

- **Was getestet:** `TMDbClient.discover_movies()` (`backend/app/services/tmdb_client.py:61`) — pin auf den Sprint-9-H1-Release-Type-Filter `2|3|4|6` (Theatrical + Digital + TV, ohne Premiere und Physical).
- **Tatsächliches Verhalten:** `discover_movies` ist eine `async def`-Methode, die `_get(...)` mit den richtigen Parametern aufruft. Tests sind reine Unit-Tests mit Mock-Recorder (`_RecordingClient`) — kein Network, kein DB, kein TMDb-API-Key nötig.
- **Fehler-Grund:** `@pytest.mark.asyncio` über `async def test_…` → ohne pytest-asyncio-Plugin wird die Async-Funktion nicht awaitet, pytest meldet `Failed`.
- **Production-Code-Stand:** unverändert seit `3cee3a1` (Sprint 9 TMDb-Match-Fix). Mask-String `"2|3|4|6"` ist in Z. 78 wörtlich, exakt wie die Tests pinnen.
- **Empfehlung:** pytest-asyncio installieren — keine Test-Anpassung nötig.
- **Aufwand bei Fix:** 0 Min (Tests sind bereits korrekt).

### `test_async_refactor_per_channel.py` (4 Failures)

- **Was getestet:** `_run_apify_sync_for_platform_async()` (`backend/app/api/monitor.py:337`) + `DEFAULT_OPENAI_CONCURRENCY` / `DEFAULT_HTTPX_CONCURRENCY` (Z. 63-64) + `_create_asset_from_item_async()` (Z. 243). Vier Szenarien: 1-von-3-Channels-fehlerhaft, 3-von-3-fehlerhaft, parallele Asset-Erstellung mit `asyncio.gather`, Semaphore-Cap auf OpenAI-Concurrency.
- **Tatsächliches Verhalten:** alle drei genannten Production-Symbole existieren im aktuellen `monitor.py`. Test stubt OpenAI- und httpx-Calls via `AsyncMock`/`patch` — deterministisch, kein Network.
- **Fehler-Grund:** identisch zu `test_tmdb_client.py` — `@pytest.mark.asyncio` + fehlendes Plugin.
- **Production-Code-Stand:** post-Revert-Re-Import (siehe Phase 4). Der Code, den der Test prüft, ist heute der live laufende Cron-/Monitor-Pfad.
- **Empfehlung:** pytest-asyncio installieren — keine Test-Anpassung nötig.
- **Aufwand bei Fix:** 0 Min (Tests sind bereits korrekt).

---

## Phase 4: PR #79 Revert-Kontext

Die Brief-Hypothese „Tests sind PR #79-Leichen, der Revert hat den Code gelöscht" passt **nicht**. Die Git-Spur ist:

| Commit | Datum | Was passierte |
|---|---|---|
| `9066b58` | (vor PR #79-Merge) | `refactor(monitor+cron): async asset-creation + per-channel error isolation` — Originalarbeit. |
| `a88f8cb` | — | Merge PR #79. |
| `7f71845` | 2026-05-06 | **Revert PR #79.** Diff: `monitor.py` -462/+115, `test_async_refactor_per_channel.py` **342 Zeilen gelöscht**, weitere fünf Dateien angefasst. |
| `c88481a` | 2026-05-07 | `ux: Brief-Header auf Trailerhaus-Sprache + Aufraeumen` — **168 Dateien, +33590 Zeilen.** Wholesale Re-Import des Codestands. Bringt `test_async_refactor_per_channel.py` zurück und `monitor.py` in der Async-Variante. |

→ **Der Revert wurde durch den Bulk-Re-Import einen Tag später effektiv rückgängig gemacht.** Der aktuelle Cron-/Monitor-Code IST die PR-#79-Implementierung, die Tests sind die PR-#79-Tests. Beide gehören zusammen und sind heute live-relevant (Cron läuft auf diesem Code).

**Konsequenz:** kein „Ghost-Code". Tests prüfen den realen Cron-Pfad, der gerade in Production läuft. Das macht sie nicht nur fixbar, sondern **wichtig**.

---

## Phase 5: B2-Relevanz

| Datei | B2-Relevanz | Begründung |
|---|---|---|
| `test_tmdb_client.py` | **mittel** | TMDB-Match-Key-Mechanik. B2 (UK Cross-Market-Matching) nutzt match_key indirekt — aber der TMDb-Discovery-Pfad selbst ist B2-orthogonal. Tests sichern „die Title-Discovery liefert Digital+TV-Releases, nicht nur Theatrical", was für UK-Titel relevant ist (Streaming-Releases zuerst). |
| `test_async_refactor_per_channel.py` | **hoch** | `_run_apify_sync_for_platform_async` ist genau der Channel-Iteration-Pfad, den B2 cross-market erweitert. Per-channel-Error-Isolation + Semaphore-Caps sind B2-Voraussetzungen, sonst killt ein UK-Channel-Fail den ganzen Run. |

→ **Mindestens `test_async_refactor_per_channel.py` muss vor B2 laufen.** `test_tmdb_client.py` mitzunehmen kostet nichts extra (dieselbe Plugin-Installation).

---

## Cluster-Empfehlung

### Quick-Win — der einzige Fix

Eine PR mit drei Zeilen Diff:

**`backend/requirements.txt`** — eine Zeile hinzufügen (Plugin-Version pinnen für Reproducibility):
```
pytest-asyncio==1.3.0
```

**`backend/pytest.ini`** — Auto-Modus aktivieren (keine `@pytest.mark.asyncio`-Anpassung an einzelnen Tests nötig):
```ini
[pytest]
pythonpath = .
asyncio_mode = auto
```

Alternativ ohne `asyncio_mode = auto`: die bestehenden `@pytest.mark.asyncio`-Marker reichen — dann nur Plugin in requirements aufnehmen, `pytest.ini` unverändert lassen. Auto-Mode ist etwas robuster (zukünftige `async def test_…` brauchen keinen Marker), aber rein optional.

**Aufwand:**
- requirements.txt-Edit: 30 Sek
- pytest.ini-Edit: 30 Sek
- Verification-Test-Run: 1 Min
- PR + Commit-Message: 3 Min
- **Total ~5 Min**

### Echte Fixes nötig

**Keine.** Alle 9 Tests sind korrekt, der Production-Code, den sie prüfen, ist korrekt. Es fehlt nur das Plugin.

### B2-Voraussetzungen

- **`test_async_refactor_per_channel.py`** muss vor B2 grün laufen — sonst hat B2 keine Confidence-Signal für die Channel-Iteration-Erweiterung.
- Folgt automatisch aus dem Quick-Win — kein separater Sprint nötig.

---

## Nebenbefund — Process-Hygiene

Diese 9 Failures haben sich seit Sprints **#129, #131, #132, #134** als „pre-existing, unrelated" gehalten. Die Wurzelursache (ein fehlendes Dev-Plugin) war die ganze Zeit ein 5-Min-Fix. Hypothesen warum es so lange überlebt hat:

1. **Boy-Who-Cried-Wolf:** die Failure-Message *"async def functions are not natively supported"* sieht aus wie „kann ich nicht reparieren, hängt am Test-Framework" → 5 Sprints lang übersprungen.
2. **Kein CI-Gate:** der Repo hat kein Backend-CI verdrahtet (Netlify-Checks sind frontend-only). Lokale Test-Läufe sind opt-in. Eine grüne Baseline wäre erzwingbar gewesen mit einem GitHub-Action-Job, der `pytest -q` läuft.
3. **Local-only Repro:** Wolf läuft Tests vermutlich nicht ständig lokal — der Failure ist nur sichtbar wenn jemand bewusst pytest startet.

Out-of-Scope für diesen Sprint, aber Kandidat für **Tech-Debt-3**: GitHub-Action mit `pytest -q --no-header` als required check auf PRs.

---

## Wolf-Ping

1. **Quick-Win freigeben?** Eine PR mit:
   - `pytest-asyncio==1.3.0` in `backend/requirements.txt`
   - `asyncio_mode = auto` in `backend/pytest.ini`
   
   Erwartete Auswirkung: Test-Baseline springt von **549 passing + 9 failed → 558 passing + 0 failed**. Keine Code-Touches, keine Test-Anpassungen. ~5 Min Sprint.

2. **`asyncio_mode = auto` oder explizite Marker?** Empfehlung: `auto`. Spart Wolf einen Marker-Decorator pro neuem `async def test_…` und ist konsistent mit den bestehenden zwei Test-Dateien (die haben den Marker aber er ist mit `auto` redundant — bleibt drin, schadet nicht). Alternativ explizit lassen, Plugin alleine reicht.

3. **Tech-Debt-3 (CI-Gate) als nächster Sprint?** Wäre der natürliche Folgeschritt — ein GitHub-Action-Job, der `pytest -q --no-header` als required check auf PRs läuft. Verhindert Wiederholung des Patterns. Optional, ich habe das nur als Beobachtung im Nebenbefund aufgenommen.

---

## Tagesreport

**Output:** dieses Doc
**Aufwand:** ~35 Min Diagnose

### Befund
- Failure-Anzahl: **9 (verifiziert)**, davon 5× tmdb / 4× async_refactor
- Kategorien-Verteilung: **A=0, B=0, C=0, D=9, E=0** — alle 9 sind dieselbe Wurzelursache (missing pytest-asyncio)
- B2-Relevant: **9 von 9** (zumindest indirekt — Quick-Win löst alle gemeinsam)
- Quick-Wins: **9** (alle Tests laufen nach Plugin-Install grün)
- Echte Fixes: **0**

### Nächster sinnvoller Schritt
- Wolf-Antwort zu Ping #1 abwarten → Implementations-Sprint mit ~5 Min Aufwand: Plugin in requirements + pytest.ini-Eintrag.
- Optional anschließend Tech-Debt-3 (Backend-CI-Gate).
