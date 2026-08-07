# Schritt 4 — die 1.005 kaputten Bildabrufe

Stand: 07.08.2026. Diagnose vor der Reparatur. Ziel: die Thumbnail-Abdeckung von
~76 % auf ~90 % heben, ohne blind gegen ein bereits zweimal repariertes System zu
patchen.

## 1. Das System wurde schon zweimal repariert — das ändert die Diagnose

Bevor irgendein Fix vorgeschlagen wird, ein Fund aus der Git-Historie, der die ganze
Fragestellung umdreht: **`fetch_failed` ist kein unbehandelter Bug, sondern ein Zustand,
der schon zwei gezielte Reparaturen hinter sich hat.**

- **10.06.2026:** `asset_screenshot_persistence.py` eingeführt. Seitdem wird das Bild
  **sofort beim Scrape** gesichert, bevor die Asset-Zeile überhaupt committet wird —
  zu einem Zeitpunkt, an dem der CDN-Link garantiert noch frisch ist.
- **13.07.2026 (Incident):** ein Bug behoben, der genau das wieder zunichtemachte. Die
  spätere Vision-Analyse rief `capture_asset_screenshot()` **unconditional erneut**
  auf und verwarf damit die schon gesicherte Kopie — sie griff stattdessen auf den
  Original-CDN-Link zurück, der bei Instagram nach 24–48 Stunden tot ist. Ein einzelner
  Lauf verbrannte laut Code-Kommentar $3 auf 200/200 `fetch_failed`, rein durch dieses
  erneute Fetchen.

**Das heißt: die naheliegende Diagnose — „CDN-Links laufen ab, also früher abrufen" —
ist bereits umgesetzt.** Ein dritter Fix in dieselbe Richtung würde nichts mehr bringen.
Die Frage ist nicht mehr *ob* früh abgerufen wird, sondern *woraus die 1.005 noch
bestehen*, nachdem beide Reparaturen längst laufen.

## 2. Drei Ursachen, drei völlig verschiedene Reparaturen

| Fall | Woran erkennbar | Reparierbar? |
|---|---|---|
| **A. Altbestand von vor dem 10.06.** | `visual_evidence_status` ist `NULL` — kein Ingest-Versuch existierte noch gar nicht | **Nein**, nicht rückwirkend. Die Original-CDN-Links dieser Posts sind seit Monaten tot. |
| **B. Ingest selbst schon `fetch_failed`** | `visual_evidence_status = 'fetch_failed'` (oder `no_source`), Asset nach dem 10.06. erstellt | Vermutlich **nicht am CDN-Timing**, sondern am Post selbst (gelöscht/privat) oder an der URL-Extraktion. Zu prüfen, nicht anzunehmen. |
| **C. Ingest hat `captured`, Analyse zeigt trotzdem `fetch_failed`** | `visual_evidence_status = 'captured'`, aber `visual_analysis_status = 'fetch_failed'`, Asset nach dem 13.07. | **Ja, ein echter Bug** — der Wiederverwendungs-Pfad aus dem Incident-Fix hätte hier greifen müssen und hat es nicht. |

Jeder Fall braucht eine andere Antwort. Fall A ist Verlust, kein Fehler. Fall B ist eine
Datenfrage (wie viele Posts sind wirklich weg?). Fall C wäre der einzige, für den noch
Code zu ändern ist.

**Fall C ist bereits gegen genau diese Bedingung getestet**
(`test_reuses_ingest_time_evidence_without_refetching`,
`test_visual_analysis.py:312`) — der Wiederverwendungspfad prüft
`visual_evidence_status == "captured" and visual_evidence_url`. Sollte Abschnitt 3
trotzdem Fall-C-Zeilen zeigen, ist der wahrscheinlichste Grund keine Logik-Lücke,
sondern eine Dateninkonsistenz: `visual_evidence_status = 'captured'` bei gleichzeitig
leerem `visual_evidence_url` (die Bedingung verlangt beides). Das lässt sich mit einer
Zeile prüfen:

```sql
SELECT count(*) FROM creative_radar.asset
WHERE visual_evidence_status = 'captured' AND visual_evidence_url IS NULL;
```

Ist das Ergebnis 0, gibt es in Fall C nichts zu programmieren, und die 1.005 sind
vollständig durch Fall A und B erklärt.

