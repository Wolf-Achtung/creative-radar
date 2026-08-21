import React, { useEffect, useMemo, useRef, useState } from 'react';
import { endpoints } from './api/client';
import { normalizeHandle } from './format';
import { neuesterLauf, warteAufTitelSyncEnde } from './titelSyncWarten';
import { ASSET_PAGE_SIZE } from './constants';
import { MonitoringPanel } from './panels/MonitoringPanel';
import { ReportsPanel } from './panels/ReportsPanel';
import { ReviewPanel } from './panels/ReviewPanel';
import { SourcesPanel } from './panels/SourcesPanel';
import { UsersPanel } from './panels/UsersPanel';

// Sprint User-Login 2026-07: fuenfter Tab "Nutzer" — Verwaltung der
// Login-Allowlist (siehe UsersPanel.jsx); die Nutzungs-Auswertung
// dazu lebt im Monitoring-Tab.
const NAV_ITEMS = ['Report erstellen', 'Treffer prüfen', 'Quellen', 'Nutzer', 'Monitoring'];

// Sprint 28.05.2026 (Admin-Sektion): die Tools-Logik aus dem bisherigen
// App() in eine eigene AdminApp-Komponente herausgezogen + exportiert.
// AdminPage.jsx wraps sie mit einem Login-Gate. Die Startseite (App()
// in App.jsx) zeigt nur noch Pair-Tiles + RoundupBlock; der Werkzeug-
// Block + DE/US-Vergleich + "Waehle ein Werkzeug…"-Empty-State sind
// ersatzlos entfernt.
export function AdminApp({ onLogout }) {
  const [health, setHealth] = useState(null);
  const [channels, setChannels] = useState([]);
  const [titles, setTitles] = useState([]);
  const [assets, setAssets] = useState([]);
  // V3 Perf-Fix: 'candidates' (Default, nur OPEN-Candidate-Assets) | 'all'
  // (paginiert). Verhindert den 4163-Karten-Freeze.
  const [assetMode, setAssetMode] = useState('candidates');
  const [assetOffset, setAssetOffset] = useState(0);
  const [assetsHasMore, setAssetsHasMore] = useState(false);
  const [report, setReport] = useState(null);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  // UX-Audit Befund 3 (2026-07-14): vorher ``null`` — der Admin startete
  // mit leerem Inhaltsbereich, bis der Nutzer einen Tab entdeckte.
  // "Treffer prüfen" als Default ist die Hypothese für den häufigsten
  // wiederkehrenden Arbeitsschritt (per Nutzungsdaten zu validieren).
  const [activeTab, setActiveTab] = useState('Treffer prüfen');
  const [channelFile, setChannelFile] = useState(null);
  const [monitorForm, setMonitorForm] = useState({ max_channels: 5, results_limit_per_channel: 5, only_whitelist_matches: false });
  const [tiktokForm, setTiktokForm] = useState({ username: 'warnerbros', max_channels: 1, results_limit_per_channel: 5, only_whitelist_matches: false });
  const [filters, setFilters] = useState({ status: 'all', platform: 'all', market: 'all', query: '' });
  const [recentlyCreatedByAssetId, setRecentlyCreatedByAssetId] = useState({});
  const [titleCandidates, setTitleCandidates] = useState([]);
  const [whitelistStats, setWhitelistStats] = useState(null);
  const [reportSuggestion, setReportSuggestion] = useState(null);
  const [reportForm, setReportForm] = useState({ report_type: 'weekly_overview', date_range: '30d', market: 'all', channel: 'all', limit: 10 });
  // "Jetzt komplett aktualisieren": eigener lokaler State, NICHT das globale
  // ``busy`` — der Lauf ist ein mehrminütiger BackgroundTask, ``busy`` würde
  // sonst die ganze Admin-UI für die Dauer sperren.
  const [cronBusy, setCronBusy] = useState(false);
  const [cronMessage, setCronMessage] = useState('');
  const cronPollRef = useRef(null);
  // Read-only "letzter Lauf"-Status (Diagnose-Folge 2026-07-06) — unabhaengig
  // vom Trigger-Button, damit man den Stand pruefen kann ohne einen neuen
  // Lauf anzustossen.
  const [lastCronRun, setLastCronRun] = useState(null);

  const sortedTitles = useMemo(() => {
    const map = new Map();
    [...titles].forEach((title) => {
      const key = (title.title_original || "").trim().toLowerCase();
      if (!map.has(key) || (!map.get(key).tmdb_id && title.tmdb_id)) map.set(key, title);
    });
    return [...map.values()].sort((a, b) => a.title_original.localeCompare(b.title_original));
  }, [titles]);
  // Wartung 20.08.2026 — hier standen fuenf abgeleitete Kennzahlen
  // (``approved``, ``highlights``, ``openReview``, ``missingTitles``,
  // ``reportCandidates``). Keine davon wurde je gerendert: sie sind beim
  // Aufteilen der alten Admin-Datei in AdminApp.jsx mitgewandert, die
  // Kopfzeile, die sie anzeigte, nicht. Fuenf ``assets.filter()`` ueber
  // den vollen Bestand bei JEDEM Render, ohne Empfaenger.
  //
  // Entfernt statt mit ``_`` stillgelegt: sie sind nicht "absichtlich
  // ungenutzt", sie sind Rest. Kommt die Kennzahlen-Zeile zurueck, steht
  // die Berechnung in der Historie (git log -S approved -- diese Datei).

  // Candidate-Review: OPEN-Candidates pro asset_id (erster gewinnt). Speist
  // sich aus der bereits geladenen ``titleCandidates``-Liste — kein Extra-
  // Fetch; nach Resolve/Ignore + ``load()`` faellt der Candidate aus dem
  // ``open``-Filter und der Vorschlag verschwindet.
  const openCandidatesByAssetId = useMemo(() => {
    const map = {};
    for (const candidate of titleCandidates || []) {
      if (candidate?.status === 'open' && candidate.asset_id && !map[candidate.asset_id]) {
        map[candidate.asset_id] = candidate;
      }
    }
    return map;
  }, [titleCandidates]);

  const visibleAssets = useMemo(() => {
    const query = filters.query.trim().toLowerCase();
    return assets.filter((asset) => {
      const platform = asset.platform || asset.channel_platform || asset.media_type;
      if (filters.status !== 'all' && asset.review_status !== filters.status) return false;
      if (filters.platform !== 'all' && platform !== filters.platform) return false;
      if (filters.market !== 'all' && asset.channel_market !== filters.market) return false;
      if (!query) return true;
      return [asset.title_name, asset.placement_title_text, asset.channel_name, asset.asset_type, asset.ai_summary_de, asset.ai_trend_notes, asset.caption, asset.ocr_text, asset.kinetic_text]
        .filter(Boolean).join(' ').toLowerCase().includes(query);
    });
  }, [assets, filters]);

  async function run(fn) {
    setBusy(true); setError(''); setMessage('');
    try { await fn(); } catch (err) { setError(err.message || String(err)); } finally { setBusy(false); }
  }

  // V3 Perf-Fix: Asset-Fetch je Modus. 'candidates' lädt nur die OPEN-
  // Candidate-Assets (~Dutzend) → kein Freeze; 'all' lädt die erste 50er-Seite.
  function fetchAssetsForMode(mode) {
    return mode === 'candidates'
      ? endpoints.assets({ candidate_queue: true })
      : endpoints.assets({ limit: ASSET_PAGE_SIZE, offset: 0 });
  }

  async function load() {
    const [h, c, t, a, stats, candidates, cronRuns] = await Promise.all([endpoints.health(), endpoints.channels(), endpoints.titles(), fetchAssetsForMode(assetMode), endpoints.titleWhitelistStats().catch(() => null), endpoints.titleCandidates().catch(() => []), endpoints.cronRuns(1).catch(() => null)]);
    setHealth(h); setChannels(c); setTitles(t); setAssets(a); setWhitelistStats(stats); setTitleCandidates(candidates);
    setLastCronRun(Array.isArray(cronRuns) ? (cronRuns[0] || null) : null);
    setAssetOffset(0);
    setAssetsHasMore(assetMode === 'all' && a.length === ASSET_PAGE_SIZE);
    try { setReport(await endpoints.latestReport()); } catch (_) { setReport(null); }
  }

  // Wartung 20.08.2026 — ``set-state-in-effect`` bewusst abgeschaltet,
  // gleiche Begruendung wie an den vier Stellen in InsightWeekly.jsx:
  // die Regel zielt auf abgeleiteten Zustand (ein Prop in State
  // spiegeln). Der ist in dieser Runde dort behoben, wo er vorlag
  // (``CollapsibleCard``, ``ImagePreview``). Hier liegt der andere Fall
  // vor: ``run()`` setzt synchron ``busy`` auf true, BEVOR geladen wird,
  // damit der Nutzer sofort "Arbeite gerade …" sieht. Das in den Render
  // zu ziehen ginge nur mit einer Fetch-Bibliothek — eine
  // Abhaengigkeits-Entscheidung, kein Lint-Fix. Die Regel bleibt fuer
  // alle anderen Stellen scharf.
  //
  // ``load`` fehlt bewusst in den Deps: die Funktion wird bei jedem
  // Render neu erzeugt, ein Eintrag wuerde den Abruf in eine Schleife
  // schicken.
  // eslint-disable-next-line react-hooks/set-state-in-effect, react-hooks/exhaustive-deps
  useEffect(() => { run(load); }, []);

  // Modus wechseln (Default-Kandidaten ↔ Alle paginiert) — lädt die Assets neu.
  async function switchAssetMode(mode) {
    await run(async () => {
      const a = await fetchAssetsForMode(mode);
      setAssets(a);
      setAssetMode(mode);
      setAssetOffset(0);
      setAssetsHasMore(mode === 'all' && a.length === ASSET_PAGE_SIZE);
    });
  }

  // "Mehr laden" im 'all'-Modus: nächste 50er-Seite anhängen.
  async function loadMoreAssets() {
    await run(async () => {
      const nextOffset = assetOffset + ASSET_PAGE_SIZE;
      const more = await endpoints.assets({ limit: ASSET_PAGE_SIZE, offset: nextOffset });
      setAssets((prev) => [...prev, ...more]);
      setAssetOffset(nextOffset);
      setAssetsHasMore(more.length === ASSET_PAGE_SIZE);
    });
  }

  async function importChannelFile(event) {
    event.preventDefault();
    if (!channelFile) return;
    await run(async () => {
      const result = await endpoints.importChannelsExcel(channelFile);
      await endpoints.seedTitles();
      await load();
      setChannelFile(null);
      setMessage(`Kanalliste importiert: ${result.created} neu, ${result.updated} aktualisiert.`);
    });
  }

  async function runApifyMonitor() {
    await run(async () => {
      const result = await endpoints.runApifyMonitor({
        channel_ids: [],
        max_channels: Number(monitorForm.max_channels) || 5,
        results_limit_per_channel: Number(monitorForm.results_limit_per_channel) || 5,
        only_whitelist_matches: Boolean(monitorForm.only_whitelist_matches),
      });
      await load();
      setMessage(`Instagram: ${result.raw_items} Roh-Treffer, ${result.created_assets} neue Treffer.`);
    });
  }

  async function runTikTokMonitor() {
    await run(async () => {
      const username = normalizeHandle(tiktokForm.username);
      if (username) {
        await endpoints.createChannel({
          name: `TikTok @${username}`,
          platform: 'tiktok',
          url: `https://www.tiktok.com/@${username}`,
          handle: username,
          market: 'US',
          channel_type: 'Studio/Verleih',
          priority: 'A',
          active: true,
          mvp: true,
          notes: 'Automatisch angelegt.',
        }).catch(() => null);
      }
      const result = await endpoints.runTikTokMonitor({
        usernames: username ? [username] : [],
        max_channels: Number(tiktokForm.max_channels) || 1,
        results_limit_per_channel: Number(tiktokForm.results_limit_per_channel) || 5,
        only_whitelist_matches: Boolean(tiktokForm.only_whitelist_matches),
      });
      await load();
      setMessage(`TikTok: ${result.raw_items} Roh-Treffer, ${result.created_assets} neue Treffer.`);
    });
  }

  async function analyzeAssetVisual(asset) {
    await run(async () => {
      await endpoints.analyzeAssetVisual(asset.id);
      await load();
      setMessage('Bild-/Text-Prüfung aktualisiert.');
    });
  }

  async function reviewAsset(asset, status) {
    await run(async () => {
      await endpoints.reviewAsset(asset.id, {
        review_status: status,
        include_in_report: status === 'approved' || status === 'highlight',
        is_highlight: status === 'highlight',
        title_id: asset.title_id || null,
        curator_note: asset.curator_note || '',
      });
      await load();
      setMessage('Trefferstatus wurde aktualisiert.');
    });
  }

  async function assignTitle(asset, titleId) {
    await run(async () => {
      setRecentlyCreatedByAssetId((current) => {
        if (!current[asset.id]) return current;
        const next = { ...current };
        delete next[asset.id];
        return next;
      });
      await endpoints.reviewAsset(asset.id, {
        review_status: asset.review_status,
        include_in_report: asset.include_in_report,
        is_highlight: asset.is_highlight,
        title_id: titleId || null,
        curator_note: asset.curator_note || '',
      });
      await load();
      setMessage(titleId ? 'Filmtitel wurde zugeordnet.' : 'Filmtitel-Zuordnung wurde entfernt.');
    });
  }

  async function reportMissingTitle(asset, hint) {
    await run(async () => {
      await endpoints.createTitleCandidateFromAsset(asset.id, { suggested_title: hint?.label || null });
      await load();
      setMessage('Titelkandidat wurde zur Prüfung vorgemerkt.');
    });
  }

  // Candidate-Review BESTÄTIGEN: nutzt die bestehende ``assignTitle``-Logik
  // (setzt title_id + de_us_match_key via PATCH /assets/{id}/review) UND
  // schliesst zusaetzlich den Candidate (status=resolved). title_id wird nur
  // auf explizite Bestaetigung mit einem exakt aufgeloesten Title gesetzt.
  async function confirmCandidate(asset, candidate, matchedTitle) {
    if (!matchedTitle || !candidate) return;
    await assignTitle(asset, matchedTitle.id);
    await run(async () => {
      await endpoints.patchTitleCandidate(candidate.id, { status: 'resolved' });
      await load();
      setMessage(`„${matchedTitle.title_original}" zugeordnet, Kandidat geschlossen.`);
    });
  }

  // Entscheidungs-Queue 21.08.2026: der haeufigste Fall in der Queue ist
  // „Titel fehlt in der Titelliste" (die KI-Notizen belegen das) — dafuer
  // gab es keinen direkten Weg. Legt den Titel manuell an (Quelle:
  // Kandidaten-Queue), ordnet das Asset ueber denselben Review-PATCH zu
  // wie der manuelle Klick (setzt auch den de_us_match_key) und
  // schliesst den Kandidaten.
  async function createTitleFromCandidate(asset, candidate, titleName) {
    const name = (titleName || '').trim();
    if (!name || !candidate) return;
    await run(async () => {
      const title = await endpoints.createTitle({
        title_original: name,
        notes: 'Aus der Vorschlags-Queue angelegt.',
      });
      await endpoints.reviewAsset(asset.id, {
        review_status: asset.review_status,
        include_in_report: asset.include_in_report,
        is_highlight: asset.is_highlight,
        title_id: title.id,
        curator_note: asset.curator_note || '',
      });
      await endpoints.patchTitleCandidate(candidate.id, { status: 'resolved' });
      setRecentlyCreatedByAssetId((current) => ({ ...current, [asset.id]: name }));
      await load();
      setMessage(`„${name}" neu angelegt und zugeordnet, Vorschlag geschlossen.`);
    });
  }

  // Candidate-Review VERWERFEN: nur den Candidate auf ``ignored`` setzen.
  // Das Asset bleibt unassigned — die normale Zuordnung bleibt moeglich.
  async function dismissCandidate(candidate) {
    if (!candidate) return;
    await run(async () => {
      await endpoints.patchTitleCandidate(candidate.id, { status: 'ignored' });
      await load();
      setMessage('Titel-Vorschlag verworfen. Asset bleibt offen.');
    });
  }

  // Hintergrund-Umbau 21.08.2026: der Sync antwortet sofort mit 202 und
  // laeuft server-seitig (inkl. Rematch) — vorher wartete der Fetch
  // minutenlang und starb mit "Failed to fetch", obwohl der Sync lief.
  // Bewusst NICHT in run(): das Polling dauert Minuten und soll die
  // uebrigen Admin-Buttons nicht so lange sperren; Doppelstarts faengt
  // der 409-Schutz des Endpoints.
  async function syncTitleSources() {
    setError(''); setMessage('');
    let vorherigeLaufId = null;
    try {
      const vorheriger = await neuesterLauf(endpoints.titleSyncRuns).catch(() => null);
      vorherigeLaufId = vorheriger ? vorheriger.id : null;
      await endpoints.syncTmdbTitles({ markets: ['DE', 'US'] });
    } catch (err) {
      setError(err.message || String(err));
      return;
    }
    setMessage('Titel-Sync läuft im Hintergrund (einige Minuten). Diese Meldung aktualisiert sich, sobald er fertig ist — die Seite kann offen bleiben.');
    const lauf = await warteAufTitelSyncEnde(endpoints.titleSyncRuns, vorherigeLaufId);
    if (!lauf) {
      setMessage('Titel-Sync läuft noch — Status später unter „Letzter Sync" prüfen.');
      return;
    }
    if (lauf.status === 'success') {
      await load();
      setMessage(`Titelquellen aktualisiert: ${lauf.upserted_count} übernommen, ${lauf.deduped_count} dedupliziert. Die Treffer-Neuzuordnung lief direkt im Anschluss.`);
    } else {
      setError(`Titel-Sync fehlgeschlagen: ${lauf.error_message || lauf.status}`);
    }
  }

  async function rematchAssets() {
    await run(async () => {
      const result = await endpoints.rematchAssets();
      await load();
      setMessage(`Rematch abgeschlossen: ${result.checked} geprüft, ${result.auto_matched} auto-zugeordnet, ${result.candidates_created} Kandidaten, ${result.still_unmatched} offen.`);
    });
  }

  // Kandidaten-Autopilot (2026-07-20): Backlog-Abbau on demand — dieselbe
  // Logik läuft wöchentlich im Cron nach dem Rematch.
  async function runCandidateAutopilot() {
    await run(async () => {
      const result = await endpoints.candidatesAutopilot();
      await load();
      setMessage(
        `Autopilot: ${result.auto_assigned} Vorschläge automatisch bestätigt, `
        + `${result.resolved_already_assigned} bereits zugeordnete geschlossen, `
        + `${result.ignored_stale} alte schwache Vorschläge aussortiert. `
        + `Offen bleiben: ${result.skipped_no_exact_match} ohne Exakt-Treffer, `
        + `${result.skipped_low_confidence} unter der Sicherheits-Schwelle, `
        + `${result.skipped_ambiguous} mehrdeutig.`
      );
    });
  }

  // Kandidaten-LLM-Assist (21.08.2026): löst die Rest-Kandidaten OHNE
  // Exakt-Treffer per KI auf — Batch von 12 je Klick (~30 s), die
  // Meldung sagt, wie viele noch offen sind.
  async function runCandidateLlmAssist() {
    await run(async () => {
      const r = await endpoints.candidatesLlmAssist();
      await load();
      if (r.skipped === 'anthropic_not_configured') {
        setMessage('KI-Prüfung übersprungen: kein Anthropic-API-Key in dieser Umgebung.');
        return;
      }
      const rest = r.offen_danach
        ? ` Noch ${r.offen_danach} ungeprüft — für die nächste Runde einfach erneut klicken.`
        : ' Alle Rest-Vorschläge sind jetzt KI-geprüft — was übrig ist, ist echte Handarbeit (mit KI-Hinweis in „Treffer prüfen").';
      setMessage(
        `KI-Prüfung: ${r.geprueft} neu geprüft, ${r.zugeordnet} sicher zugeordnet, `
        + `${r.unsicher} zur Hand-Prüfung markiert.${rest}`
      );
    });
  }

  // Wir-Segment Schritt 1 (21.08.2026): Kanal als "vom eigenen Team
  // betreut" markieren — Grundlage der empfohlen→gemacht→gewirkt-
  // Auswertung im Monitoring. Optimistisch ohne run(): ein Checkbox-
  // Klick soll nicht die ganze Admin-UI sperren.
  async function toggleChannelOwn(channel) {
    const neu = !channel.is_own;
    setChannels((alte) => alte.map((c) => (c.id === channel.id ? { ...c, is_own: neu } : c)));
    try {
      await endpoints.updateChannel(channel.id, { is_own: neu });
    } catch (err) {
      setChannels((alte) => alte.map((c) => (c.id === channel.id ? { ...c, is_own: !neu } : c)));
      setError(err.message || String(err));
    }
  }

  // "Jetzt komplett aktualisieren": triggert den vollen Cron-Lauf (gerade
  // abgeschlossene KW + Force) und pollt dann GET /cron/runs, bis der
  // BackgroundTask fertig ist. Kein globales ``busy`` (sperrt sonst 5 Min die
  // UI), kein Kosten-Dialog. target_week='completed' (nicht 'current' — siehe
  // Incident 2026-07-13 in client.js), damit dieser Button als Recovery nach
  // einem fehlgeschlagenen Scheduled-Run sicher ist.
  function summarizeCronRun(runRow) {
    const s = runRow?.summary || {};
    const briefs = s.briefs || {};
    const rematch = s.rematch || {};
    // IG/TT melden ``created_assets`` (umbenannt), YouTube ``created`` —
    // beide Schlüssel berücksichtigen.
    const created = Object.values(s.platforms || {}).reduce(
      (sum, p) => sum + (Number(p?.created_assets ?? p?.created) || 0), 0,
    );
    return `Briefs: ${briefs.generated ?? 0} erzeugt`
      + `, ${briefs.skipped_cache_hit ?? 0} übersprungen, ${briefs.failed ?? 0} fehlgeschlagen`
      + ` · Treffer neu zugeordnet: ${rematch.auto_matched ?? 0}`
      + ` · Neue Posts: ${created}`;
  }

  function clearCronPoll() {
    if (cronPollRef.current) {
      clearInterval(cronPollRef.current);
      cronPollRef.current = null;
    }
  }

  async function triggerFullSync() {
    if (cronBusy) return;
    setError('');
    setCronMessage('');
    setCronBusy(true);
    try {
      await endpoints.cronSyncAll({ targetWeek: 'completed', force: true });
      setCronMessage('Lauf gestartet … (Scrape → Briefs, dauert einige Minuten)');
    } catch (err) {
      // 409: ein Lauf läuft bereits — kein Fehler, sondern Hinweis. Wir pollen
      // trotzdem weiter, damit der laufende Lauf sein Ergebnis hier anzeigt.
      const msg = err?.message || String(err);
      if (msg.includes('cron_run_already_running') || msg.includes('409')) {
        setCronMessage('Ein Lauf läuft bereits — warte auf Abschluss …');
      } else {
        setCronBusy(false);
        setError(msg);
        return;
      }
    }
    clearCronPoll();
    cronPollRef.current = setInterval(async () => {
      try {
        const runs = await endpoints.cronRuns(1);
        const latest = Array.isArray(runs) ? runs[0] : null;
        if (!latest || latest.status === 'running') return;
        clearCronPoll();
        setCronBusy(false);
        if (latest.status === 'completed') {
          setCronMessage(`Fertig ✓ — ${summarizeCronRun(latest)}`);
          await load();
        } else if (latest.status === 'budget_exceeded') {
          setCronMessage(`Abgebrochen (Budget): ${latest.summary?.reason || 'budget_exceeded'}`);
        } else {
          setCronMessage(`Lauf fehlgeschlagen: ${latest.error_message || latest.status}`);
        }
      } catch (err) {
        clearCronPoll();
        setCronBusy(false);
        setError(err?.message || String(err));
      }
    }, 8000);
  }

  // Poll-Interval beim Unmount aufräumen.
  useEffect(() => () => clearCronPoll(), []);

  async function generateReportSuggestion() {
    await run(async () => {
      const payload = {
        report_type: reportForm.report_type,
        date_range: reportForm.date_range,
        channels: reportForm.channel === "all" ? [] : [reportForm.channel],
        markets: reportForm.market === 'all' ? [] : [reportForm.market],
        limit: reportForm.limit,
      };
      const suggested = await endpoints.suggestReport(payload);
      setReportSuggestion(suggested);
      setMessage(suggested.selected > 0 ? 'Vorschlag erstellt. Bitte vorgeschlagene Assets prüfen und danach den Report erzeugen.' : 'Kein technischer Fehler. Es wurden nur keine passenden Treffer für diese Filter gefunden.');
    });
  }

  async function generateReportFromSuggestion() {
    await run(async () => {
      if (!reportSuggestion) return;
      const created = await endpoints.generateSuggestedReport({
        report_type: reportSuggestion.report_type,
        asset_ids: reportSuggestion.assets.map((a) => a.asset_id),
        date_range: reportForm.date_range,
      });
      setReport(created);
      setMessage('Report bereit. Die ausgewählten Treffer sind ausreichend geprüft und visuell dokumentiert.');
    });
  }
  async function relaxFiltersAndResuggest() {
    const nextForm = { ...reportForm, date_range: '30d', market: 'all', channel: 'all', limit: 10 };
    setReportForm(nextForm);
    await run(async () => {
      const suggested = await endpoints.suggestReport({ report_type: nextForm.report_type, date_range: nextForm.date_range, channels: [], markets: [], limit: 10 });
      setReportSuggestion(suggested);
    });
  }

  return (
    <main>
      <header className="hero" style={{ background: '#1f4d4d' }}>
        <div>
          <p className="eyebrow">Admin-Bereich</p>
          <h1>Creative Radar — Werkzeuge</h1>
          {/* UX-Audit Befund 10: alle vier Tabs nennen, Orthografie
              vereinheitlicht (prüfen mit Umlaut). */}
          <p>Treffer prüfen · Reports erstellen · Quellen verwalten · Monitoring.</p>
        </div>
        <div className="admin-header-actions">
          {/* UX-Audit Befund 5: einheitliches Rückweg-Label ("← zur
              Übersicht") auf allen Seiten. */}
          <a className="hero__back-link" href="/">← zur Übersicht</a>
          <button type="button" className="secondary admin-logout-btn" onClick={onLogout}>Abmelden</button>
        </div>
      </header>

      {error && <div className="error">{error}</div>}
      {message && <div className="success">{message}</div>}
      {busy && <div className="info">Arbeite gerade …</div>}

      {/* UX-Audit Befunde 4+7 (2026-07-14): kein Toggle-Verhalten mehr
          (Klick auf den aktiven Tab liess vorher das Panel auf einen
          leeren Zustand kollabieren — widerspricht dem erwarteten
          Tab-Pattern), plus ARIA-Tab-Semantik: role=tablist/tab und
          aria-selected, damit der aktive Zustand nicht nur ueber Farbe
          kommuniziert wird. Der Inline-Coral-Override entfaellt —
          .tabs button.active in styles.css ist die eine Quelle fuer den
          Aktiv-Stil. */}
      <nav className="tabs" role="tablist" aria-label="Admin-Werkzeuge">
        {NAV_ITEMS.map((tab) => (
          <button
            key={tab}
            role="tab"
            aria-selected={activeTab === tab}
            className={activeTab === tab ? 'active' : ''}
            onClick={() => setActiveTab(tab)}
          >
            {tab}
          </button>
        ))}
      </nav>

      {activeTab === 'Report erstellen' && (
        <ReportsPanel
          report={report}
          busy={busy}
          suggestion={reportSuggestion}
          form={reportForm}
          setForm={setReportForm}
          onSuggest={generateReportSuggestion}
          onGenerateSuggestedReport={generateReportFromSuggestion}
          onRelaxFiltersAndSuggest={relaxFiltersAndResuggest}
        />
      )}
      {activeTab === 'Treffer prüfen' && (
        <ReviewPanel
          assets={assets}
          titles={sortedTitles}
          visibleAssets={visibleAssets}
          filters={filters}
          setFilters={setFilters}
          busy={busy}
          onReview={reviewAsset}
          onAnalyzeVisual={analyzeAssetVisual}
          onAssignTitle={assignTitle}
          onReportMissingTitle={reportMissingTitle}
          recentlyCreatedByAssetId={recentlyCreatedByAssetId}
          openCandidatesByAssetId={openCandidatesByAssetId}
          onConfirmCandidate={confirmCandidate}
          onDismissCandidate={dismissCandidate}
          onCreateTitleFromCandidate={createTitleFromCandidate}
          assetMode={assetMode}
          assetsHasMore={assetsHasMore}
          onSwitchAssetMode={switchAssetMode}
          onLoadMoreAssets={loadMoreAssets}
        />
      )}
      {activeTab === 'Quellen' && (
        <SourcesPanel
          busy={busy}
          channelFile={channelFile}
          setChannelFile={setChannelFile}
          onImportChannelFile={importChannelFile}
          monitorForm={monitorForm}
          setMonitorForm={setMonitorForm}
          onInstagram={runApifyMonitor}
          tiktokForm={tiktokForm}
          setTiktokForm={setTiktokForm}
          onTikTok={runTikTokMonitor}
          whitelistStats={whitelistStats}
          titleCandidates={titleCandidates}
          onSyncTitleSources={syncTitleSources}
          onRematchAssets={rematchAssets}
          onCandidateAutopilot={runCandidateAutopilot}
          onCandidateLlmAssist={runCandidateLlmAssist}
          onToggleChannelOwn={toggleChannelOwn}
          channels={channels}
          onFullSync={triggerFullSync}
          cronBusy={cronBusy}
          cronMessage={cronMessage}
          lastCronRun={lastCronRun}
        />
      )}
      {activeTab === 'Nutzer' && <UsersPanel />}
      {activeTab === 'Monitoring' && <MonitoringPanel />}

      <footer className="footer-status">
        API: {health?.status || 'offen'} · Kanäle {channels.length} · Titel {titles.length} · Treffer {assets.length}
        <button className="secondary" onClick={() => run(load)} disabled={busy}>Aktualisieren</button>
      </footer>
    </main>
  );
}
