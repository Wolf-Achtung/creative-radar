# Creative Radar — Phase 4 Bilanz

**Zeitraum:** 2026-04-30 bis 2026-05-02 (Mini-Run-Strategie, ein Arbeitstag pro Wochen-Sprint)
**Operating-Modus:** Pilot-Phase, ~5 vertraute User, Trailerhaus-Kontext.

## Was geliefert wurde

**Stabilisierung & Sicherheitsnetze.** Build-Reproduzierbarkeit hergestellt, Konfig-Drift entfernt, Insights-Counter ehrlich gemacht, Frontend-Dependencies pinned, Alembic-Baseline angelegt.

**Asset-Capture-Persistenz auf Cloudflare R2.** Asset-Bilder überleben jetzt Railway-Deploys. Capture-Pipeline schreibt direkt nach R2 (S3-API), 14 von 18 Bestandsassets via Backfill-Skript migriert. Die 4 fehlgeschlagenen Migrationen sind durch TikTok-CDN-Hotlink-Schutz erklärbar und werden beim nächsten regulären Apify-Run automatisch neu erfasst.

**Visual-Pipeline ehrlich gemacht.** Statt einem `done`-Sammelstatus, der jeden Vision-API-Aufruf als erfolgreich markiert, gibt es jetzt vier neue Fehler-Status (`vision_empty`, `vision_timeout`, `vision_error`, `image_unreachable`), die in Counter und Selector korrekt als „nicht abgeschlossen" gewertet werden. Plus drei kleine Hebel: Sprach-Whitelist (kein halluzinierter Freitext im `language`-Feld mehr), explizite „keine Analyse möglich"-Phrase wenn Bild und Caption fehlen, Counter-Korrektur für den `analyzed`-Status.

**DB-Trennung ins eigene Schema.** Alle 8 Creative-Radar-Tabellen plus Cost-Log und Alembic-Versionstabelle leben jetzt im `creative_radar`-Schema, getrennt von Fremd-Projekt-Tabellen wie `appetizer_*` oder `auth_audit`. Migration mit `ALTER TABLE SET SCHEMA` ohne Datenverlust ausgeführt. Backup-Sicherung vom 2026-05-01 19:06 UTC bleibt 7 Tage.

**Performance-Indizes (8 total).** Fünf für die Hot-Read-Pfade (Time-Range-Queries, Channel-Filter, Title-Joins, Review-Status-Filter, Visual-Analysis-Status-Counter), drei für die Cost-Log-Tabelle. Alle CONCURRENTLY erstellt — kein Tabellen-Lock während des Builds.

**Bearer-Token-Auth.** Backend-Endpoints sind jetzt mit `Authorization: Bearer …` geschützt. Public bleiben nur Health, Docs, Image-Proxy und Report-Downloads (technische Limitierung von `<img>`/`<a href>`). Das Frontend liest `VITE_API_TOKEN` aus dem Build-Time-ENV. **Sicherheits-Wort:** der Frontend-Token ist Anti-Bot-Schutz, nicht User-Auth — User-Sessions sind Phase-5-Thema.

**Cost-Logging für Apify und OpenAI.** Jeder bezahlte externe Call wird in `creative_radar.costlog` persistiert mit Provider, Operation, USD-Cents, EUR-Cents und JSON-Metadaten (Token-Counts, Run-IDs, Asset-IDs). Kein Hard-Cap (Phase 5+); ein Read-Endpoint `GET /api/admin/cost-summary` liefert tägliche/wöchentliche Bilanzen für manuelles Monitoring.

## Erledigte Roadmap-Punkte

F0.1 (Asset-Capture), F0.2 (DB-Trennung), F0.3 (Auth), F0.4 (Visual-Pipeline ehrlich), F0.6 (Cost-Logging), F1.4 (Insights-Counter), F1.12 (Frontend-Pinning), F1.14 (Alembic produktiv), F2.14 (Tote Pfade), F2.15 (Branch-Hygiene), F2.17 (.env.example), F2.18 (Indizes).

## Was nicht in Phase 4 gemacht wurde (bewusst)

- **Hard-Cost-Cap.** Cost-Logging gibt heute Sichtbarkeit; ein automatischer Stopper bei Schwellenüberschreitung kommt in Phase 5+.
- **DSGVO-Sprint** (F0.7). Wolf-Entscheidung: für die Pilot-Gruppe Doku-tolerabel, eigener Sprint später.
- **User-Accounts oder Session-Cookies.** Bearer-Token reicht für die Pilot-Phase.
- **Image-Proxy-Auth.** Public-Endpoint mit Host-Whitelist + Größenlimit; Signed-URLs sind Phase-5-Thema.

## Statistik

- **22 Pull Requests** gemerged (15 Feature-PRs + 4 Hotfix-PRs + 3 Cleanup-PRs)
- **161 Tests** grün am Phase-Ende (95 neue dazugekommen)
- **6 Lessons-Learned** in `docs/PHASE_4_DONE.md` dokumentiert (vier davon aus Production-Hotfixes)
- **Sprint-8.2-Tabu eingehalten:** keine Edits in `api/proxy.py`, `report_renderer_v2.py`, `frontend/src/App.jsx ImagePreview`. Einzige Ausnahme: `report_selector.py` wurde in W3 für F0.4 (Object-Key-Erkennung) lokal freigegeben und erweitert.

## Was als nächstes ansteht

Detaillierter Phase-5-Backlog in `docs/PHASE_4_DONE.md`. Die wichtigsten Punkte für externe Stakeholder:

1. **Größere Pilot-Gruppe →** User-Accounts statt Token-Whitelist
2. **Mehr Apify-Volumen →** Hard-Cost-Cap mit automatischem Stopper
3. **Whitelist-Wachstum →** Bootstrap-Skripte für Fresh-Install-Setups
4. **Vision-Qualität →** größere Sample-Stichprobe für Prompt-Anti-Halluzination

## Operating-Modus für Phase 5

Mini-Run-Strategie hat funktioniert: ein Push pro Task, sichtbarer Fortschritt, niedrige Rollback-Kosten. Diagnose-First für riskante Operationen (DB-Migration, Auth-Aktivierung) hat Production-Crashes auf vier kurze Hotfix-Episoden begrenzt — alle in <30 Min auf Hauptast zurückgespielt. Belt-and-Braces-Tests (Subprocess-Postgres-Probes, Layout-Probes, Regression-Guards) haben drei der vier Bug-Klassen gefangen, bevor sie nochmal Production-relevant wurden.
