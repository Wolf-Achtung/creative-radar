// Pure-Node tests for buildProxyImageUrl — no Vite/JSDOM needed. Run with:
//   node --test frontend/src/api/imageUrl.test.mjs
//
// Coverage matches Sprint 5.1 acceptance criteria:
//   1. Internal /storage/... path -> absolutised against apiBase
//   2. Allowlisted external CDN URL -> routed through /api/img proxy
//   3. Other absolute http(s) URL -> returned unchanged

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { buildProxyImageUrl, PROXY_HOST_SUFFIXES } from './imageUrl.js';

const API_BASE = 'https://api.creative-radar.de';

test('relative /storage/ path is prefixed with apiBase', () => {
  const result = buildProxyImageUrl('/storage/evidence/abc.jpg', { apiBase: API_BASE });
  assert.equal(result, 'https://api.creative-radar.de/storage/evidence/abc.jpg');
});

test('relative /storage/ path tolerates trailing slash on apiBase', () => {
  const result = buildProxyImageUrl('/storage/evidence/abc.jpg', {
    apiBase: 'https://api.creative-radar.de/',
  });
  assert.equal(result, 'https://api.creative-radar.de/storage/evidence/abc.jpg');
});

test('Instagram CDN URL is routed through /api/img proxy', () => {
  const upstream = 'https://scontent.cdninstagram.com/v/t51/abc.jpg';
  const result = buildProxyImageUrl(upstream, { apiBase: API_BASE });
  assert.equal(
    result,
    `${API_BASE}/api/img?url=${encodeURIComponent(upstream)}`,
  );
});

test('TikTok CDN URL is routed through /api/img proxy', () => {
  const upstream = 'https://p16-sign-va.tiktokcdn-us.com/obj/foo.jpeg';
  const result = buildProxyImageUrl(upstream, { apiBase: API_BASE });
  assert.equal(
    result,
    `${API_BASE}/api/img?url=${encodeURIComponent(upstream)}`,
  );
});

test('non-allowlisted absolute http(s) URL is returned unchanged', () => {
  const upstream = 'https://example.com/some/asset.jpg';
  assert.equal(
    buildProxyImageUrl(upstream, { apiBase: API_BASE }),
    upstream,
  );
});

test('YouTube thumbnail URL passes through unchanged (allowlist not yet extended)', () => {
  // Documents current behaviour ahead of Sprint 5.2.3, where the YouTube
  // connector lands and *.ytimg.com is added to PROXY_HOST_SUFFIXES.
  const upstream = 'https://i.ytimg.com/vi/abc123/hqdefault.jpg';
  assert.equal(
    buildProxyImageUrl(upstream, { apiBase: API_BASE }),
    upstream,
  );
});

test('empty / null / non-string input is returned unchanged', () => {
  assert.equal(buildProxyImageUrl('', { apiBase: API_BASE }), '');
  assert.equal(buildProxyImageUrl(null, { apiBase: API_BASE }), null);
  assert.equal(buildProxyImageUrl(undefined, { apiBase: API_BASE }), undefined);
  assert.equal(buildProxyImageUrl(42, { apiBase: API_BASE }), 42);
});

test('malformed http URL falls through to original value', () => {
  // The URL constructor throws on this — helper must not crash, must return input.
  const broken = 'http://';
  assert.equal(buildProxyImageUrl(broken, { apiBase: API_BASE }), broken);
});

test('PROXY_HOST_SUFFIXES export is non-empty (regression guard)', () => {
  assert.ok(Array.isArray(PROXY_HOST_SUFFIXES));
  assert.ok(PROXY_HOST_SUFFIXES.length > 0);
});