## 3. Diagnose-Query

Read-only, gegen ein lokales Postgres 16 mit allen drei synthetischen Fällen geprüft —
sie trennt die drei Gruppen korrekt.

```sql
SELECT c.platform,
       CASE WHEN a.created_at < '2026-06-10' THEN '1_vor_ingest_fix'
            WHEN a.created_at < '2026-07-13' THEN '2_vor_reuse_fix'
            ELSE '3_nach_beiden_fixes' END AS zeitraum,
       COALESCE(a.visual_evidence_status, '(kein Ingest-Versuch)') AS ingest_ergebnis,
       count(*) AS assets
FROM creative_radar.asset a
JOIN creative_radar.post p ON p.id = a.post_id
JOIN creative_radar.channel c ON c.id = p.channel_id
WHERE a.visual_analysis_status IN ('fetch_failed','no_source','image_unreachable','image_invalid')
GROUP BY 1, 2, 3
ORDER BY 1, 2, 3;
```

**Wie zu lesen:**

- Zeile `zeitraum = 1_vor_ingest_fix` → Fall A, unrettbarer Altbestand. Erwartungsgemäß
  der größte Block, wenn die 1.005 vor allem historisch sind.
- Zeile `zeitraum = 3_nach_beiden_fixes` mit `ingest_ergebnis = captured` → **Fall C**.
  Jede Zeile hier ist eine Anomalie, die es nach dem 13.07.-Fix nicht mehr geben dürfte.
  Genau diese Zahl entscheidet, ob es noch etwas zu programmieren gibt.
- Zeile `zeitraum = 3_nach_beiden_fixes` mit `ingest_ergebnis = fetch_failed` (oder
  `no_source`) → Fall B, zu klären mit Abschnitt 4.

## 4. Für Fall B: sind die Posts wirklich weg, oder ist es die Extraktion?

Zweite Query, falls Abschnitt 3 nennenswert viele Fall-B-Zeilen zeigt. Prüft stichprobenartig, ob die im Post gespeicherte Bild-URL überhaupt plausibel aussieht — eine leere oder offensichtlich falsche URL deutet auf einen Extraktions-Bug hin, eine plausibel aussehende tote URL auf einen gelöschten Post.

```sql
SELECT c.platform, p.raw_payload -> 'displayUrl' AS ig_url,
       p.raw_payload -> 'videoMeta' -> 'coverUrl' AS tt_cover,
       a.visual_evidence_status, a.created_at
FROM creative_radar.asset a
JOIN creative_radar.post p ON p.id = a.post_id
JOIN creative_radar.channel c ON c.id = p.channel_id
WHERE a.visual_evidence_status IN ('fetch_failed', 'no_source')
  AND a.created_at >= '2026-07-13'
ORDER BY a.created_at DESC
LIMIT 20;
```

Zwanzig Beispiele reichen, um von Hand zu beurteilen: leere/fehlende Felder → Extraktion;
gefüllte, aber tote URLs → Post-Verlust (nicht reparierbar, nur zu dokumentieren).

## 5. Nächster Schritt

Sobald Abschnitt 3 gelaufen ist, entscheidet die Verteilung über das weitere Vorgehen:

- **Fall A dominiert:** die 1.005 sind größtenteils historisch. Die Abdeckung von
  ~76 % auf ~90 % zu heben bedeutet dann in der Praxis: für den 90-Tage-Report-Zeitraum
  ist das Problem ohnehin fast erledigt (Fall-A-Assets fallen mit der Zeit aus dem
  Fenster), und es bleibt bei der Doku dieser Erkenntnis statt einem Code-Fix.
- **Fall C tritt auf:** dann lohnt sich ein gezielter Test, der den
  Wiederverwendungs-Pfad in `analyze_asset_visual` nachstellt und zeigt, unter welcher
  Bedingung er nicht greift.
- **Fall B dominiert:** dann klärt Abschnitt 4, ob eine Extraktions-Lücke im
  `apify_connector` steckt (reparierbar) oder die Posts schlicht weg sind (nicht
  reparierbar, nur zu beziffern).

Ohne diese Zahlen wäre jeder Fix ein Schuss ins Blaue gegen ein System, das zwei
frühere Schüsse schon verdaut hat.
