import { buildProxyImageUrl } from './imageUrl.js';

const DEFAULT_API_BASE = 'https://api.creative-radar.de';

function resolveApiBase() {
  // trim() vor replace(): bei "http://host/ " (Slash + Leerzeichen) wuerde
  // der Slash sonst stehen bleiben und Doppel-Slash-URLs erzeugen.
  const configured = (import.meta.env.VITE_API_BASE || '').trim().replace(/\/+$/, '');
  if (!configured) return DEFAULT_API_BASE;
  try {
    const url = new URL(configured.startsWith('http') ? configured : `https://${configured}`);
    if (typeof window !== 'undefined' && url.host === window.location.host) {
      return DEFAULT_API_BASE;
    }
    return `${url.protocol}//${url.host}`;
  } catch (_) {
    return DEFAULT_API_BASE;
  }
}

const API_BASE = resolveApiBase();

// Wartung 20.08.2026 — exportiert, damit ``<a href download>``-Links
// dieselbe Basis benutzen wie ``fetch``. Vorher verlinkte ReportsPanel
// relativ auf ``/api/reports/...`` und lief damit ueber die
// ``/api/*``-Weiterleitung in netlify.toml, die fest auf
// api.creative-radar.de zeigt: auf der STAGING-Seite luden die
// Report-Downloads den PRODUKTIONS-Report. Beide Endpunkte sind
// oeffentlich (``PUBLIC_PATH_EXACT`` in backend/app/auth.py), ein
// absoluter Link braucht also keinen Token.
export function apiUrl(path) {
  return `${API_BASE}${path}`;
}

export function proxyImageUrl(url) {
  return buildProxyImageUrl(url, { apiBase: API_BASE });
}

async function parseJsonResponse(response) {
  const contentType = response.headers.get('content-type') || '';
  const body = await response.text();

  if (!response.ok) {
    throw new Error(body || `API error ${response.status}`);
  }

  if (!contentType.includes('application/json')) {
    throw new Error(`API returned HTML/text instead of JSON. Genutzte API: ${API_BASE}. Preview: ${body.slice(0, 120)}`);
  }

  return body ? JSON.parse(body) : null;
}

// Bearer-token auth header (Phase 4 W4 Task 4.3).
// VITE_API_TOKEN is a build-time env var injected by Netlify. If it is missing
// or empty, we omit the header entirely — sending 'Bearer undefined' would
// fail every backend check once auth_enabled flips on.
//
// Security note: this token lives in the Frontend bundle and is therefore
// effectively public. It is anti-bot, not user auth. Phase-5 replaces with
// session cookies or OAuth.
function authHeaders() {
  const token = (import.meta.env.VITE_API_TOKEN || '').trim();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

// Sprint 28.05.2026 (Admin-Login): credentials: 'include' damit der
// Browser das HttpOnly-Session-Cookie auf Cross-Subdomain-Aufrufen
// (app.creative-radar.de → api.creative-radar.de) mitschickt. CORS-
// Setup im Backend hat allow_credentials=True. Routes ohne Session
// bleiben unbeeintraechtigt — der Browser sendet nur das Cookie, das
// fuer die Domain gesetzt ist (nach Login).
export async function api(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders(),
      ...(options.headers || {}),
    },
    ...options,
  });
  return parseJsonResponse(response);
}

// Datei-Download fuer geschuetzte Endpoints (2026-07-20, Nutzungs-
// Export). Ein einfacher <a href> wuerde am Bearer-Header scheitern
// (Link-Klicks koennen keine Authorization mitschicken) — daher fetch
// mit Bearer+Cookie, Blob, und programmatischer Download-Klick. Den
// Dateinamen liefert das Backend via Content-Disposition.
export async function download(path, fallbackName) {
  const response = await fetch(`${API_BASE}${path}`, {
    credentials: 'include',
    headers: { ...authHeaders() },
  });
  if (!response.ok) {
    throw new Error((await response.text()) || `API error ${response.status}`);
  }
  const blob = await response.blob();
  const disposition = response.headers.get('content-disposition') || '';
  const match = disposition.match(/filename="?([^";]+)"?/);
  const name = (match && match[1]) || fallbackName;
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = name;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

export async function upload(path, formData) {
  const response = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    credentials: 'include',
    headers: { ...authHeaders() },
    body: formData,
  });
  return parseJsonResponse(response);
}

