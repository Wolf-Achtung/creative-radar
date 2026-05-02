// Pure URL-resolver for asset thumbnails — extracted from client.js so it can
// be unit-tested without booting Vite (see imageUrl.test.mjs).
//
// Two image-source classes funnel through here:
//
//  1. External CDN URLs (Instagram/TikTok). These block hotlinking from a
//     fresh origin, so we route them through the backend image proxy at
//     `${apiBase}/api/img?url=...`. The proxy's host-allowlist must mirror
//     PROXY_HOST_SUFFIXES below.
//
//  2. Internal storage paths (`/storage/<key>`). The backend serves these
//     from a StaticFiles mount on the API origin, NOT the SPA origin. A
//     bare `<img src="/storage/...">` would resolve against the page's host
//     (e.g. app.creative-radar.de), where Netlify's SPA catch-all returns
//     index.html instead of the image. We absolutise against `apiBase` so
//     the browser hits api.creative-radar.de/storage/... directly.
//
// All other strings — absolute http(s) URLs to non-allowlisted hosts, data:
// URIs, empty values — are returned unchanged. The caller's <img onError>
// handles the rest.

export const PROXY_HOST_SUFFIXES = [
  'cdninstagram.com',
  'fbcdn.net',
  'tiktokcdn.com',
  'tiktokcdn-us.com',
  'tiktokcdn-eu.com',
];

function stripTrailingSlash(value) {
  return typeof value === 'string' ? value.replace(/\/$/, '') : '';
}

export function buildProxyImageUrl(url, { apiBase, allowedHostSuffixes = PROXY_HOST_SUFFIXES } = {}) {
  if (!url || typeof url !== 'string') return url;
  const base = stripTrailingSlash(apiBase);

  // Internal storage path: absolutise against the API origin so the browser
  // bypasses the SPA host (where Netlify would serve index.html).
  if (url.startsWith('/storage/')) {
    return base ? `${base}${url}` : url;
  }

  if (!/^https?:\/\//i.test(url)) return url;

  let host;
  try {
    host = new URL(url).host.toLowerCase();
  } catch (_) {
    return url;
  }
  const allowed = allowedHostSuffixes.some(
    (suffix) => host === suffix || host.endsWith('.' + suffix),
  );
  if (!allowed) return url;
  return `${base}/api/img?url=${encodeURIComponent(url)}`;
}
