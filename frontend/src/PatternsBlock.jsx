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

// Die Quote-Spalte liegt auf der hellen Karte und faerbt nach Befund —
// NICHT die Immer-Rot-Klasse des Breakouts-Blocks (dort heisst Rot
// "Ausreisser nach oben", hier waere Rot auf 20 % ueber Schnitt eine
// falsche Warnung). Dunklere Toene als VERDICT_LABEL, wegen des
// Kontrasts auf Creme statt Dunkelgruen.
const QUOTE_COLOR_ON_CARD = {
  over: '#1f7a45',
  under: '#b03d2e',
  neutral: '#6b6b6b',
};

// Anzeige-Namen fuer bekannte Auspraegungen — unbekannte Werte (Genres,
// Titel) erscheinen unveraendert. Die Karten sprechen Klartext, die
// Tabellen behalten die Rohwerte (Wiedererkennung mit Admin-Endpoint).
const WERT_LABEL = {
  behind_the_scenes: 'Behind-the-Scenes',
  trailer: 'Trailer',
  teaser: 'Teaser',
  clip: 'Clip',
  promo: 'Promo',
  interview: 'Interview',
  compilation: 'Compilation',
  short: 'Short',
  langform: 'Langform',
  kurzform: 'Kurzform',
  uebergang_60_90s: 'Übergang 60–90s',
  emotional: 'Emotional',
  energetic: 'Energetisch',
  informative: 'Informativ',
  inspirational: 'Inspirierend',
  suspenseful: 'Spannungsgetrieben',
  humorous: 'Humorvoll',
  neutral: 'Neutral',
  edgy: 'Edgy',
  pre_launch: 'Pre-Launch (vor dem Start)',
  launch: 'Zum Start',
  post_launch: 'Nach dem Start',
  evergreen: 'Evergreen',
  unclear: 'Phase unklar',
  licensed_track: 'Lizenzierter Track',
  original_sound: 'Original-Sound',
};

function prozent(wert) {
  return `${(wert * 100).toFixed(1)} %`;
}

// Die staerksten Befunde ueber ALLE Dimensionen, nach Signalstaerke
// (|z|) sortiert. Nur over/under — ein unauffaelliges Muster ist keine
// Empfehlung, ein duennes erst recht nicht. Exportiert fuer den Test.
export function staerksteBefunde(dimensions, max = 5) {
  const alle = [];
  Object.entries(dimensions || {}).forEach(([dim, cells]) => {
    (cells || []).forEach((cell) => {
      if (
        (cell.breakout_verdict === 'over' || cell.breakout_verdict === 'under')
        && cell.breakout_z !== null && cell.breakout_z !== undefined
      ) {
        alle.push({ dim, cell });
      }
    });
  });
  alle.sort((a, b) => Math.abs(b.cell.breakout_z) - Math.abs(a.cell.breakout_z));
  return alle.slice(0, max);
}

function BefundKarte({ dim, cell }) {
  const over = cell.breakout_verdict === 'over';
  const farbe = QUOTE_COLOR_ON_CARD[cell.breakout_verdict];
  const faktor = cell.expected_breakout_rate > 0
    ? (cell.breakout_rate / cell.expected_breakout_rate).toFixed(1)
    : null;
  const satz = over
    ? `Produziert ${faktor ? `${faktor}× so oft` : 'öfter'} Ausreißer wie erwartet `
      + `(${prozent(cell.breakout_rate)} statt ${prozent(cell.expected_breakout_rate)}).`
    : `Bleibt unter der erwarteten Ausreißer-Quote `
      + `(${prozent(cell.breakout_rate)} statt ${prozent(cell.expected_breakout_rate)}).`;
  return (
    <div
      className="card breakouts-card"
      style={{ flex: '1 1 250px', maxWidth: '340px', padding: '0.85rem 1rem', borderLeft: `4px solid ${farbe}` }}
    >
      <p style={{ margin: '0 0 0.25rem', fontSize: '0.7em', textTransform: 'uppercase', letterSpacing: '0.05em', fontWeight: 600, color: '#6b6b6b' }}>
        {DIMENSION_LABEL[dim] || dim} · <span style={{ color: farbe }}>{over ? 'Mehr davon testen' : 'Sparsam einsetzen'}</span>
      </p>
      <p style={{ margin: '0 0 0.35rem', fontWeight: 700, fontSize: '1em' }}>
        {WERT_LABEL[cell.value] || cell.value}
      </p>
      <p style={{ margin: 0, fontSize: '0.85em', color: '#4a4a44' }}>
        {satz} {cell.sample_size} Posts von {cell.channel_count} Kanälen.
      </p>
    </div>
  );
}

