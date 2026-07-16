import React from 'react';
import { SiteFooter } from './components/SiteFooter';

// UX-Audit Befund 1 (2026-07-14) — Impressum & Datenschutz als eigene
// Routen. Die konkreten Pflichtangaben (Betreiber, Anschrift, Kontakt,
// ggf. USt-ID) und der Datenschutztext werden von Wolf nachgeliefert;
// bis dahin trägt die Seite einen ehrlichen, klar markierten Platzhalter
// statt erfundener Angaben. Struktur/Route stehen damit bereits, das
// Befüllen ist ein reiner Text-Edit in LEGAL_CONTENT.
const LEGAL_CONTENT = {
  impressum: {
    title: 'Impressum',
    sections: [
      {
        heading: 'Angaben gemäß § 5 DDG',
        body: 'Die vollständigen Anbieterangaben (Betreiber, Anschrift, Kontakt) werden derzeit ergänzt.',
      },
    ],
  },
  datenschutz: {
    title: 'Datenschutzerklärung',
    sections: [
      {
        heading: 'Datenschutz auf einen Blick',
        body: 'Die vollständige Datenschutzerklärung wird derzeit ergänzt. Creative Radar setzt keine Tracking-Cookies ein; im Admin-Bereich wird ein technisch notwendiges Session-Cookie für die Anmeldung verwendet.',
      },
    ],
  },
};

export default function LegalPage({ page }) {
  const content = LEGAL_CONTENT[page] || LEGAL_CONTENT.impressum;
  return (
    <main>
      <header className="hero" style={{ background: '#1f4d4d' }}>
        <div>
          <a href="/" className="hero__back-link">← zur Übersicht</a>
          <p className="eyebrow">Rechtliches</p>
          <h1>{content.title}</h1>
        </div>
      </header>
      {content.sections.map((section) => (
        <section key={section.heading} className="card">
          <h2>{section.heading}</h2>
          <p>{section.body}</p>
        </section>
      ))}
      <SiteFooter />
    </main>
  );
}
