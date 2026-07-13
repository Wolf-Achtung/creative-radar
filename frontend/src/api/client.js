import { buildProxyImageUrl } from './imageUrl.js';

const DEFAULT_API_BASE = 'https://api.creative-radar.de';

function resolveApiBase() {
  const configured = (import.meta.env.VITE_API_BASE || '').replace(/\/$/, '').trim();
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
  analyzeVisualBatch: (limit = 10) => api(`/api/assets/analyze-visual-batch?limit=${limit}`, { method: 'POST' }),
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
  adminMe: () => api('/api/admin/me'),

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
};