function BefundKarten({ dimensions }) {
  const befunde = staerksteBefunde(dimensions);
  if (befunde.length === 0) return null;
  return (
    <div style={{ marginBottom: '1.5rem' }}>
      <h3 style={{ color: 'white', margin: '0 0 0.5rem', fontSize: '1em' }}>
        Das Wichtigste zuerst
      </h3>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.75rem' }}>
        {befunde.map(({ dim, cell }) => (
          <BefundKarte key={`${dim}:${cell.value}`} dim={dim} cell={cell} />
        ))}
      </div>
      <p style={{ color: '#b9c7bd', margin: '0.5rem 0 0', fontSize: '0.75em' }}>
        Gemessene Korrelationen im eigenen Bestand — kein Beweis für Ursache und
        Wirkung. Die Tabellen darunter zeigen die vollständigen Zahlen.
      </p>
    </div>
  );
}

// Redundante Zweit-Kodierung der beiden Zahlenspalten: Balken = Quote,
// Strich = erwartete Quote, Skala je Tabelle. Duenn, an der Basislinie
// verankert; die Farbe traegt nichts Eigenes (der Befund steht als Text
// daneben), sie wiederholt nur das Verdikt.
function DeltaBalken({ rate, expected, verdict, skala }) {
  const anteil = (wert) => `${((wert / skala) * 100).toFixed(1)}%`;
  return (
    <div
      title={`${prozent(rate)} gegen ${prozent(expected)} erwartet`}
      style={{ position: 'relative', width: '110px', height: '8px', background: '#e4ddd0', borderRadius: '4px' }}
    >
      <div style={{ position: 'absolute', left: 0, top: 0, height: '8px', width: anteil(rate), background: QUOTE_COLOR_ON_CARD[verdict] || QUOTE_COLOR_ON_CARD.neutral, borderRadius: '4px' }} />
      <div style={{ position: 'absolute', left: anteil(expected), top: '-2px', height: '12px', width: '2px', background: '#4a4a44' }} />
    </div>
  );
}

