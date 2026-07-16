import React from 'react';

// UX-Audit Befunde 1+8 (2026-07-14) — globaler Footer als "Sicherheitsnetz"
// auf allen öffentlichen Seiten: Impressum + Datenschutz (Pflichtangaben
// für eine öffentlich erreichbare DE-Site, §5 DDG / DSGVO) plus ein
// dezenter Utility-Link zum Admin-Login, der vorher nur per URL-Eingabe
// erreichbar war. Die Inhalte der Rechtsseiten liefert Wolf nach —
// LegalPage.jsx trägt bis dahin einen klar markierten Platzhalter.
export function SiteFooter() {
  return (
    <footer className="site-footer">
      <nav aria-label="Rechtliches und Verwaltung">
        <a href="/impressum">Impressum</a>
        <span aria-hidden="true">·</span>
        <a href="/datenschutz">Datenschutz</a>
        <span aria-hidden="true">·</span>
        <a href="/admin">Admin</a>
      </nav>
      <p className="site-footer-copy">Creative Radar — Social-Media-Wochenanalyse für die Filmbranche.</p>
    </footer>
  );
}