export const endpoints = {
  health: () => api('/api/health'),
  dbHealth: () => api('/api/health/db'),
  channels: () => api('/api/channels'),
  createChannel: (payload) => api('/api/channels', { method: 'POST', body: JSON.stringify(payload) }),
  seedChannels: () => api('/api/channels/seed-mvp', { method: 'POST' }),
  importChannelsExcel: (file) => {
    const formData = new FormData();
    formData.append('file', file);
    return upload('/api/channels/import-excel', formData);
  },
  titles: () => api('/api/titles'),
  createTitle: (payload) => api('/api/titles', { method: 'POST', body: JSON.stringify(payload) }),
  seedTitles: () => api('/api/titles/seed-mvp', { method: 'POST' }),

  syncTmdbTitles: (payload) => api('/api/titles/sync/tmdb', { method: 'POST', body: JSON.stringify(payload) }),
  rematchAssets: () => api('/api/titles/rematch-assets', { method: 'POST' }),
  // Kandidaten-Autopilot (2026-07-20): bestätigt offene Titel-Vorschläge
  // mit eindeutigem Exakt-Treffer in der Whitelist, schließt Karteileichen.
  candidatesAutopilot: () => api('/api/titles/candidates/autopilot', { method: 'POST' }),
  candidatesLlmAssist: () => api('/api/titles/candidates/llm-assist', { method: 'POST' }),
  // Aufraeum-Knopf (31.08.2026): die Kandidaten-Pipeline in einem
  // Aufruf — Autopilot, KI-Pruefung, Katalog-Nachladen scharf.
  candidatesAufraeumen: () => api('/api/titles/candidates/aufraeumen', { method: 'POST' }),
  // Katalog-Nachladen (25.08.2026): legt Titel an, die ein Post
  // nachweislich bewirbt und die der Katalog nicht kennt. POST, und
  // hinter dem Bearer-Token — deshalb ist der Knopf der einzige Weg
  // dorthin, nicht die Browser-Adresszeile.
  katalogNachladen: (anwenden = false) => api(
    `/api/titles/katalog-nachladen?anwenden=${anwenden ? 'true' : 'false'}`,
    { method: 'POST' },
  ),
  // TMDb-Auswahl (31.08.2026): die menschliche Haelfte des Nachladens.
  // Der automatische Pfad laesst mehrdeutige Namen liegen — hier holt
  // die Queue-Karte alle exakten aktuellen Treffer zur Auswahl, und
  // tmdbAnlegen setzt den angeklickten in einem Schritt um (anlegen
  // bzw. per tmdb_id wiederverwenden, zuordnen, Kandidat schliessen).
  titlesTmdbAuswahl: (name) => api(`/api/titles/tmdb-auswahl?name=${encodeURIComponent(name)}`),
  titlesTmdbAnlegen: (payload) => api('/api/titles/tmdb-anlegen', { method: 'POST', body: JSON.stringify(payload) }),
  updateChannel: (channelId, payload) => api(`/api/channels/${channelId}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  updateTitle: (titleId, payload) => api(`/api/titles/${titleId}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  titleSyncRuns: () => api('/api/titles/sync/runs'),
  titleWhitelistStats: () => api('/api/titles/stats/whitelist'),
  titleCandidates: () => api('/api/titles/candidates'),
  createTitleCandidateFromAsset: (assetId, payload = {}) => api(`/api/titles/candidates/from-asset/${assetId}`, { method: 'POST', body: JSON.stringify(payload) }),
  patchTitleCandidate: (id, payload) => api(`/api/titles/candidates/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  posts: () => api('/api/posts'),
  manualImport: (payload) => api('/api/posts/manual-import', { method: 'POST', body: JSON.stringify(payload) }),
  analyzeInstagramLink: (payload) => api('/api/posts/analyze-instagram-link', { method: 'POST', body: JSON.stringify(payload) }),
  runApifyMonitor: (payload) => api('/api/monitor/apify-instagram', { method: 'POST', body: JSON.stringify(payload) }),
  runTikTokMonitor: (payload) => api('/api/monitor/apify-tiktok', { method: 'POST', body: JSON.stringify(payload) }),
  pairs: () => api('/api/pairs'),
  // Breakouts fuer die Startseite (Wolf 21.07.) — gleiche Berechnung wie
  // der Admin-Feed, aber hinter dem normalen User-Login statt der
  // Admin-Session. Rein lesend.
  breakoutsPublic: ({ limit = 10 } = {}) => api(`/api/breakouts?limit=${limit}`),
  // market ('DE'/'US'/'UK') grenzt die ganze Rechnung auf einen Markt
  // ein (Markt-Filter 26.08.); leer/undefined = alle Maerkte zusammen.
  insightPatterns: ({ windowDays = 90, market } = {}) => {
    const params = new URLSearchParams({ window_days: String(windowDays) });
    if (market) params.set('market', market);
    return api(`/api/insights/patterns?${params.toString()}`);
  },
  // Bewaehrung (26.08.): Trefferquote der eigenen Empfehlungen aus den
  // Wochen-Snapshots — wie viele over-Zellen einer Woche standen in der
  // Folgewoche noch? Rein lesend, markt-unabhaengig.
  insightPatternBewaehrung: () => api('/api/insights/patterns/bewaehrung'),
  // Beispiel-Posts einer Muster-Zelle (Aufwertung B): die staerksten
  // Posts hinter einem Befund, sortiert nach Lift. value kann Leer-
  // und Sonderzeichen tragen (Genres, Titel) — deshalb URLSearchParams.
  // market muss zum Filter der Muster-Anfrage passen, sonst zeigen die
  // Beispiele Posts, die in der Zelle gar nicht mitgezaehlt wurden.
  insightPatternExamples: ({ dimension, value, windowDays = 90, limit = 5, market } = {}) => {
    const params = new URLSearchParams({
      dimension, value, window_days: String(windowDays), limit: String(limit),
    });
    if (market) params.set('market', market);
    return api(`/api/insights/patterns/examples?${params.toString()}`);
  },
  // Referenz-Suche (Roadmap Schritt 1, 25.08.): Facetten-Suche ueber die
  // analysierten Posts. facetten ist ein Objekt dimension->wert und wird
  // als wiederholter facette=dimension=wert-Parameter uebertragen —
  // Werte koennen Leer- und Sonderzeichen tragen (Genres), deshalb
  // URLSearchParams.
  // Post-Check (Roadmap-Ausbau 25.08.): ein Entwurf wird VOR dem
  // Posten gegen die aktuellen Befunde geprueft. POST, weil die
  // Caption lang sein kann.
  postCheck: (entwurf) => api('/api/insights/post-check', { method: 'POST', body: JSON.stringify(entwurf) }),
  referenzSuche: ({ windowDays = 90, platform, facetten = {}, minLift, limit = 24 } = {}) => {
    const params = new URLSearchParams({ window_days: String(windowDays), limit: String(limit) });
    if (platform) params.set('platform', platform);
    if (minLift != null) params.set('min_lift', String(minLift));
    Object.entries(facetten).forEach(([dimension, wert]) => {
      if (wert) params.append('facette', `${dimension}=${wert}`);
    });
    return api(`/api/insights/referenzen?${params.toString()}`);
  },
  // Juengstes Pattern-Briefing (Text-Bausteine aus den Mustern, Stufe 1
  // Schritt 3). 404 = noch keins persistiert — der Block blendet die
  // Sektion dann aus, kein Fehlerzustand.
  insightPatternBriefing: ({ mode = 'genre' } = {}) => api(`/api/insights/pattern-briefing?mode=${mode}`),
  // Latest Segment-Roundup pro Segment. Public, kein Auth-Token noetig
  // (Backend-Whitelist analog /api/pairs). Treibt den Roundup-Block auf
  // der Landing-Page (Master-Plan-Schritt-3b, 26.05.).
  roundupsLatest: () => api('/api/roundups/latest'),
  insightsOverview: () => api('/api/insights/overview'),
  insightsWeekly: (pair, { windowDays = 30, dryRun = false } = {}) => {
    const params = new URLSearchParams({ pair, window_days: String(windowDays), dry_run: String(dryRun) });
    return api(`/api/insights/weekly?${params.toString()}`);
  },
  // V3 Sprint 2 — film-zentrierte Detailansicht. Posts eines Titels,
  // gruppiert nach Markt (DE/US/UK) und Plattform. Leere/unbekannte
  // title_id liefert wohlgeformte leere Gruppen, keinen Fehler.
  titlePosts: (titleId) => api(`/api/insights/title/${titleId}/posts`),
  // V3 Sprint 6 — deskriptive Markt-Zeitreihe über die Brief-Wochen eines
  // Pairs. Σ Views + aggregierte ER je ISO-Woche × Markt, lückenlose Achse.
  insightsTimeline: (pair, { weeks } = {}) => {
    const params = new URLSearchParams({ pair });
    if (weeks != null) params.set('weeks', String(weeks));
    return api(`/api/insights/timeline?${params.toString()}`);
  },
  // #252 — ER-Prognose pro Markt, öffentlich mit Ehrlichkeits-Gate: Märkte
  // ohne belastbaren Trend (R² < 0.5 oder n < 5) kommen als
  // status='too_volatile' OHNE Prognosewert zurück. POST bleibt die Methode:
  // ein Cache-Miss der Woche löst serverseitig genau einen LLM-Call für die
  // Einordnung aus, alle weiteren Aufrufe lesen den Cache.
  insightsForecast: (pair, { weeks } = {}) => {
    const params = new URLSearchParams({ pair });
    if (weeks != null) params.set('weeks', String(weeks));
    return api(`/api/insights/forecast?${params.toString()}`, { method: 'POST' });
  },
  // V3 Perf-Fix: candidate_queue=true lädt nur Assets mit OPEN-Candidate
  // (Default des "Treffer prüfen"-Tabs); limit/offset paginieren den
  // "Alle anzeigen"-Fall. Ohne Argumente: Backend-Default (limit 50).
  assets: ({ candidate_queue, limit, offset } = {}) => {
    const params = new URLSearchParams();
    if (candidate_queue) params.set('candidate_queue', 'true');
    if (limit != null) params.set('limit', String(limit));
    if (offset != null) params.set('offset', String(offset));
    const qs = params.toString();
    return api(`/api/assets${qs ? `?${qs}` : ''}`);
  },
  reviewAsset: (id, payload) => api(`/api/assets/${id}/review`, { method: 'PATCH', body: JSON.stringify(payload) }),
  analyzeAssetVisual: (id) => api(`/api/assets/${id}/analyze-visual`, { method: 'POST' }),
  reports: () => api('/api/reports'),
  latestReport: () => api('/api/reports/latest'),
  generateReport: (payload) => api('/api/reports/generate-weekly', { method: 'POST', body: JSON.stringify(payload) }),
  suggestReport: (payload) => api('/api/reports/suggest', { method: 'POST', body: JSON.stringify(payload) }),
  generateSuggestedReport: (payload) => api('/api/reports/generate', { method: 'POST', body: JSON.stringify(payload) }),

  // Sprint 28.05.2026 (Admin-Login). Login setzt das HttpOnly-Cookie
  // im Browser; nachfolgende Calls bringen es ueber credentials:'include'
  // mit. Logout cleared das Cookie. Me liefert {authenticated, auth_enabled}
  // und ist die einzige Route, die ohne Session 200 statt 401 sagt — das
  // Frontend kann beim Page-Load checken ohne Error-Toast.
  adminLogin: (password) => api('/api/admin/login', { method: 'POST', body: JSON.stringify({ password }) }),
  adminLogout: () => api('/api/admin/logout', { method: 'POST' }),
  // Playbook-Test-Mail (21.08.2026): sendet sofort an
  // PLAYBOOK_MAIL_RECIPIENTS, auch ohne TI-Flag — der Pruef-Weg vor
  // der Freigabe. Antwort traegt sent/failed bzw. den Skip-Grund.
  adminPlaybookMailTest: () => api('/api/admin/playbook-mail/test', { method: 'POST' }),
  // Text-Bausteine sofort generieren (21.08.2026): Review-Weg per
  // Klick statt Endpoint-URL. Kostet je Lauf einen echten Opus-Call
  // (~5-10 Cent); Leerlauf ohne Muster ist kostenfrei.
  // Juengstes persistiertes Briefing einer Ebene — die Review-Ansicht
  // im Admin, VOR der Flag-Freigabe (das Nutzer-Panel ist in Prod aus).
  adminWirSegment: () => api('/api/admin/wir-segment'),
  projektStartBrief: (titleId) => api(`/api/admin/projekt-start-brief/${titleId}`),
  kampagnenTiming: () => api('/api/admin/kampagnen-timing'),
  releaseCountdown: () => api('/api/admin/release-countdown'),
  beweisLoop: () => api('/api/admin/beweis-loop'),
  wochenPlan: () => api('/api/admin/wochen-plan'),
  soundTrends: () => api('/api/admin/sound-trends'),
  adminPatternBriefingLatest: ({ mode = 'genre' } = {}) => api(
    `/api/admin/pattern-briefing/latest?mode=${mode}`,
  ),
  adminPatternBriefingGenerate: ({ mode = 'genre', windowDays = 90 } = {}) => api(
    `/api/admin/pattern-briefing/generate?mode=${mode}&window_days=${windowDays}`,
    { method: 'POST' },
  ),
  adminMe: () => api('/api/admin/me'),

  // Sprint User-Login 2026-07: E-Mail+Code-Login fuer die gesamte
  // Website (Flow identisch zum Schwesterprojekt: 6-stelliger Code,
  // 10 Min. gueltig). Login setzt ein 30-Tage-HttpOnly-Cookie
  // (cr_user_session); /me antwortet IMMER 200 mit {authenticated,
  // email, auth_enabled} — bei auth_enabled=false (Rollout/lokal) gilt
  // "eingeloggt", das Gate bleibt unsichtbar.
  authRequestCode: (email) => api('/api/auth/request-code', { method: 'POST', body: JSON.stringify({ email }) }),
  authLogin: (email, code) => api('/api/auth/login', { method: 'POST', body: JSON.stringify({ email, code }) }),
  authLogout: () => api('/api/auth/logout', { method: 'POST' }),
  authMe: () => api('/api/auth/me'),

  // User-Verwaltung + Nutzungs-Auswertung (Admin-Session-geschuetzt).
  adminUsers: () => api('/api/admin/users'),
  adminCreateUser: (payload) => api('/api/admin/users', { method: 'POST', body: JSON.stringify(payload) }),
  adminPatchUser: (id, payload) => api(`/api/admin/users/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  adminDeleteUser: (id) => api(`/api/admin/users/${id}`, { method: 'DELETE' }),
  adminUsage: (days = 30) => api(`/api/admin/usage?days=${days}`),
  // Nutzungs-Bericht als weitergebbares Dokument (Wolf 2026-07-20):
  // HTML = formatierter Bericht (per Browser-Druck auch als PDF),
  // CSV = Roh-Events fuer eigene Auswertungen in Excel.
  adminUsageExportHtml: (days = 30) => download(`/api/admin/usage/export.html?days=${days}`, 'creative-radar-nutzung.html'),
  adminUsageExportCsv: (days = 30) => download(`/api/admin/usage/export.csv?days=${days}`, 'creative-radar-nutzung.csv'),
  // Drill-down pro Nutzer (aufklappbare Zeile in der Nutzungs-Ansicht).
  adminUsageUserEvents: (email, days = 30) => api(`/api/admin/usage/user-events?email=${encodeURIComponent(email)}&days=${days}`),

  // "Jetzt komplett aktualisieren" (Admin-Button): löst den vollen Cron-Lauf
  // on-demand aus. Default targetWeek='completed' + force=true → gerade
  // abgeschlossene KW mit Force-Overwrite (identisch zum wöchentlichen
  // GitHub-Action-Cron, nur eben force=true statt false). Antwort ist 202 mit
  // run_id; der Lauf ist ein mehrminütiger BackgroundTask, dessen Status über
  // cronRuns gepollt wird.
  //
  // Incident 2026-07-13: der Default war zuvor 'current' — nach einem
  // gescheiterten Montags-Cron hat ein manueller Klick auf diesen Button
  // damit die GERADE ERST BEGONNENE KW (Stunden statt Tage an Daten) unter
  // dem Cache-Key der Woche abgelegt. Der reguläre Cron (target_week=
  // completed) hat die Woche danach als "schon generiert" übersprungen und
  // konnte sie nie mehr mit vollständigen Daten überschreiben. 'completed'
  // als Default macht den Button sicher als Recovery-Pfad nach einem
  // fehlgeschlagenen Scheduled-Run.
  cronSyncAll: ({ targetWeek = 'completed', force = true } = {}) => {
    const params = new URLSearchParams({ target_week: targetWeek, force: String(force) });
    return api(`/api/admin/cron/sync-all?${params.toString()}`, { method: 'POST' });
  },
  cronRuns: (limit = 1) => api(`/api/admin/cron/runs?limit=${limit}`),

  // Platin 2 (Admin Ops-Dashboard) — reine Lesezugriffe auf bereits
  // bestehende Budget-/Kosten-Endpoints (F0.6/F0.7/Incident 2026-07-13),
  // die bisher nur per curl abfragbar waren. Kein neuer Backend-Code.
  costSummary: ({ fromDate, toDate, groupBy = 'provider' } = {}) => {
    const params = new URLSearchParams({ group_by: groupBy });
    if (fromDate) params.set('from_date', fromDate);
    if (toDate) params.set('to_date', toDate);
    return api(`/api/admin/cost-summary?${params.toString()}`);
  },
  apifyBudgetStatus: () => api('/api/admin/budget-status'),
  anthropicBudgetStatus: () => api('/api/admin/anthropic-budget-status'),
  openaiBudgetStatus: () => api('/api/admin/openai-budget-status'),

  // Platin 4 — Breakout-Feed über alle Pairs (rein lesend, kein LLM-Call).
  breakouts: ({ limit = 20 } = {}) => api(`/api/admin/breakouts?limit=${limit}`),
};
