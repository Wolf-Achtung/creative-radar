const API_BASE = (import.meta.env.VITE_API_BASE || '').replace(/\/$/, '');

if (!API_BASE) {
  console.warn('VITE_API_BASE fehlt. In Netlify unter Site configuration > Environment variables setzen.');
}

export async function api(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `API error ${response.status}`);
  }
  return response.json();
}

export const endpoints = {
  health: () => api('/api/health'),
  channels: () => api('/api/channels'),
  seedChannels: () => api('/api/channels/seed-mvp', { method: 'POST' }),
  titles: () => api('/api/titles'),
  seedTitles: () => api('/api/titles/seed-mvp', { method: 'POST' }),
  posts: () => api('/api/posts'),
  manualImport: (payload) => api('/api/posts/manual-import', { method: 'POST', body: JSON.stringify(payload) }),
  assets: () => api('/api/assets'),
  reviewAsset: (id, payload) => api(`/api/assets/${id}/review`, { method: 'PATCH', body: JSON.stringify(payload) }),
  reports: () => api('/api/reports'),
  latestReport: () => api('/api/reports/latest'),
  generateReport: (payload) => api('/api/reports/generate-weekly', { method: 'POST', body: JSON.stringify(payload) }),
};
