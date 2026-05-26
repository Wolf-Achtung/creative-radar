import React, { useEffect, useState } from 'react';
import { endpoints } from './api/client';

// Roundup-Block — eigenstaendiger Block direkt unterhalb der Studio-
// Briefings auf der Landing-Page (Master-Plan-Schritt-3b, 26.05.).
//
// Architektur-Entscheidungen (Wolf-Ping 1, 26.05.):
// (a) Ein Listen-Endpoint /api/roundups/latest liefert alle Kacheln in
//     einem Call. Frontend macht NICHT vier Einzel-Calls.
// (b) Details als <details>-Aufklappbereich in der Kachel — bestehendes
//     Frontend-Pattern (AssetCard, ReportsPanel). Diese Detail-
//     Aufklappung wird in einem Folge-Commit ergaenzt; hier nur der
//     Block + die Kurzform-Kachel.
//
// Bewusst separat von App.jsx — der bestehende Studio-Briefings-Block
// und die Pair-Brief-Anzeige werden nicht angefasst (Briefing-Pre-
// Commitment). Der Datenpfad (eigener useEffect, unabhaengig vom Auth-
// gegateten load()) spiegelt das /api/pairs-Muster: public-Endpoint
// muss rendern, auch wenn der Bearer-Token noch nicht hydratisiert ist.

// Sprint-Scope: nur die vier substanziellen Segmente. UK-Segmente
// (uk_major, uk_independent) sind auf Prod faktisch leer und nicht im
// Roll-out — kein Kachel-Slot. Spaeteres Zuschalten ist hier ein
// Listen-Change, kein UI-Konzept.
const DISPLAY_SEGMENTS = [
  { key: 'us_major',        label: 'US Major' },
  { key: 'us_independent',  label: 'US Independent' },
  { key: 'de_verleih',      label: 'DE Verleih' },
  { key: 'de_independent',  label: 'DE Independent' },
];

function formatActivity(roundup) {
  if (!roundup) return '';
  const channels = roundup.channels_with_posts;
  const posts = roundup.total_posts;
  return `${channels} aktive Channels · ${posts} Posts`;
}

function RoundupTile({ segmentLabel, roundup }) {
  // Kurzform der Kachel: Segment-Label, KW + Jahr, Headline (oder TLDR-
  // Anriss als Fallback), Aktivitaets-Strip. Muss stark schwankende
  // Dichte aushalten — us_major 171 Posts vs us_independent 10. Die
  // Headline kommt vom LLM und traegt unabhaengig von der Dichte; der
  // Kontext (channels_with_posts/total_posts) macht die Lautstaerke
  // transparent. Erklaerende data_caveats kommen erst im Aufklapp-
  // Bereich (Folge-Commit).
  if (!roundup) {
    return (
      <div
        className="card"
        style={{ padding: '1rem', margin: 0 }}
      >
        <strong style={{ display: 'block', marginBottom: '0.25rem' }}>{segmentLabel}</strong>
        <span className="muted small">Noch kein Roundup</span>
      </div>
    );
  }
  const headlineOrTldr = roundup.llm_output?.headline
    || roundup.llm_output?.tldr
    || 'Roundup verfügbar';
  return (
    <div
      className="card"
      style={{ padding: '1rem', margin: 0 }}
    >
      <strong style={{ display: 'block', marginBottom: '0.25rem' }}>{segmentLabel}</strong>
      <span className="muted small" style={{ display: 'block', marginBottom: '0.5rem' }}>
        {`KW ${roundup.iso_week}/${roundup.iso_year} · ${formatActivity(roundup)}`}
      </span>
      <p style={{ margin: 0 }}>{headlineOrTldr}</p>
    </div>
  );
}

export default function RoundupBlock() {
  // null = noch nicht geladen, [] = geladen aber leer, [...] = Daten da.
  // Dieselbe Drei-Zustands-Logik wie der Pair-Registry-Fetch in App.jsx,
  // bewusst gespiegelt damit der Block sich vorhersehbar verhaelt.
  const [roundups, setRoundups] = useState(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    endpoints.roundupsLatest()
      .then((data) => {
        if (cancelled) return;
        setRoundups(Array.isArray(data?.roundups) ? data.roundups : []);
      })
      .catch((err) => {
        if (cancelled) return;
        console.error('Failed to load /api/roundups/latest', err);
        setError(true);
        setRoundups([]);
      });
    return () => { cancelled = true; };
  }, []);

  // Lookup-Map: segment-key -> roundup-row. Segmente ohne Row im Payload
  // bleiben undefined und werden als "noch kein Roundup"-Slot gerendert.
  const bySegment = (roundups || []).reduce((acc, r) => {
    acc[r.segment] = r;
    return acc;
  }, {});

  return (
    <section style={{
      background: '#1f4d4d',
      padding: '1.5rem 2rem 2rem 2rem',
      marginBottom: '1.5rem',
      borderRadius: '12px',
    }}>
      <p style={{
        color: '#F26B5E',
        fontSize: '0.75em',
        textTransform: 'uppercase',
        letterSpacing: '0.05em',
        marginBottom: '0.5rem',
        fontWeight: 600,
      }}>Diese Woche im Schnitt</p>
      <h2 style={{ color: 'white', marginTop: 0, marginBottom: '0.75rem' }}>Segment-Roundups</h2>
      <p style={{ color: '#aaa', marginTop: 0, marginBottom: '1rem', fontSize: '0.95em' }}>
        Was lief diese Woche in den Non-Pair-Segmenten — deskriptiv, kein Markt-Vergleich.
      </p>

      {roundups === null && (
        <p style={{ color: '#aaa', margin: 0, fontSize: '0.95em' }}>Lade Segment-Roundups …</p>
      )}
      {error && (
        <p style={{ color: '#aaa', margin: 0, fontSize: '0.95em' }}>
          Segment-Roundups momentan nicht verfügbar. Bitte später erneut versuchen.
        </p>
      )}
      {roundups !== null && !error && (
        <div className="roundup-grid" style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
          gap: '0.75rem',
        }}>
          {DISPLAY_SEGMENTS.map(({ key, label }) => (
            <RoundupTile
              key={key}
              segmentLabel={label}
              roundup={bySegment[key]}
            />
          ))}
        </div>
      )}
    </section>
  );
}
