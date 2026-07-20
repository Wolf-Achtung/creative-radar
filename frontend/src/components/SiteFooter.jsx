import React from 'react';

// UX-Audit Befunde 1+8 (2026-07-14) — globaler Footer als "Sicherheitsnetz"
// auf allen Seiten: Impressum + Datenschutz (Pflichtangaben für eine
// DE-Site, §5 DDG / DSGVO) plus die Nutzer-Anleitung.
//
// 2026-07-20 (Wolf): der "Admin"-Link ist bewusst wieder raus — er lud
// die ~15 normalen Login-Nutzer zum Rumprobieren am Passwort-Formular
// ein, ohne ihnen etwas zu bieten. Der Admin-Bereich bleibt unter
// /admin direkt erreichbar (Bookmark), er wird nur nicht mehr beworben.
export function SiteFooter() {
  return (
    <footer className="site-footer">
      <nav aria-label="Rechtliches und Hilfe">
        <a href="/impressum">Impressum</a>
        <span aria-hidden="true">·</span>
        <a href="/datenschutz">Datenschutz</a>
        <span aria-hidden="true">·</span>
        <a href="/anleitung">Anleitung</a>
      </nav>
      <p className="site-footer-copy">Creative Radar — Social-Media-Wochenanalyse für die Filmbranche.</p>
    </footer>
  );
}
