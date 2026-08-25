import React, { useEffect, useState } from 'react';
import { apiUrl, endpoints } from './api/client';

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

export const DIMENSION_LABEL = {
  genre: 'Genre',
  format: 'Format',
  format_class: 'Formatklasse',
  tone: 'Tonalität',
  lifecycle_stage: 'Kampagnenphase',
  duration_bucket: 'Länge',
  music_kind: 'Musik',
  cover_titel: 'Cover: Titel im Bild',
  cover_kinetik: 'Cover: Bildtext & Kinetik',
  caption_frage: 'Caption: Frage',
  caption_cta: 'Caption: Call-to-Action',
  caption_laenge: 'Caption-Länge',
  caption_hashtags: 'Hashtags',
};

// Reihenfolge der Anzeige: die Dimensionen, nach denen Wolf plant,
// zuerst.
const DIMENSION_ORDER = [
  'genre', 'format', 'duration_bucket', 'format_class',
  'tone', 'lifecycle_stage', 'music_kind',
  'cover_titel', 'cover_kinetik',
  'caption_frage', 'caption_cta', 'caption_laenge', 'caption_hashtags',
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
export const WERT_LABEL = {
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
  mit_titel: 'Titel im Bild',
  ohne_titel: 'Ohne Titel im Bild',
  ohne_kinetik: 'Ohne Bildtext',
  text_overlay: 'Text-Overlay',
  title_card: 'Title-Card',
  animated_text: 'Animierte Schrift',
  motion_graphic: 'Motion-Graphic',
  mit_frage: 'Caption mit Frage',
  ohne_frage: 'Caption ohne Frage',
  mit_cta: 'Mit Call-to-Action',
  ohne_cta: 'Ohne Call-to-Action',
  kurz: 'Kurze Caption (≤80 Zeichen)',
  mittel: 'Mittlere Caption (81–199)',
  lang: 'Lange Caption (200+)',
  keine: 'Keine Hashtags',
  '1-3': '1–3 Hashtags',
  '4+': '4+ Hashtags',
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

// Radar (20.08.2026): was sich gegenueber der Vorwoche geaendert hat.
// Nur echte Bewegung — ein Verdikt-WECHSEL oder eine NEU belastbare
// Zelle mit Befund. "stabil" und duenne Zellen sind keine Nachricht.
// Wechsel vor Neuzugaengen, innerhalb nach Signalstaerke. Exportiert
// fuer den Test.
export function bewegungen(dimensions, max = 4) {
  const alle = [];
  Object.entries(dimensions || {}).forEach(([dim, cells]) => {
    (cells || []).forEach((cell) => {
      if (cell.trend === 'gewechselt') {
        alle.push({ dim, cell, art: 'gewechselt' });
      } else if (
        cell.trend === 'neu'
        && (cell.breakout_verdict === 'over' || cell.breakout_verdict === 'under')
      ) {
        alle.push({ dim, cell, art: 'neu' });
      }
    });
  });
  alle.sort((a, b) => {
    if (a.art !== b.art) return a.art === 'gewechselt' ? -1 : 1;
    return Math.abs(b.cell.breakout_z ?? 0) - Math.abs(a.cell.breakout_z ?? 0);
  });
  return alle.slice(0, max);
}

const VERDICT_WORT = {
  over: 'läuft über Schnitt',
  under: 'läuft unter Schnitt',
  neutral: 'unauffällig',
};

function BewegungsKarte({ dim, cell, art }) {
  const farbe = QUOTE_COLOR_ON_CARD[cell.breakout_verdict] || QUOTE_COLOR_ON_CARD.neutral;
  const satz = art === 'gewechselt'
    ? `${VERDICT_WORT[cell.vorwoche?.breakout_verdict] || 'unauffällig'} → `
      + `${VERDICT_WORT[cell.breakout_verdict]} `
      + `(Quote ${prozent(cell.breakout_rate)} nach ${prozent(cell.vorwoche?.breakout_rate ?? 0)}).`
    : `neu belastbar — ${VERDICT_WORT[cell.breakout_verdict]} (${prozent(cell.breakout_rate)}).`;
  return (
    <div
      className="card breakouts-card"
      style={{ flex: '1 1 250px', maxWidth: '340px', padding: '0.85rem 1rem', borderLeft: `4px solid ${farbe}` }}
    >
      <p style={{ margin: '0 0 0.25rem', fontSize: '0.7em', textTransform: 'uppercase', letterSpacing: '0.05em', fontWeight: 600, color: '#6b6b6b' }}>
        {DIMENSION_LABEL[dim] || dim} · <span style={{ color: farbe }}>{art === 'gewechselt' ? 'Verdikt gewechselt' : 'Neu belastbar'}</span>
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

function BewegungsKarten({ dimensions }) {
  const liste = bewegungen(dimensions);
  if (liste.length === 0) return null;
  return (
    <div style={{ marginBottom: '1.5rem' }}>
      <h3 style={{ color: 'white', margin: '0 0 0.5rem', fontSize: '1em' }}>
        Bewegung diese Woche
      </h3>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.75rem' }}>
        {liste.map(({ dim, cell, art }) => (
          <BewegungsKarte key={`${dim}:${cell.value}`} dim={dim} cell={cell} art={art} />
        ))}
      </div>
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
        <div key={ex.post_url} style={{ display: 'flex', alignItems: 'flex-start', gap: '0.5rem', padding: '0.35rem 0', borderBottom: '1px solid #eee6d8' }}>
          <ReferenzThumb assetId={ex.asset_id} />
          <div>
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
        </div>
      ))}
    </div>
  );
}

function ZellenTabelle({ name, cells, ohneTitel = false }) {
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
      {!ohneTitel && (
        <h3 style={{ color: 'white', margin: '0 0 0.5rem', fontSize: '1em' }}>
          {DIMENSION_LABEL[name] || name}
          {duenn > 0 && (
            <span style={{ color: '#b9c7bd', fontWeight: 400, fontSize: '0.85em' }}>
              {' '}· {duenn} weitere unter der Stichproben-Schwelle
            </span>
          )}
        </h3>
      )}
      <div className="card breakouts-card">
        <div style={{ overflowX: 'auto' }}>
          <table className="breakouts-table">
            <thead>
              <tr>
                <th>Ausprägung</th><th>Posts</th><th>Kanäle</th>
                <th>Breakout-Quote</th><th>erwartet</th>
                <th aria-label="Quote im Vergleich zur erwarteten Quote" />
                <th>Befund</th>
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
                      <td colSpan={7} style={{ background: '#faf5ea', padding: '0.5rem 0.75rem' }}>
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
              {eintraege.map((text) => <li key={text}>{text} <CopyButton text={text} /></li>)}
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


// Fokus-Ansicht (20.08.2026): Dimensionen ohne einen einzigen Befund
// klappen standardmaessig zu einer Zeile zusammen — elf Tabellen voller
// "unauffaellig" sind Rauschen, nicht Information. Exportiert fuer den
// Test.
export function hatBefund(cells) {
  return (cells || []).some(
    (c) => c.breakout_verdict === 'over' || c.breakout_verdict === 'under',
  );
}

function EingeklappteDimension({ name, cells }) {
  const [offen, setOffen] = useState(false);
  const brauchbar = cells.filter((c) => c.breakout_verdict !== 'insufficient');
  const duenn = cells.length - brauchbar.length;
  return (
    <div style={{ marginBottom: offen ? '0.5rem' : '0.25rem' }}>
      <button
        type="button"
        onClick={() => setOffen((v) => !v)}
        style={{
          background: 'none', border: 'none', padding: '0.15rem 0',
          color: '#b9c7bd', fontSize: '0.9em', cursor: 'pointer',
          textAlign: 'left',
        }}
      >
        {offen ? '▾' : '▸'} {DIMENSION_LABEL[name] || name}
        {' '}— {brauchbar.length} Ausprägungen, alle unauffällig
        {duenn > 0 && ` · ${duenn} unter der Stichproben-Schwelle`}
      </button>
      {offen && <ZellenTabelle name={name} cells={cells} ohneTitel />}
    </div>
  );
}


// ---- Empfehlungs-Ansicht (20.08.2026) -------------------------------
//
// Wolfs Befund nach dem ersten Team-Blick: die Seite beantwortet
// "stimmt das?" (Analysten-Frage), aber nicht "was mache ich am Montag
// anders?" (Cutter-Frage). Der Empfehlungs-Tab uebersetzt die
// staerksten Befunde deterministisch in Werkstatt-Sprache — KEIN LLM,
// jede Karte kommt aus einer geprueften Zelle, die Referenz-Posts sind
// dieselben wie hinter den Tabellenzeilen. Der Zahlen-Tab bleibt die
// vollstaendige Evidenz.
//
// Vorlagen je (Dimension, Wert) fuer die bekannten Befunde; alles ohne
// Vorlage bekommt einen generischen, ehrlichen Satz. Exportiert fuer
// den Test.

// Themen-Woerter fuer die Empfehlungs-Karten: Alltagssprache statt
// Dimensions-Namen. "Cover: Bildtext & Kinetik" erklaert sich nicht
// selbst — "Cover" schon. Der Zahlen-Tab behaelt die praezisen Labels.
const THEMA_LABEL = {
  genre: 'Genre',
  format: 'Format',
  format_class: 'Länge',
  duration_bucket: 'Länge',
  tone: 'Tonfall',
  lifecycle_stage: 'Timing',
  music_kind: 'Musik',
  cover_titel: 'Cover',
  cover_kinetik: 'Cover',
  caption_frage: 'Bildunterschrift',
  caption_cta: 'Bildunterschrift',
  caption_laenge: 'Bildunterschrift',
  caption_hashtags: 'Hashtags',
};

// Der Tipp steht seit Wolfs Layout-Feedback (25.08.) GETRENNT vom
// Satz: im Fliesstext ging die konstruktivste Aussage unter, jetzt
// bekommt sie einen eigenen, hervorgehobenen Kasten.
const WERKSTATT_VORLAGEN = {
  'cover_kinetik:title_card': (f) => ({
    titel: 'Titel-Tafel im Cover wirkt',
    satz: `Posts mit gestalteter Titel-Tafel im Cover liegen ${f}-mal öfter weit über dem Kanal-Schnitt als Posts ohne.`,
    tipp: 'Eine gestaltete Titel-Tafel im Cover lohnt den Design-Aufwand.',
  }),
  'lifecycle_stage:pre_launch': (f) => ({
    titel: 'Die stärkste Phase liegt vor dem Start',
    satz: `Die stärksten Posts entstehen vor dem Kinostart (${f}-mal öfter als erwartet).`,
    tipp: 'Reichweite entsteht, bevor der Film läuft — der Vorlauf ist die wichtigste Zeit.',
  }),
  'lifecycle_stage:launch': () => ({
    titel: 'Rund um den Starttag wird es schwerer',
    satz: 'Posts rund um den Starttag bleiben öfter unter dem Kanal-Schnitt.',
    tipp: 'Ein eigener Aufhänger hilft hier mehr als Routine.',
  }),
  'lifecycle_stage:evergreen': () => ({
    titel: 'Ohne Anlass bleibt die Reichweite klein',
    satz: 'Posts ohne aktuellen Anlass erreichen am seltensten große Reichweite.',
    tipp: 'Ein Termin als Anlass — Start, Jubiläum, Heimkino — hebt die Chance.',
  }),
  'tone:humorous': () => ({
    titel: 'Humor zieht nicht von allein',
    satz: 'Lustige Posts bleiben öfter unter dem Kanal-Schnitt.',
    tipp: 'Humor trägt mit einem starken Aufhänger — als Selbstläufer selten.',
  }),
  'format:behind_the_scenes': (f) => ({
    titel: 'Blicke hinter die Kulissen wirken',
    satz: `Posts vom Set oder aus der Produktion liegen ${f}-mal öfter weit über dem Kanal-Schnitt.`,
    tipp: 'Nähe schlägt Hochglanz — echte Set-Momente wirken.',
  }),
  'format:clip': () => ({
    titel: 'Rohe Szenen-Clips fallen ab',
    satz: 'Ein roher Film-Ausschnitt bleibt öfter unter dem Kanal-Schnitt.',
    tipp: 'Mit Einstieg — Hook, Kontext oder Anlass — schneiden Clips besser ab.',
  }),
  'format_class:langform': (f) => ({
    titel: 'Lange Videos funktionieren',
    satz: `Videos über 90 Sekunden liegen ${f}-mal öfter weit über dem Kanal-Schnitt.`,
    tipp: 'Länge schreckt nicht ab — gute lange Videos finden ihr Publikum.',
  }),
};

export function werkstattEmpfehlung(dim, cell) {
  const faktor = cell.expected_breakout_rate > 0
    ? (cell.breakout_rate / cell.expected_breakout_rate).toFixed(1)
    : null;
  const vorlage = WERKSTATT_VORLAGEN[`${dim}:${cell.value}`];
  if (vorlage) return vorlage(faktor);
  const wert = WERT_LABEL[cell.value] || cell.value;
  if (cell.breakout_verdict === 'over') {
    return {
      titel: `${wert}: funktioniert gerade`,
      satz: `Posts mit diesem Merkmal liegen ${faktor ? `${faktor}-mal öfter` : 'öfter'} weit über dem Kanal-Schnitt.`,
    };
  }
  return {
    titel: `${wert}: funktioniert gerade nicht`,
    satz: 'Posts mit diesem Merkmal bleiben öfter unter dem Kanal-Schnitt.',
  };
}


function ReferenzThumb({ assetId }) {
  if (!assetId) return null;
  return (
    <img
      src={apiUrl(`/api/thumbnails/${assetId}`)}
      alt=""
      loading="lazy"
      onError={(e) => { e.currentTarget.style.display = 'none'; }}
      style={{ width: '72px', height: '44px', objectFit: 'cover', borderRadius: '6px', flexShrink: 0 }}
    />
  );
}

function ReferenzPosts({ dimension, value }) {
  const [refs, setRefs] = useState(null);
  useEffect(() => {
    let cancelled = false;
    endpoints.insightPatternExamples({ dimension, value, limit: 3 })
      .then((d) => { if (!cancelled) setRefs(d.examples || []); })
      .catch(() => { if (!cancelled) setRefs([]); });
    return () => { cancelled = true; };
  }, [dimension, value]);
  if (refs === null) {
    return <p style={{ margin: '0.5rem 0 0', fontSize: '0.8em', color: '#6b6b6b' }}>Lade Referenz-Posts …</p>;
  }
  if (refs.length === 0) return null;
  return (
    <div style={{ marginTop: '0.5rem', borderTop: '1px solid #eee6d8', paddingTop: '0.4rem' }}>
      {refs.map((ex) => (
        <div key={ex.post_url} style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', margin: '0.3rem 0' }}>
          <ReferenzThumb assetId={ex.asset_id} />
          <p style={{ margin: 0, fontSize: '0.8em', color: '#4a4a44' }}>
            <span style={{ fontWeight: 700 }}>{ex.lift}x</span>
            {' '}@{ex.channel_handle} ({ex.platform})
            {' · '}
            <a href={ex.post_url} target="_blank" rel="noreferrer" style={{ color: '#1f7a45' }}>
              Post ansehen
            </a>
          </p>
        </div>
      ))}
    </div>
  );
}

function EmpfehlungsKarte({ dim, cell }) {
  const farbe = QUOTE_COLOR_ON_CARD[cell.breakout_verdict];
  const { titel, satz, tipp } = werkstattEmpfehlung(dim, cell);
  // Volle Breite statt Kachel-Wand (Wolfs Lesbarkeits-Feedback
  // 25.08.): die Karten stehen untereinander in zwei klar
  // beschrifteten Gruppen — das Urteil steht EINMAL ueber der Gruppe,
  // nicht als Chip auf jeder Karte.
  return (
    <div
      className="card breakouts-card"
      style={{ padding: '0.85rem 1.25rem', borderLeft: `4px solid ${farbe}` }}
    >
      <p style={{ margin: '0 0 0.25rem', fontSize: '0.7em', textTransform: 'uppercase', letterSpacing: '0.05em', fontWeight: 600, color: '#6b6b6b' }}>
        {THEMA_LABEL[dim] || DIMENSION_LABEL[dim] || dim}
      </p>
      <p style={{ margin: '0 0 0.35rem', fontWeight: 700, fontSize: '1.05em' }}>{titel}</p>
      <p style={{ margin: 0, fontSize: '0.9em', color: '#4a4a44' }}>{satz}</p>
      {tipp && (
        <div style={{ margin: '0.5rem 0 0', background: '#e9f2ec', borderLeft: '3px solid #1f7a45', borderRadius: '0 8px 8px 0', padding: '0.45rem 0.7rem' }}>
          <p style={{ margin: 0, fontSize: '0.85em', color: '#1c3a2a' }}>
            <span style={{ fontWeight: 700 }}>Tipp: </span>{tipp}
          </p>
        </div>
      )}
      <p style={{ margin: '0.35rem 0 0', fontSize: '0.75em', color: '#6b6b6b' }}>
        Basis: {cell.sample_size} Posts von {cell.channel_count} Kanälen.
      </p>
      <ReferenzPosts dimension={dim} value={cell.value} />
    </div>
  );
}

function EmpfehlungsAnsicht({ dimensions }) {
  const befunde = staerksteBefunde(dimensions);
  if (befunde.length === 0) {
    return (
      <p style={{ color: '#b9c7bd', margin: 0, fontSize: '0.95em' }}>
        Noch keine belastbaren Empfehlungen — die Datenbasis wächst mit jedem Cron-Lauf.
      </p>
    );
  }
  // Erst alles, was gerade funktioniert, dann alles, was nicht — nicht
  // gemischt (Wolfs Feedback 25.08.: die Mischung war unlesbar).
  const gut = befunde.filter(({ cell }) => cell.breakout_verdict === 'over');
  const schlecht = befunde.filter(({ cell }) => cell.breakout_verdict !== 'over');
  const gruppe = (titelText, farbe, eintraege) => eintraege.length > 0 && (
    <div style={{ margin: '0 0 1.25rem' }}>
      <h3 style={{ color: farbe, margin: '0 0 0.5rem', fontSize: '1em', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
        {titelText}
      </h3>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.6rem' }}>
        {eintraege.map(({ dim, cell }) => (
          <EmpfehlungsKarte key={`${dim}:${cell.value}`} dim={dim} cell={cell} />
        ))}
      </div>
    </div>
  );
  return (
    <div>
      {gruppe('Das funktioniert gerade', '#7ee2a8', gut)}
      {gruppe('Das funktioniert gerade nicht', '#ffa294', schlecht)}
      <p style={{ color: '#b9c7bd', margin: '0.5rem 0 0', fontSize: '0.75em' }}>
        Abgeleitet aus gemessenen Mustern im eigenen Bestand — kein Wirkungsbeweis.
        Alle Zahlen und die Methodik stehen im Tab „Zahlen & Methode“.
      </p>
    </div>
  );
}

function CopyButton({ text }) {
  const [kopiert, setKopiert] = useState(false);
  const kopieren = () => {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(() => {
        setKopiert(true);
        setTimeout(() => setKopiert(false), 1500);
      }).catch(() => {});
    }
  };
  return (
    <button
      type="button"
      onClick={kopieren}
      title="In die Zwischenablage kopieren"
      style={{
        background: 'none', border: 'none', cursor: 'pointer',
        color: kopiert ? '#7ee2a8' : '#b9c7bd', fontSize: '0.8em', padding: '0 0.35rem',
      }}
    >
      {kopiert ? '✓ kopiert' : 'kopieren'}
    </button>
  );
}

export default function PatternsBlock() {
  const [aktiv, setAktiv] = useState(false);
  const [daten, setDaten] = useState(null);
  const [fehler, setFehler] = useState(false);
  const [briefing, setBriefing] = useState(null);
  const [titelBriefing, setTitelBriefing] = useState(null);
  const [alleZahlen, setAlleZahlen] = useState(false);
  const [tab, setTab] = useState('empfehlungen');

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
  const mitBefund = sichtbare.filter((n) => hatBefund(dimensionen[n]));
  const ohneBefund = sichtbare.filter((n) => !hatBefund(dimensionen[n]));

  return (
    <section style={{ background: '#1f4d4d', padding: '1.5rem 2rem 2rem 2rem', marginBottom: '1.5rem', borderRadius: '12px' }}>
      <p style={{ color: '#ffa294', fontSize: '0.75em', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.5rem', fontWeight: 600 }}>
        Trailer-Intelligence · letzte 90 Tage
      </p>
      <h2 style={{ color: 'white', marginTop: 0, marginBottom: '0.75rem' }}>
        Was gerade funktioniert — und was nicht
      </h2>
      <p style={{ color: '#c8d6cc', margin: '0 0 1rem', fontSize: '0.9em' }}>
        Welche Merkmale gehen mit überdurchschnittlicher Reichweite einher? Jeder Post wird
        an seinem <strong>eigenen Kanal-Schnitt</strong> gemessen, damit große und kleine
        Kanäle vergleichbar sind. Die Breakout-Quote sagt, wie oft ein Merkmal Ausreißer
        (mindestens 2x Kanal-Schnitt) produziert — verglichen mit der Quote, die seine
        Plattform-Mischung erwarten ließe.
      </p>
      <details style={{ margin: '0 0 1rem' }}>
        <summary style={{ color: '#b9c7bd', fontSize: '0.85em', cursor: 'pointer' }}>
          Wie lese ich das?
        </summary>
        <div style={{ color: '#c8d6cc', fontSize: '0.85em', margin: '0.5rem 0 0', lineHeight: 1.5 }}>
          <p style={{ margin: '0 0 0.5rem' }}>
            <strong>Breakout-Quote:</strong> Anteil der Posts eines Merkmals, die mindestens
            das Doppelte des üblichen Schnitts ihres eigenen Kanals erreicht haben.
            <strong> erwartet:</strong> die Quote, die allein aus der Plattform-Mischung
            dieser Posts zu erwarten wäre — YouTube produziert generell mehr Ausreißer
            als TikTok, der Vergleich rechnet das heraus.
          </p>
          <p style={{ margin: '0 0 0.5rem' }}>
            <strong>Balken:</strong> die Füllung ist die gemessene Quote, der Strich die
            erwartete — Füllung rechts vom Strich heißt besser als erwartet.
          </p>
          <p style={{ margin: 0 }}>
            <strong>Befund:</strong> „läuft über/unter Schnitt“ steht erst dort, wo die
            Abweichung statistisch belastbar ist (genug Posts aus genug Kanälen, Signal
            deutlich über dem Zufallsrauschen). Klick auf eine Zeile zeigt die stärksten
            Beispiel-Posts dahinter.
          </p>
        </div>
      </details>
      {!fehler && daten !== null && (
        <div style={{ display: 'flex', gap: '1.25rem', margin: '0 0 1rem', borderBottom: '1px solid rgba(255,255,255,0.25)' }}>
          {[['empfehlungen', 'Empfehlungen'], ['zahlen', 'Zahlen & Methode']].map(([id, label]) => (
            <button
              key={id}
              type="button"
              onClick={() => setTab(id)}
              style={{
                background: 'none', border: 'none', cursor: 'pointer',
                padding: '0 0 0.5rem', fontSize: '0.95em',
                color: tab === id ? 'white' : '#b9c7bd',
                fontWeight: tab === id ? 700 : 400,
                borderBottom: tab === id ? '2px solid #ffa294' : '2px solid transparent',
                marginBottom: '-1px',
              }}
            >
              {label}
            </button>
          ))}
        </div>
      )}
      {!fehler && daten !== null && tab === 'empfehlungen' && (
        <EmpfehlungsAnsicht dimensions={dimensionen} />
      )}

      {fehler && (
        <p style={{ color: '#b9c7bd', margin: 0, fontSize: '0.95em' }}>
          Muster momentan nicht verfügbar. Bitte später erneut versuchen.
        </p>
      )}
      {!fehler && daten === null && (
        <p style={{ color: '#b9c7bd', margin: 0, fontSize: '0.95em' }}>Lade Muster …</p>
      )}
      {!fehler && daten !== null && tab === 'zahlen' && sichtbare.length === 0 && (
        <p style={{ color: '#b9c7bd', margin: 0, fontSize: '0.95em' }}>
          Noch kein Muster mit ausreichender Stichprobe. Die Datenbasis wächst mit jedem
          Cron-Lauf.
        </p>
      )}
      {!fehler && daten !== null && tab === 'zahlen' && <BefundKarten dimensions={dimensionen} />}
      {!fehler && daten !== null && tab === 'zahlen' && <BewegungsKarten dimensions={dimensionen} />}
      {!fehler && daten !== null && tab === 'zahlen' && ohneBefund.length > 0 && (
        <p style={{ textAlign: 'right', margin: '0 0 0.5rem' }}>
          <button
            type="button"
            onClick={() => setAlleZahlen((v) => !v)}
            style={{
              background: 'none', border: 'none', padding: 0,
              color: '#7ee2a8', fontSize: '0.85em', cursor: 'pointer',
              textDecoration: 'underline',
            }}
          >
            {alleZahlen
              ? 'Nur Befunde zeigen'
              : `Alle Zahlen zeigen (${ohneBefund.length} weitere Dimensionen)`}
          </button>
        </p>
      )}
      {!fehler && tab === 'zahlen' && (alleZahlen ? sichtbare : mitBefund).map((name) => (
        <ZellenTabelle key={name} name={name} cells={dimensionen[name]} />
      ))}
      {!fehler && tab === 'zahlen' && !alleZahlen && ohneBefund.map((name) => (
        <EingeklappteDimension key={name} name={name} cells={dimensionen[name]} />
      ))}
      {!fehler && daten !== null && tab === 'zahlen' && (
        <p style={{ color: '#b9c7bd', margin: '0.5rem 0 0', fontSize: '0.8em' }}>
          Datenbasis: {daten.posts_with_baseline} Posts mit Kanal-Baseline
          {' '}({daten.channels_covered} Kanäle, Fenster {daten.window_days} Tage).
        </p>
      )}
      {!fehler && daten !== null && tab === 'zahlen' && (daten.notes || []).length > 0 && (
        <ul style={{ color: '#b9c7bd', margin: '0.5rem 0 0', paddingLeft: '1.2rem', fontSize: '0.8em' }}>
          {daten.notes.map((note) => <li key={note}>{note}</li>)}
        </ul>
      )}

      {tab === 'empfehlungen' && (
        <>
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
        </>
      )}
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
