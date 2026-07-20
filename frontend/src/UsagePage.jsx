import React from 'react';
import { UsageSection } from './components/UsageSection';
import { SiteFooter } from './components/SiteFooter';

// /nutzung (Wolf-Festlegung 2026-07-20) — Nutzungs-Auswertung fuer
// einzeln freigeschaltete Login-User (app_user.can_view_usage), ohne
// Admin-Passwort. Rendert dieselbe UsageSection wie der Monitoring-Tab
// im Admin-Bereich; die Zugriffskontrolle erzwingt das Backend
// (require_usage_access) — ein nicht freigeschalteter Nutzer, der die
// URL direkt aufruft, bekommt eine freundliche Fehlermeldung statt Daten.
export default function UsagePage() {
  return (
    <main>
      <header className="hero" style={{ background: '#1f4d4d' }}>
        <div>
          <a href="/" className="hero__back-link">← zur Übersicht</a>
          <p className="eyebrow">Monitoring</p>
          <h1>Nutzung</h1>
          <p>Wer nutzt Creative Radar wie — letzte 30 Tage, mit Bericht zum Weitergeben.</p>
        </div>
      </header>
      <section className="card">
        <UsageSection />
      </section>
      <SiteFooter />
    </main>
  );
}
