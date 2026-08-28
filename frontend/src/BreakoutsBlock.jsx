import React, { useEffect, useState } from 'react';
import { endpoints } from './api/client';
import { SectionHelpHint, SectionHelpPanel } from './components/HelpSystem';
import { clip, formatMultiplier, formatNumber } from './format';

// Breakouts auf der Startseite (Wolf 21.07.): der Feed lag bisher nur im
// Admin-Monitoring — dabei ist "welcher Post explodiert gerade?" fuer
// jeden eingeloggten Nutzer der schnellste Einstieg. Gleiche Daten wie
// der Admin-Feed (GET /api/breakouts, rein lesend, 30-Tage-Fenster,
// >= 2x Kanal-Schnitt), eigene dunkle Sektion analog Studios/Roundups.
//
// Pair-Label kommt als "disney DE+US+UK" — fuer die Startseite reicht
// der Studio-Teil vor dem ersten Leerzeichen, die Maerkte stehen in der
// eigenen Spalte.
function studioFromLabel(label) {
  return (label || '').split(' ')[0] || label;
}

export default function BreakoutsBlock() {
  const [entries, setEntries] = useState(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    endpoints.breakoutsPublic({ limit: 10 })
      .then((data) => {
        if (cancelled) return;
        setEntries(Array.isArray(data?.entries) ? data.entries : []);
      })
      .catch((err) => {
        if (cancelled) return;
        console.error('Failed to load /api/breakouts', err);
        setError(true);
        setEntries([]);
      });
    return () => { cancelled = true; };
  }, []);

  return (
    <section style={{ background: '#1f4d4d', padding: '1.5rem 2rem 2rem 2rem', marginBottom: '1.5rem', borderRadius: '12px' }}>
      {/* Titel-Trennung (Wolf 26.08.): "Breakouts — was gerade
          explodiert" stand direkt neben "Was gerade funktioniert — und
          was nicht" und las sich wie dieselbe Aussage. Der Unterschied
          ist EINZELFALL gegen MUSTER: hier stehen konkrete Posts zum
          Anschauen, dort Merkmale ueber viele Posts hinweg. Beide
          Ueberschriften tragen das jetzt im Wortlaut (Waechter:
          Sektionstitel.test.js). */}
      <p style={{ color: '#ffa294', fontSize: '0.75em', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.5rem', fontWeight: 600 }}>
        Einzelne Posts · letzte 30 Tage
      </p>
      <h2 style={{ color: 'white', marginTop: 0, marginBottom: '0.75rem' }}>
        Einzelne Posts, die herausstechen<SectionHelpHint sectionKey="landingBreakouts" />
      </h2>
      <SectionHelpPanel sectionKey="landingBreakouts" onDark />
      <p style={{ color: '#c8d6cc', margin: '0 0 1rem', fontSize: '0.9em' }}>
        Posts, die <strong>mindestens doppelt so stark</strong> laufen wie der übliche Schnitt ihres
        Kanals. Einzelfälle zum Anschauen, noch keine Regel — Klick öffnet das Original auf der
        Plattform.
      </p>

      {entries === null && (
        <p style={{ color: '#b9c7bd', margin: 0, fontSize: '0.95em' }}>Lade Breakouts …</p>
      )}
      {error && (
        <p style={{ color: '#b9c7bd', margin: 0, fontSize: '0.95em' }}>
          Breakouts momentan nicht verfügbar. Bitte später erneut versuchen.
        </p>
      )}
      {entries !== null && !error && entries.length === 0 && (
        <p style={{ color: '#b9c7bd', margin: 0, fontSize: '0.95em' }}>
          Aktuell liegt kein Post deutlich über dem Schnitt seines Kanals.
        </p>
      )}
      {entries !== null && entries.length > 0 && (
        <div className="card breakouts-card">
          <div style={{ overflowX: 'auto' }}>
            <table className="breakouts-table">
              <thead>
                <tr><th>Studio</th><th>Markt</th><th>Post</th><th>Vielfaches</th><th>Aufrufe</th></tr>
              </thead>
              <tbody>
                {entries.map((entry) => (
                  <tr key={entry.post_url}>
                    <td>{studioFromLabel(entry.pair_label)}</td>
                    <td>{entry.market}</td>
                    <td className="breakouts-caption">
                      <a href={entry.post_url} target="_blank" rel="noreferrer">
                        {clip(entry.caption_excerpt || entry.post_url, 80)}
                      </a>
                    </td>
                    <td className="breakouts-multiplier">{formatMultiplier(entry.multiplier)}</td>
                    {/* Manche Post-Formate (IG-Bildposts, YT-Community-
                        Posts) liefern keine Aufruf-Zahl — "—" statt einer
                        irreführenden 0. Das Vielfache basiert ohnehin auf
                        Interaktionen, nicht auf Aufrufen. */}
                    <td>{entry.views ? formatNumber(entry.views) : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </section>
  );
}
