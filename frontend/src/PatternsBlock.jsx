import React, { useEffect, useState } from 'react';
import { endpoints } from './api/client';

// Trailer-Intelligence Stufe 1 (20.08.2026) — der Muster-Bericht aus
// services/trailer_patterns.py fuer eingeloggte Nutzer: welches Merkmal
// (Genre, Format, Laenge, Tonalitaet, Musik) geht historisch mit
// ueberdurchschnittlicher Reichweite einher. Daten aus
// GET /api/insights/patterns — dieselbe Berechnung wie der
// Admin-Endpunkt, Kanal-normierter Lift + Breakout-Quoten.
//
// Feature-Flag-Gate: der Block fragt /api/health, ob diese Umgebung das
// Feature anbietet (FEATURE_TRAILER_INTELLIGENCE_ENABLED — in Staging
// an, in Production aus). Meldet health nichts, rendert der Block
// NICHTS: derselbe main-Build zeigt auf Prod keine Spur davon.
//
// Ehrlichkeit der Anzeige: nur Zellen, deren Trefferquoten-Verdikt
// nicht "insufficient" ist, stehen in den Tabellen; wie viele duenne
// Zellen es daneben gibt, steht als Zaehler dabei. Die notes des
// Berichts (Abdeckungs-Warnungen) werden UNGEKUERZT angezeigt — sie
// sind Teil des Ergebnisses, nicht Beiwerk.

const DIMENSION_LABEL = {
  genre: 'Genre',
  format: 'Format',
  format_class: 'Formatklasse',
  tone: 'Tonalität',
  lifecycle_stage: 'Kampagnenphase',
  duration_bucket: 'Länge',
  music_kind: 'Musik',
};

// Reihenfolge der Anzeige: die Dimensionen, nach denen Wolf plant,
// zuerst.
const DIMENSION_ORDER = [
  'genre', 'format', 'duration_bucket', 'format_class',
  'tone', 'lifecycle_stage', 'music_kind',
];

const VERDICT_LABEL = {
  over: { text: 'läuft über Schnitt', color: '#7ee2a8' },
  under: { text: 'läuft unter Schnitt', color: '#ffa294' },
  neutral: { text: 'unauffällig', color: '#c8d6cc' },
};

function prozent(wert) {
  return `${(wert * 100).toFixed(1)} %`;
}

