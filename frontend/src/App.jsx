import React, { useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { endpoints } from './api/client';
import InsightWeekly from './InsightWeekly';
import RoundupBlock from './RoundupBlock';
import AdminPage from './AdminPage';
import AuthGate from './AuthGate';
import BreakoutsBlock from './BreakoutsBlock';
import PatternsBlock from './PatternsBlock';
import GuidePage from './GuidePage';
import LegalPage from './LegalPage';
import UsagePage from './UsagePage';
import { EnvironmentBanner } from './components/EnvironmentBanner';
import { SectionHelpHint, SectionHelpPanel } from './components/HelpSystem';
import { SiteFooter } from './components/SiteFooter';
import {
  computePairSectionDefaults,
  formatPairUpdated,
  isBriefStale,
  pairDeviationLabel,
} from './format';
import './styles.css';

// Lightweight URL-based view-switch. Sprint 28.05.2026 (Admin-Sektion):
// dritte Route ``/admin`` fuer den Login-Gate + Werkzeug-Bereich. Eine
// echte Router-Lib (react-router) ist bei drei Routen weiter overkill —
// pathname-Matching reicht.
function Router() {
  const path = (typeof window !== 'undefined' ? window.location.pathname : '') || '';
  const weeklyMatch = path.match(/^\/insights\/weekly\/([\w.-]+)\/?$/);
  // Sprint User-Login 2026-07: Startseite + Wochen-Briefs stehen
  // komplett hinter dem E-Mail+Code-Login (AuthGate). Ausserhalb des
  // Gates bleiben nur die Rechtsseiten (muessen ohne Login erreichbar
  // sein) und /admin (eigene Passwort-Session, kein Doppel-Login).
  if (weeklyMatch) {
    return (
      <AuthGate>
        <InsightWeekly pair={weeklyMatch[1]} />
      </AuthGate>
    );
  }
  if (path === '/admin' || path === '/admin/') {
    return <AdminPage />;
  }
  // Anleitung fuer neue Nutzer (2026-07-20) — hinter dem Gate wie die
  // Inhalte, auf die sie verweist.
  if (path === '/anleitung' || path === '/anleitung/') {
    return (
      <AuthGate>
        <GuidePage />
      </AuthGate>
    );
  }
  // Nutzungs-Auswertung fuer einzeln freigeschaltete User (2026-07-20).
  // Das Gate prueft nur "eingeloggt"; ob der User die Auswertung sehen
  // darf, erzwingt das Backend pro Request (require_usage_access).
  if (path === '/nutzung' || path === '/nutzung/') {
    return (
      <AuthGate>
        <UsagePage />
      </AuthGate>
    );
  }
  // UX-Audit Befund 1 (2026-07-14): Rechtsseiten als eigene Routen.
  if (path === '/impressum' || path === '/impressum/') {
    return <LegalPage page="impressum" />;
  }
  if (path === '/datenschutz' || path === '/datenschutz/') {
    return <LegalPage page="datenschutz" />;
  }
  return (
    <AuthGate>
      <App />
    </AuthGate>
  );
}

// Sprint 28.05.2026 (Admin-Sektion): die neue, schmale Startseite.
// Hero + Pair-Tiles (mit Markt-Info ueber der Sektion in Commit 3,
// Phase 4) + RoundupBlock. Keine Werkzeuge mehr — die leben in
// /admin hinter dem Login (siehe AdminApp.jsx).
// Sprint 28.05.2026 (Kachel-Variante A) — ermittelt aus der Pair-Liste
// die haeufigste Markt-/Frequenz-Kombination als Sektions-Default.
// Kacheln mit abweichender Kombi tragen eine kleine Pill; Default-
// Kacheln bleiben minimal. Skaliert automatisch wenn neue
// Markt-Kombis dazukommen (z.B. DE-only Indie-Studios).
function App() {
  // ``null`` = not yet fetched; ``[]`` = fetched but empty (treated as
  // an error state); ``[...]`` = ready to render.
  const [pairs, setPairs] = useState(null);
  const [pairsError, setPairsError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    endpoints.pairs()
      .then((data) => {
        if (cancelled) return;
        setPairs(Array.isArray(data?.pairs) ? data.pairs : []);
      })
      .catch((err) => {
        if (cancelled) return;
        console.error('Failed to load /api/pairs', err);
        setPairsError(true);
        setPairs([]);
      });
    return () => { cancelled = true; };
  }, []);

  const sectionDefaults = useMemo(() => computePairSectionDefaults(pairs || []), [pairs]);

  return (
    <main>
      <header className="hero" style={{ background: '#1f4d4d', borderBottomLeftRadius: 0, borderBottomRightRadius: 0 }}>
        <div>
          <p className="eyebrow">Creative Intelligence Workspace</p>
          <h1>Creative Radar</h1>
          <p>Social-Media-Wochenanalyse für die Filmbranche — DE, US, UK.</p>
          <p style={{ margin: '0.5rem 0 0', fontSize: '0.9em' }}>
            <a href="/anleitung" style={{ color: '#ffa294' }}>Neu hier? → Zur Anleitung</a>
          </p>
        </div>
      </header>

      <section style={{ background: '#1f4d4d', padding: '1.5rem 2rem 2rem 2rem', marginBottom: '1.5rem', borderRadius: '0 0 12px 12px', marginTop: 0 }}>
        <p style={{ color: '#ffa294', fontSize: '0.75em', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.5rem', fontWeight: 600 }}>Die Woche im Rückblick</p>
        {/* UX-Nachschliff 2026-07-20 (Wolf): der Unterschied zur Roundup-
            Sektion (ein Studio pro Kachel vs. viele Player gebündelt)
            steht jetzt IMMER sichtbar in Überschrift + Einordnungszeile,
            nicht nur im aufklappbaren Hilfetext. */}
        <h2 style={{ color: 'white', marginTop: 0, marginBottom: '0.5rem' }}>Die großen Studios — einzeln im Blick<SectionHelpHint sectionKey="landingStudios" /></h2>
        <SectionHelpPanel sectionKey="landingStudios" onDark />
        {/* Sprint 28.05.2026 (Kachel-Klick-Signal): Lead-Zeile traegt
            jetzt den Drei-Maerkte-Hinweis UND das Klick-Signal — zusammen
            mit dem "→ zum Brief"-Affordance auf der Kachel signalisiert
            das, dass die Kacheln zu einer eigenen Seite navigieren
            (NICHT in-place aufklappen wie die Roundup-Kacheln). */}
        {pairs && pairs.length > 0 && (
          <p className="pair-section-default" style={{ color: '#c8d6cc', margin: '0 0 1rem', fontSize: '0.9em' }}>
            Jede Kachel ist <strong>ein einzelnes Studio</strong> im Drei-Märkte-Vergleich DE · US · UK — anklicken öffnet den eigenen Wochenbrief.
          </p>
        )}
        {pairs === null && (
          <p style={{ color: '#b9c7bd', margin: 0, fontSize: '0.95em' }}>Lade Studios …</p>
        )}
        {pairsError && (
          <p style={{ color: '#b9c7bd', margin: 0, fontSize: '0.95em' }}>
            Studio-Liste momentan nicht verfügbar. Bitte später erneut versuchen.
          </p>
        )}
        {/* Sprint 28.05.2026 Edge-Case: API antwortet mit leerer Liste
            ohne Fehler (alle Pairs deaktiviert). Vorher sah der Nutzer
            schlicht nichts — die drei Branches (null/error/data)
            sprangen alle nicht an. Jetzt expliziter Slot, analog
            RoundupBlock's Empty-State. */}
        {pairs !== null && !pairsError && pairs.length === 0 && (
          <p style={{ color: '#b9c7bd', margin: 0, fontSize: '0.95em' }}>
            Keine Studios konfiguriert.
          </p>
        )}
        {pairs && pairs.length > 0 && (
          <div className="pair-briefs-grid pair-tile-grid">
            {pairs.map((pair) => {
              const deviation = pairDeviationLabel(pair, sectionDefaults);
              const count = pair.posts_count_completed_week ?? 0;
              // Studio-Kachel-Vorwoche (2026-06-22): Kennzahl + Label zeigen
              // die abgeschlossene KW (KW-1, vom Backend aus
              // last_completed_iso_week_anchor abgeleitet) — konsistent mit
              // den Segment-Ueberschriften darunter. Fallback "diese Woche"
              // nur, falls die KW-Felder fehlen (aeltere API-Antwort).
              const weekLabel =
                pair.iso_week != null && pair.iso_year != null
                  ? `KW ${pair.iso_week}/${pair.iso_year}`
                  : 'diese Woche';
              const generated = formatPairUpdated(pair.last_generated_at);
              const stale = isBriefStale(pair.last_generated_at);
              const headline = (pair.headline || '').trim();
              return (
                <a
                  key={pair.pair_key}
                  href={`/insights/weekly/${pair.pair_key}`}
                  className="card pair-tile"
                >
                  <strong className="pair-tile-name">{pair.display_name}</strong>
                  {deviation && (
                    <span className="pair-tile-deviation">{deviation}</span>
                  )}
                  {/* Sprint 28.05.2026 (Option B): Headline als Lead.
                      Wenn kein Brief existiert (has_brief=false), wird
                      diese Zeile bewusst weggelassen — Kachel bleibt
                      sauber mit Name + Kennzahl. Bei has_brief=true
                      aber leerer Headline (Backend ist robust gegen
                      drei Empty-Faelle) rendert nichts — Kennzahl
                      uebernimmt visuell. */}
                  {headline && (
                    <p className="pair-tile-headline">{headline}</p>
                  )}
                  {!pair.has_brief && (
                    <span className="pair-tile-empty-hint">Brief wird vorbereitet.</span>
                  )}
                  <span className="pair-tile-metric">
                    {`${count} ${count === 1 ? 'Post' : 'Posts'} in ${weekLabel}`}
                  </span>
                  <span className="pair-tile-meta">
                    {generated
                      ? (stale
                          ? `Aktualisiert ${generated} — älter als eine Woche`
                          : `Aktualisiert ${generated}`)
                      : 'Aktualisiert —'}
                  </span>
                  {/* Sprint 28.05.2026 (Klick-Signal): Pfeil + "zum
                      Brief"-Affordance grenzt die Studio-Kachel sichtbar
                      von der Roundup-Kachel ab. Roundup-Kacheln nutzen
                      "▸ Mehr Details" und klappen IN-PLACE auf — wir
                      nutzen bewusst "→ zum Brief", damit die zwei
                      Interaktionen (navigieren vs. aufklappen) nicht
                      verwechselbar sind. */}
                  <span className="pair-tile-action" aria-hidden="true">→ zum Brief</span>
                </a>
              );
            })}
          </div>
        )}
      </section>

      {/* Breakouts zwischen Studios und Roundups (Wolf 21.07.): der
          schnellste "was ist gerade los?"-Einstieg direkt nach den
          Studio-Kacheln. */}
      <BreakoutsBlock />

      {/* Trailer-Intelligence Stufe 1 (20.08.2026): rendert NUR, wenn
          /api/health das Feature meldet (Flag in Staging an, in
          Production aus) — sonst null, kein leerer Rahmen. */}
      <PatternsBlock />

      <RoundupBlock />
      <SiteFooter />
    </main>
  );
}

createRoot(document.getElementById('root')).render(
  <>
    <EnvironmentBanner />
    <Router />
  </>,
);
