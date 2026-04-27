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

export async function api(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  });

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

export const endpoints = {
  health: () => api('/api/health'),
  channels: () => api('/api/channels'),
  seedChannels: () => api('/api/channels/seed-mvp', { method: 'POST' }),
  titles: () => api('/api/titles'),
  seedTitles: () => api('/api/titles/seed-mvp', { method: 'POST' }),
  posts: () => api('/api/posts'),
  manualImport: (payload) => api('/api/posts/manual-import', { method: 'POST', body: JSON.stringify(payload) }),
  analyzeInstagramLink: (payload) => api('/api/posts/analyze-instagram-link', { method: 'POST', body: JSON.stringify(payload) }),
  assets: () => api('/api/assets'),
  reviewAsset: (id, payload) => api(`/api/assets/${id}/review`, { method: 'PATCH', body: JSON.stringify(payload) }),
  reports: () => api('/api/reports'),
  latestReport: () => api('/api/reports/latest'),
  generateReport: (payload) => api('/api/reports/generate-weekly', { method: 'POST', body: JSON.stringify(payload) }),
};