function ZellenTabelle({ name, cells }) {
  const brauchbar = cells.filter((c) => c.breakout_verdict !== 'insufficient');
  const duenn = cells.length - brauchbar.length;
  if (brauchbar.length === 0) return null;
  const sortiert = [...brauchbar].sort((a, b) => b.breakout_rate - a.breakout_rate);
  return (
    <div style={{ marginBottom: '1.25rem' }}>
      <h3 style={{ color: 'white', margin: '0 0 0.5rem', fontSize: '1em' }}>
        {DIMENSION_LABEL[name] || name}
        {duenn > 0 && (
          <span style={{ color: '#b9c7bd', fontWeight: 400, fontSize: '0.85em' }}>
            {' '}· {duenn} weitere unter der Stichproben-Schwelle
          </span>
        )}
      </h3>
      <div className="card breakouts-card">
        <div style={{ overflowX: 'auto' }}>
          <table className="breakouts-table">
            <thead>
              <tr>
                <th>Ausprägung</th><th>Posts</th><th>Kanäle</th>
                <th>Breakout-Quote</th><th>erwartet</th><th>Median-Lift</th><th>Befund</th>
              </tr>
            </thead>
            <tbody>
              {sortiert.map((c) => {
                const verdict = VERDICT_LABEL[c.breakout_verdict] || VERDICT_LABEL.neutral;
                return (
                  <tr key={c.value}>
                    <td>{c.value}</td>
                    <td>{c.sample_size}</td>
                    <td>{c.channel_count}</td>
                    <td className="breakouts-multiplier">{prozent(c.breakout_rate)}</td>
                    <td>{prozent(c.expected_breakout_rate)}</td>
                    <td>{c.median_lift.toFixed(2)}x</td>
                    <td style={{ color: verdict.color }}>{verdict.text}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function BausteinKarte({ baustein }) {
  const listen = [
    ['Hooks DE', baustein.hooks_de],
    ['Hooks EN', baustein.hooks_en],
    ['Captions DE', baustein.captions_de],
    ['Captions EN', baustein.captions_en],
  ];
  return (
    <div className="card breakouts-card" style={{ marginBottom: '1rem', padding: '1rem 1.25rem' }}>
      <h4 style={{ color: 'white', margin: '0 0 0.35rem', fontSize: '1em' }}>{baustein.muster}</h4>
      <p style={{ color: '#c8d6cc', margin: '0 0 0.75rem', fontSize: '0.85em' }}>{baustein.begruendung}</p>
      {listen.map(([label, eintraege]) => (
        (eintraege || []).length > 0 && (
          <div key={label} style={{ marginBottom: '0.5rem' }}>
            <p style={{ color: '#ffa294', margin: '0 0 0.15rem', fontSize: '0.7em', textTransform: 'uppercase', letterSpacing: '0.05em', fontWeight: 600 }}>
              {label}
            </p>
            <ul style={{ color: '#e8f0ea', margin: 0, paddingLeft: '1.2rem', fontSize: '0.9em' }}>
              {eintraege.map((text) => <li key={text}>{text}</li>)}
            </ul>
          </div>
        )
      ))}
      {(baustein.hashtags || []).length > 0 && (
        <p style={{ color: '#b9c7bd', margin: '0.5rem 0 0', fontSize: '0.8em' }}>
          {baustein.hashtags.map((tag) => `#${tag}`).join(' ')}
        </p>
      )}
      {(baustein.cited_post_ids || []).length > 0 && (
        <p style={{ margin: '0.5rem 0 0', fontSize: '0.75em' }}>
          <span style={{ color: '#b9c7bd' }}>Belege: </span>
          {baustein.cited_post_ids.map((url, i) => (
            <a key={url} href={url} target="_blank" rel="noreferrer" style={{ color: '#7ee2a8', marginRight: '0.5rem' }}>
              Post {i + 1}
            </a>
          ))}
        </p>
      )}
    </div>
  );
}

export default function PatternsBlock() {
  const [aktiv, setAktiv] = useState(false);
  const [daten, setDaten] = useState(null);
  const [fehler, setFehler] = useState(false);
  const [briefing, setBriefing] = useState(null);

  useEffect(() => {
    let cancelled = false;
    endpoints.health()
      .then((health) => {
        if (cancelled) return;
        if (health?.features?.trailer_intelligence) {
          setAktiv(true);
          // Text-Bausteine sind ein eigener Datensatz mit eigenem
          // Fehlerpfad: 404 (noch kein Briefing persistiert) ist ein
          // NORMALER Zustand und blendet die Sektion still aus — er darf
          // den Muster-Tabellen keinen Fehlerkasten bescheren.
          endpoints.insightPatternBriefing()
            .then((data) => { if (!cancelled) setBriefing(data); })
            .catch(() => {});
          return endpoints.insightPatterns({ windowDays: 90 }).then((data) => {
            if (!cancelled) setDaten(data);
          });
        }
        return undefined;
      })
      .catch((err) => {
        if (cancelled) return;
        console.error('Failed to load /api/insights/patterns', err);
        setFehler(true);
      });
    return () => { cancelled = true; };
  }, []);

  // Flag aus (oder health nicht lesbar): kein Rahmen, kein Platzhalter —
  // Produktion sieht von Stufe 1 nichts.
  if (!aktiv) return null;

  const dimensionen = daten?.dimensions || {};
  const geordnet = DIMENSION_ORDER.filter((n) => dimensionen[n]?.length);
  const sichtbare = geordnet.filter(
    (n) => dimensionen[n].some((c) => c.breakout_verdict !== 'insufficient'),
  );

  return (
    <section style={{ background: '#1f4d4d', padding: '1.5rem 2rem 2rem 2rem', marginBottom: '1.5rem', borderRadius: '12px' }}>
      <p style={{ color: '#ffa294', fontSize: '0.75em', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.5rem', fontWeight: 600 }}>
        Trailer-Intelligence · letzte 90 Tage
      </p>
      <h2 style={{ color: 'white', marginTop: 0, marginBottom: '0.75rem' }}>
        Muster — was historisch funktioniert
      </h2>
      <p style={{ color: '#c8d6cc', margin: '0 0 1rem', fontSize: '0.9em' }}>
        Welche Merkmale gehen mit überdurchschnittlicher Reichweite einher? Jeder Post wird
        an seinem <strong>eigenen Kanal-Schnitt</strong> gemessen, damit große und kleine
        Kanäle vergleichbar sind. Die Breakout-Quote sagt, wie oft ein Merkmal Ausreißer
        (mindestens 2x Kanal-Schnitt) produziert — verglichen mit der Quote, die seine
        Plattform-Mischung erwarten ließe.
      </p>

      {fehler && (
        <p style={{ color: '#b9c7bd', margin: 0, fontSize: '0.95em' }}>
          Muster momentan nicht verfügbar. Bitte später erneut versuchen.
        </p>
      )}
      {!fehler && daten === null && (
        <p style={{ color: '#b9c7bd', margin: 0, fontSize: '0.95em' }}>Lade Muster …</p>
      )}
      {!fehler && daten !== null && sichtbare.length === 0 && (
        <p style={{ color: '#b9c7bd', margin: 0, fontSize: '0.95em' }}>
          Noch kein Muster mit ausreichender Stichprobe. Die Datenbasis wächst mit jedem
          Cron-Lauf.
        </p>
      )}
      {!fehler && sichtbare.map((name) => (
        <ZellenTabelle key={name} name={name} cells={dimensionen[name]} />
      ))}
      {!fehler && daten !== null && (
        <p style={{ color: '#b9c7bd', margin: '0.5rem 0 0', fontSize: '0.8em' }}>
          Datenbasis: {daten.posts_with_baseline} Posts mit Kanal-Baseline
          {' '}({daten.channels_covered} Kanäle, Fenster {daten.window_days} Tage).
        </p>
      )}
      {!fehler && daten !== null && (daten.notes || []).length > 0 && (
        <ul style={{ color: '#b9c7bd', margin: '0.5rem 0 0', paddingLeft: '1.2rem', fontSize: '0.8em' }}>
          {daten.notes.map((note) => <li key={note}>{note}</li>)}
        </ul>
      )}

      {briefing?.llm_output && (briefing.llm_output.bausteine || []).length > 0 && (
        <div style={{ marginTop: '1.5rem' }}>
          <h3 style={{ color: 'white', margin: '0 0 0.25rem', fontSize: '1.05em' }}>
            Text-Bausteine aus den Mustern
          </h3>
          <p style={{ color: '#c8d6cc', margin: '0 0 1rem', fontSize: '0.85em' }}>
            Hooks und Caption-Vorlagen, abgeleitet aus den stärksten Posts je Muster —
            jede Empfehlung mit Belegen. Stand KW {briefing.iso_week}/{briefing.iso_year}.
          </p>
          {briefing.llm_output.bausteine.map((baustein) => (
            <BausteinKarte key={baustein.muster} baustein={baustein} />
          ))}
          {(briefing.llm_output.data_caveats || []).length > 0 && (
            <ul style={{ color: '#b9c7bd', margin: '0.25rem 0 0', paddingLeft: '1.2rem', fontSize: '0.8em' }}>
              {briefing.llm_output.data_caveats.map((caveat) => <li key={caveat}>{caveat}</li>)}
            </ul>
          )}
          {briefing.citation_dropped > 0 && (
            <p style={{ color: '#b9c7bd', margin: '0.25rem 0 0', fontSize: '0.8em' }}>
              {briefing.citation_dropped} Baustein(e) wurden verworfen, weil ihre Belege
              nicht in den mitgegebenen Beispiel-Posts lagen.
            </p>
          )}
        </div>
      )}
    </section>
  );
}
