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

// Sprint-Scope: ab UK-Channel-Integration (PRs #175-#178, 26.-27.05.) sind
// auch uk_major und uk_independent im Roll-out. uk_major enthaelt nach der
// Bereinigung (PR #178) nur noch die Pairlos-Marken (20th Century UK,
// StudioCanal UK, Disney+ UK, Film4) — die UK-Majors mit Pair-Pendant
// werden im DE+US+UK-Pair-Brief abgedeckt. Backend-ENV CRON_ROUNDUP_SEGMENTS
// muss in Railway entsprechend gesetzt sein, sonst rendert die Kachel als
// leerer Stand.
const DISPLAY_SEGMENTS = [
  { key: 'us_major',        label: 'US Major' },
  { key: 'us_independent',  label: 'US Independent' },
  { key: 'de_verleih',      label: 'DE Verleih' },
  { key: 'de_independent',  label: 'DE Independent' },
  { key: 'uk_major',        label: 'UK Major' },
  { key: 'uk_independent',  label: 'UK Independent' },
];

function formatActivity(roundup) {
  if (!roundup) return '';
  const channels = roundup.channels_with_posts;
  const posts = roundup.total_posts;
  return `${channels} aktive Channels · ${posts} Posts`;
}

function TitleCard({ entry }) {
  // Eine Card pro Titel. Layout: Titel-Headline, darunter Channel/
  // Format/Datum als Meta-Zeile, dann Kennzahl als Datenanker,
  // abschliessend der Link zum Original-Post (falls vorhanden).
  //
  // Schritt 3d (26.05.): Die VerdictPill ist entfernt. Wolf-Begruendung:
  // die Pills behaupteten ein Urteil, fuer das kein definierter Massstab
  // existierte; die kennzahl spricht fuer sich. Ein eventueller
  // ``entry.verdict`` aus einer KW-22-Pre-3d-Row wird hier bewusst
  // nicht ausgewertet (defensive Toleranz gegen Alt-Rows).
  const meta = [entry.channel, entry.format_typ, entry.release_datum]
    .filter(Boolean).join(' · ');
  return (
    <div style={{
      background: '#fff',
      borderRadius: '8px',
      border: '1px solid #ece4d8',
      padding: '0.625rem 0.75rem',
    }}>
      <strong style={{ display: 'block' }}>{entry.titel}</strong>
      {meta && (
        <p className="muted small" style={{ margin: '0.125rem 0 0.375rem' }}>{meta}</p>
      )}
      {entry.kennzahl && (
        <p className="small" style={{ margin: 0 }}>{entry.kennzahl}</p>
      )}
      {entry.post_url && (
        <p className="small" style={{ margin: '0.375rem 0 0' }}>
          <a href={entry.post_url} target="_blank" rel="noreferrer">Post öffnen</a>
        </p>
      )}
    </div>
  );
}

function RoundupDetails({ llm }) {
  // Schritt-3c Aufklapp-Reihenfolge (Wolf-Vorgabe):
  //   tldr (Kontext) -> titles (Substanz, Block-Grid)
  //   -> themes (Optional, kurz) -> data_caveats (Lautstaerke-Hinweis).
  // Die Schritt-3-Sektionen what_ran und channels_in_focus sind weg —
  // ihr Inhalt liegt jetzt in titles (konkret) bzw. im channel-Feld
  // jeder Title-Card.
  if (!llm) return null;
  const titles = Array.isArray(llm.titles) ? llm.titles.filter(Boolean) : [];
  const themes = Array.isArray(llm.themes) ? llm.themes.filter(Boolean) : [];
  const caveats = Array.isArray(llm.data_caveats) ? llm.data_caveats.filter(Boolean) : [];
  return (
    <details style={{ marginTop: '0.75rem' }}>
      <summary style={{ cursor: 'pointer', fontSize: '13px' }}>Mehr Details</summary>
      <div style={{ marginTop: '0.5rem' }}>
        {llm.tldr && (
          <div style={{ marginBottom: '0.75rem' }}>
            <p className="small" style={{ margin: '0 0 0.25rem', fontWeight: 600 }}>Worum es ging</p>
            <p className="small" style={{ margin: 0 }}>{llm.tldr}</p>
          </div>
        )}
        {titles.length > 0 && (
          <div style={{ marginBottom: '0.75rem' }}>
            <p className="small" style={{ margin: '0 0 0.375rem', fontWeight: 600 }}>Titel im Fokus</p>
            <div style={{
              display: 'grid',
              gridTemplateColumns: '1fr',
              gap: '0.5rem',
            }}>
              {titles.map((entry, index) => <TitleCard key={index} entry={entry} />)}
            </div>
          </div>
        )}
        {themes.length > 0 && (
          <div style={{ marginBottom: '0.5rem' }}>
            <p className="small" style={{ margin: '0 0 0.25rem', fontWeight: 600 }}>Themen</p>
            <ul className="small" style={{ margin: 0, paddingLeft: '1.1rem' }}>
              {themes.map((item, index) => <li key={index}>{item}</li>)}
            </ul>
          </div>
        )}
        {caveats.length > 0 && (
          <div style={{ marginBottom: '0.5rem' }}>
            <p className="small" style={{ margin: '0 0 0.25rem', fontWeight: 600 }}>Datenhinweise</p>
            <ul className="small" style={{ margin: 0, paddingLeft: '1.1rem' }}>
              {caveats.map((item, index) => <li key={index}>{item}</li>)}
            </ul>
          </div>
        )}
      </div>
    </details>
  );
}

function RoundupTile({ segmentLabel, roundup }) {
  // Kurzform der Kachel: Segment-Label, KW + Jahr, Headline (oder TLDR-
  // Anriss als Fallback), Aktivitaets-Strip. Muss stark schwankende
  // Dichte aushalten — us_major 171 Posts vs us_independent 10. Die
  // Headline kommt vom LLM und traegt unabhaengig von der Dichte; der
  // Kontext (channels_with_posts/total_posts) macht die Lautstaerke
  // transparent. Erklaerende data_caveats stehen im <details>-Aufklapp.
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
      <RoundupDetails llm={roundup.llm_output} />
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
      }}>Die Woche im Rückblick</p>
      <h2 style={{ color: 'white', marginTop: 0, marginBottom: '0.75rem' }}>Verleiher &amp; Independents</h2>
      <p style={{ color: '#aaa', marginTop: 0, marginBottom: '1rem', fontSize: '0.95em' }}>
        Was die vergangene Woche gepostet wurde — und wie es ankam.
      </p>

      {roundups === null && (
        <p style={{ color: '#aaa', margin: 0, fontSize: '0.95em' }}>Lade Roundups …</p>
      )}
      {error && (
        <p style={{ color: '#aaa', margin: 0, fontSize: '0.95em' }}>
          Roundups momentan nicht verfügbar. Bitte später erneut versuchen.
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