// Aufgeklappte Zeile: die staerksten Posts hinter der Zelle — Lift,
// Kanal, Original-Caption, Link. Erst mit den konkreten Posts wird aus
// "laeuft ueber Schnitt" Referenzmaterial.
function BeispielZeile({ eintrag }) {
  if (!eintrag || eintrag.status === 'laden') {
    return <p style={{ margin: 0, color: '#6b6b6b', fontSize: '0.85em' }}>Lade Beispiel-Posts …</p>;
  }
  const beispiele = eintrag.daten?.examples || [];
  if (eintrag.status === 'fehler' || beispiele.length === 0) {
    return <p style={{ margin: 0, color: '#6b6b6b', fontSize: '0.85em' }}>Keine Beispiel-Posts verfügbar.</p>;
  }
  return (
    <div>
      {beispiele.map((ex) => (
        <div key={ex.post_url} style={{ padding: '0.35rem 0', borderBottom: '1px solid #eee6d8' }}>
          <span style={{ fontWeight: 700 }}>{ex.lift}x</span>
          {' '}@{ex.channel_handle} ({ex.platform})
          {ex.views ? ` · ${ex.views.toLocaleString('de-DE')} Views` : ''}
          {ex.detected_at ? ` · ${ex.detected_at}` : ''}
          {' · '}
          <a href={ex.post_url} target="_blank" rel="noreferrer" style={{ color: '#1f7a45' }}>
            Post öffnen
          </a>
          {ex.caption && (
            <div style={{ color: '#6b6b6b', fontSize: '0.85em', marginTop: '0.1rem' }}>
              „{ex.caption}“
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

function ZellenTabelle({ name, cells }) {
  const [offen, setOffen] = useState(null);
  const [beispiele, setBeispiele] = useState({});
  const brauchbar = cells.filter((c) => c.breakout_verdict !== 'insufficient');
  const duenn = cells.length - brauchbar.length;
  if (brauchbar.length === 0) return null;

  // Laden erst beim ersten Aufklappen — und nur einmal je Zelle. Ein
  // Fehler zeigt eine Leer-Meldung in der Zeile, nie einen Fehlerkasten
  // fuers ganze Panel.
  const zeileKlick = (wert) => {
    if (offen === wert) {
      setOffen(null);
      return;
    }
    setOffen(wert);
    if (!beispiele[wert]) {
      setBeispiele((b) => ({ ...b, [wert]: { status: 'laden' } }));
      endpoints.insightPatternExamples({ dimension: name, value: wert })
        .then((daten) => {
          setBeispiele((b) => ({ ...b, [wert]: { status: 'ok', daten } }));
        })
        .catch(() => {
          setBeispiele((b) => ({ ...b, [wert]: { status: 'fehler' } }));
        });
    }
  };
  const sortiert = [...brauchbar].sort((a, b) => b.breakout_rate - a.breakout_rate);
  // Eine Skala je Tabelle, damit die Balken einer Dimension vergleichbar
  // sind; 1.15 laesst den laengsten Balken nicht an den Rand stossen.
  const skala = Math.max(
    ...sortiert.map((c) => Math.max(c.breakout_rate, c.expected_breakout_rate)),
  ) * 1.15 || 1;
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
                <th>Breakout-Quote</th><th>erwartet</th>
                <th aria-label="Quote im Vergleich zur erwarteten Quote" />
                <th>Median-Lift</th><th>Befund</th>
              </tr>
            </thead>
            <tbody>
              {sortiert.map((c) => {
                const verdict = VERDICT_LABEL[c.breakout_verdict] || VERDICT_LABEL.neutral;
                return (
                  <React.Fragment key={c.value}>
                  <tr
                    onClick={() => zeileKlick(c.value)}
                    style={{ cursor: 'pointer' }}
                    title="Beispiel-Posts anzeigen"
                  >
                    <td>{offen === c.value ? '▾ ' : '▸ '}{c.value}</td>
                    <td>{c.sample_size}</td>
                    <td>{c.channel_count}</td>
                    <td style={{
                      fontWeight: 700,
                      whiteSpace: 'nowrap',
                      color: QUOTE_COLOR_ON_CARD[c.breakout_verdict] || QUOTE_COLOR_ON_CARD.neutral,
                    }}>
                      {prozent(c.breakout_rate)}
                    </td>
                    <td>{prozent(c.expected_breakout_rate)}</td>
                    <td>
                      <DeltaBalken
                        rate={c.breakout_rate}
                        expected={c.expected_breakout_rate}
                        verdict={c.breakout_verdict}
                        skala={skala}
                      />
                    </td>
                    <td>{c.median_lift.toFixed(2)}x</td>
                    <td style={{ color: verdict.color }}>
                      {verdict.text}
                      {c.trend === 'neu' && (
                        <span style={{ color: '#6b6b6b', fontSize: '0.85em' }}> · neu belastbar</span>
                      )}
                      {c.trend === 'gewechselt' && c.vorwoche && (
                        <span style={{ color: '#6b6b6b', fontSize: '0.85em' }}>
                          {' '}· Vorwoche: {(VERDICT_LABEL[c.vorwoche.breakout_verdict] || VERDICT_LABEL.neutral).text}
                        </span>
                      )}
                    </td>
                  </tr>
                  {offen === c.value && (
                    <tr>
                      <td colSpan={8} style={{ background: '#faf5ea', padding: '0.5rem 0.75rem' }}>
                        <BeispielZeile eintrag={beispiele[c.value]} />
                      </td>
                    </tr>
                  )}
                  </React.Fragment>
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
  const [titelBriefing, setTitelBriefing] = useState(null);

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
          // den Muster-Tabellen keinen Fehlerkasten bescheren. Beide
          // Ebenen (Genre / Titel) laden getrennt: jede hat ihre eigene
          // juengste Woche, und eine fehlende Ebene versteckt nur sich.
          endpoints.insightPatternBriefing({ mode: 'genre' })
            .then((data) => { if (!cancelled) setBriefing(data); })
            .catch(() => {});
          endpoints.insightPatternBriefing({ mode: 'title' })
            .then((data) => { if (!cancelled) setTitelBriefing(data); })
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
      {!fehler && daten !== null && <BefundKarten dimensions={dimensionen} />}
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

      <BausteinSektion
        briefing={briefing}
        titel="Text-Bausteine aus den Mustern"
        beschreibung="Hooks und Caption-Vorlagen, abgeleitet aus den stärksten Posts je Muster — jede Empfehlung mit Belegen."
      />
      <BausteinSektion
        briefing={titelBriefing}
        titel="Text-Bausteine je Titel"
        beschreibung="Hooks und Caption-Vorlagen je Kampagne, abgeleitet aus den stärksten Posts des jeweiligen Titels — jede Empfehlung mit Belegen."
      />
    </section>
  );
}

function BausteinSektion({ briefing, titel, beschreibung }) {
  if (!briefing?.llm_output || (briefing.llm_output.bausteine || []).length === 0) {
    return null;
  }
  return (
    <div style={{ marginTop: '1.5rem' }}>
      <h3 style={{ color: 'white', margin: '0 0 0.25rem', fontSize: '1.05em' }}>
        {titel}
      </h3>
      <p style={{ color: '#c8d6cc', margin: '0 0 1rem', fontSize: '0.85em' }}>
        {beschreibung} Stand KW {briefing.iso_week}/{briefing.iso_year}.
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
  );
}
