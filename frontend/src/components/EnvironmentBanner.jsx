import React, { useEffect } from 'react';

// Staging-Briefing 2026-08-06, Abschnitt 4 Schritt 5: unuebersehbare
// Umgebungs-Kennung, damit Screenshots und Tests nie mit Prod verwechselt
// werden. Gesteuert ueber VITE_ENVIRONMENT (Build-Zeit-Variable der
// jeweiligen Netlify-Site bzw. frontend/.env.development lokal):
//   - fehlt / 'production'  -> rendert nichts (Prod bleibt unveraendert)
//   - 'staging'             -> gelber Balken + runtime noindex-Meta
//   - 'development'         -> grauer Balken (lokaler Vite-Dev-Server)
//
// Das noindex-Meta ist der Runtime-Guertel zum Build-Zeit-Hosenträger
// (scripts/staging-headers.mjs erzeugt X-Robots-Tag via _headers) — die
// Staging-Site soll auch dann unauffindbar bleiben, wenn der Header-Schritt
// im Site-Build-Command fehlt.
const ENVIRONMENT = (import.meta.env.VITE_ENVIRONMENT || 'production').toLowerCase();

export function EnvironmentBanner() {
  const isStaging = ENVIRONMENT === 'staging';
  const isDev = ENVIRONMENT === 'development';

  useEffect(() => {
    if (!isStaging) return;
    if (document.querySelector('meta[name="robots"]')) return;
    const meta = document.createElement('meta');
    meta.name = 'robots';
    meta.content = 'noindex, nofollow';
    document.head.appendChild(meta);
  }, [isStaging]);

  if (!isStaging && !isDev) return null;

  return (
    <div className={isStaging ? 'env-banner env-banner-staging' : 'env-banner env-banner-dev'} role="status">
      {isStaging
        ? 'STAGING — Testdaten, nicht die produktive Umgebung'
        : 'DEV — lokale Entwicklungsumgebung'}
    </div>
  );
}
