const DEFAULT_API_BASE = 'https://creative-radar-production.up.railway.app';

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

// Whitelisted host suffixes for the image proxy (must match the backend
// IMAGE_PROXY_ALLOWED_HOSTS default in app/config.py). Other URLs are returned
// unchanged so the browser can try them direct — useful for storage paths or
// hosts the proxy isn't authorised to fetch.
const PROXY_HOST_SUFFIXES = [
  'cdninstagram.com',
  'fbcdn.net',
  'tiktokcdn.com',
  'tiktokcdn-us.com',
  'tiktokcdn-eu.com',
];

export function proxyImageUrl(url) {
  if (!url || typeof url !== 'string') return url;
  if (!/^https?:\/\//i.test(url)) return url;
  let host;
  try {
    host = new URL(url).host.toLowerCase();
  } catch (_) {
    return url;
  }
  const allowed = PROXY_HOST_SUFFIXES.some((suffix) => host === suffix || host.endsWith('.' + suffix));
  if (!allowed) return url;
  return `${API_BASE}/api/img?url=${encodeURIComponent(url)}`;
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

export async function api(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  });
  return parseJsonResponse(response);
}

export async function upload(path, formData) {
  const response = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
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
  insightsOverview: () => api('/api/insights/overview'),
  assets: () => api('/api/assets'),
  reviewAsset: (id, payload) => api(`/api/assets/${id}/review`, { method: 'PATCH', body: JSON.stringify(payload) }),
  analyzeAssetVisual: (id) => api(`/api/assets/${id}/analyze-visual`, { method: 'POST' }),
  analyzeVisualBatch: (limit = 10) => api(`/api/assets/analyze-visual-batch?limit=${limit}`, { method: 'POST' }),
  reports: () => api('/api/reports'),
  latestReport: () => api('/api/reports/latest'),
  generateReport: (payload) => api('/api/reports/generate-weekly', { method: 'POST', body: JSON.stringify(payload) }),
  suggestReport: (payload) => api('/api/reports/suggest', { method: 'POST', body: JSON.stringify(payload) }),
  generateSuggestedReport: (payload) => api('/api/reports/generate', { method: 'POST', body: JSON.stringify(payload) }),
};
