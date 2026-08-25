import React, { useEffect, useState } from 'react';
import { apiUrl, endpoints } from './api/client';
import { formatDate, formatLift } from './format';
import { DIMENSION_LABEL, WERT_LABEL } from './PatternsBlock';

// Referenz-Suche (Roadmap Schritt 1, 25.08.2026) — die Moodboard-
// Werkbank: Facetten-Filter ueber die analysierten Posts, Ergebnis als
// Referenz-Grid mit Bild, Lift, Kanal und Link. Daten aus
// GET /api/insights/referenzen; Lift und Zugehoerigkeit sind exakt die
// des Muster-Panels (gleiche Berechnung in trailer_patterns).
//
// Feature-Flag-Gate wie PatternsBlock: der Block fragt /api/health, ob
// diese Umgebung das Feature anbietet (FEATURE_REFERENZ_SUCHE_ENABLED —
// in Staging an, in Production erst nach Abnahme). Meldet health
// nichts, rendert der Block NICHTS.
//
// Facetten-Optionen kommen aus der facetten_zaehlung der jeweils
// AKTUELLEN Treffermenge — die Selects zeigen also nur Werte, die von
// hier aus noch Treffer haben (plus die eigene Auswahl, sonst liesse
// sie sich nicht mehr abwaehlen).

// Hauptfilter immer sichtbar; der Rest hinter "Mehr Filter".
const HAUPT_FACETTEN = ['genre', 'format', 'tone', 'duration_bucket', 'cover_titel', 'cover_kinetik'];
const WEITERE_FACETTEN = [
  'format_class', 'lifecycle_stage', 'music_kind',
  'caption_frage', 'caption_cta', 'caption_laenge', 'caption_hashtags',
];

const PLATFORM_LABEL = { instagram: 'Instagram', tiktok: 'TikTok', youtube: 'YouTube' };

const LIFT_OPTIONEN = [
  { wert: '', label: 'Alle' },
  { wert: '1.5', label: 'ab 1,5x Kanal-Schnitt' },
  { wert: '2', label: 'ab 2x Kanal-Schnitt' },
  { wert: '3', label: 'ab 3x Kanal-Schnitt' },
];

const FENSTER_OPTIONEN = [
  { wert: 30, label: 'Letzte 30 Tage' },
  { wert: 90, label: 'Letzte 90 Tage' },
  { wert: 180, label: 'Letzte 180 Tage' },
  { wert: 365, label: 'Letztes Jahr' },
];

const selectStyle = {
  background: '#173d3d', color: 'white', border: '1px solid rgba(255,255,255,0.3)',
  borderRadius: '6px', padding: '0.3rem 0.4rem', fontSize: '0.85em', maxWidth: '210px',
};

function FacettenSelect({ dimension, auswahl, optionen, onChange }) {
  // Die eigene Auswahl bleibt waehlbar, auch wenn sie in der aktuellen
  // Zaehlung fehlt (sonst koennte man sie nicht mehr abwaehlen).
  const werte = optionen.map((o) => o.wert);
  const mitAuswahl = auswahl && !werte.includes(auswahl)
    ? [{ wert: auswahl, anzahl: 0 }, ...optionen]
    : optionen;
  if (!mitAuswahl.length && !auswahl) return null;
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: '0.2rem' }}>
      <span style={{ color: '#b9c7bd', fontSize: '0.7em', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
        {DIMENSION_LABEL[dimension] || dimension}
      </span>
      <select value={auswahl || ''} onChange={(e) => onChange(dimension, e.target.value)} style={selectStyle}>
        <option value="">Alle</option>
        {mitAuswahl.map((o) => (
          <option key={o.wert} value={o.wert}>
            {(WERT_LABEL[o.wert] || o.wert)}{o.anzahl ? ` (${o.anzahl})` : ''}
          </option>
        ))}
      </select>
    </label>
  );
}

function TrefferKarte({ treffer }) {
  // Kopfzeile statt Overlay (Wolfs Layout-Feedback 25.08.): das
  // Lift-Badge lag absolut ueber dem Bildbereich und verdeckte ohne
  // Bild den Kanalnamen. Jetzt eine feste Zeile: Lift · @Kanal ·
  // Plattform — das Bild kommt darunter, falls es eines gibt.
  return (
    <a
      href={treffer.post_url}
      target="_blank"
      rel="noreferrer"
      className="card"
      style={{
        padding: 0, overflow: 'hidden', textDecoration: 'none',
        color: 'inherit', display: 'flex', flexDirection: 'column',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', padding: '0.55rem 0.75rem', borderBottom: '1px solid #eee6d8' }}>
        <span
          style={{
            background: treffer.lift >= 1.5 ? '#1f7a45' : '#6b6b6b', color: 'white',
            borderRadius: '6px', padding: '0.1rem 0.5rem', fontWeight: 700, fontSize: '0.85em', flexShrink: 0,
          }}
        >
          {formatLift(treffer.lift)}
        </span>
        <span style={{ fontWeight: 700, fontSize: '0.85em', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          @{treffer.channel_handle}
        </span>
        <span style={{ marginLeft: 'auto', color: '#6b6b6b', fontSize: '0.75em', flexShrink: 0 }}>
          {PLATFORM_LABEL[treffer.platform] || treffer.platform}
        </span>
      </div>
      {treffer.asset_id && (
        <img
          src={apiUrl(`/api/thumbnails/${treffer.asset_id}`)}
          alt=""
          loading="lazy"
          onError={(e) => { e.currentTarget.style.display = 'none'; }}
          style={{ width: '100%', height: '140px', objectFit: 'cover', display: 'block' }}
        />
      )}
      <div style={{ padding: '0.6rem 0.75rem 0.7rem', display: 'flex', flexDirection: 'column', gap: '0.3rem', flex: 1 }}>
        {(treffer.titel || treffer.genre) && (
          <p style={{ margin: 0, fontSize: '0.85em', fontWeight: 700, color: '#1c1c1a' }}>
            {treffer.titel || treffer.genre}
            {treffer.titel && treffer.genre && (
              <span style={{ color: '#6b6b6b', fontWeight: 400 }}> · {treffer.genre}</span>
            )}
          </p>
        )}
        {treffer.caption && (
          <p
            style={{
              margin: 0, fontSize: '0.78em', color: '#4a4a44', lineHeight: 1.4,
              display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden',
            }}
          >
            {treffer.caption}
          </p>
        )}
        <p style={{ margin: 'auto 0 0', paddingTop: '0.2rem', fontSize: '0.75em', color: '#6b6b6b' }}>
          {treffer.detected_at ? `${formatDate(treffer.detected_at)} · ` : ''}
          <span style={{ color: '#1f7a45', fontWeight: 600 }}>Post ansehen →</span>
        </p>
      </div>
    </a>
  );
}

export default function ReferenzSucheBlock() {
  const [aktiv, setAktiv] = useState(false);
  const [daten, setDaten] = useState(null);
  const [fehler, setFehler] = useState(false);
  const [facetten, setFacetten] = useState({});
  const [platform, setPlatform] = useState('');
  const [minLift, setMinLift] = useState('');
  const [windowDays, setWindowDays] = useState(90);
  // 8 auf einen Blick statt 24 (Wolfs Feedback 25.08.: zu viel auf
  // einmal). "Mehr anzeigen" holt jeweils 8 dazu, bis 56 erreicht
  // sind — wer mehr braucht, engt mit den Filtern ein.
  const [anzahl, setAnzahl] = useState(8);

  useEffect(() => {
    let cancelled = false;
    endpoints.health().then((health) => {
      if (!cancelled && health?.features?.referenz_suche) setAktiv(true);
    }).catch(() => {});
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (!aktiv) return undefined;
    let cancelled = false;
    endpoints.referenzSuche({
      windowDays,
      platform: platform || undefined,
      facetten,
      minLift: minLift ? Number(minLift) : undefined,
      limit: anzahl,
    }).then((d) => {
      if (!cancelled) { setDaten(d); setFehler(false); }
    }).catch(() => {
      if (!cancelled) setFehler(true);
    });
    return () => { cancelled = true; };
  }, [aktiv, facetten, platform, minLift, windowDays, anzahl]);

  if (!aktiv) return null;

  const setzeFacette = (dimension, wert) => {
    setFacetten((alt) => {
      const neu = { ...alt };
      if (wert) neu[dimension] = wert; else delete neu[dimension];
      return neu;
    });
  };
  const zaehlung = daten?.facetten_zaehlung || {};
  const filterAktiv = Object.keys(facetten).length > 0 || platform || minLift;

  return (
    <section style={{ background: '#1f4d4d', padding: '1.5rem 2rem 2rem 2rem', marginBottom: '1.5rem', borderRadius: '12px' }}>
      <p style={{ color: '#ffa294', fontSize: '0.75em', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.5rem', fontWeight: 600 }}>
        Referenz-Suche · Werkbank
      </p>
      <h2 style={{ color: 'white', marginTop: 0, marginBottom: '0.75rem' }}>
        Referenzen — Posts finden, die funktioniert haben
      </h2>
      <p style={{ color: '#c8d6cc', margin: '0 0 1rem', fontSize: '0.9em' }}>
        Filtere die analysierten Posts nach Genre, Machart und Wirkung. Der Lift misst
        jeden Post an seinem <strong>eigenen Kanal-Schnitt</strong> — 2x heißt: doppelt so
        stark wie für diesen Kanal üblich. Sortiert nach Lift, stärkste zuerst.
      </p>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.75rem', margin: '0 0 0.75rem', alignItems: 'flex-end' }}>
        <label style={{ display: 'flex', flexDirection: 'column', gap: '0.2rem' }}>
          <span style={{ color: '#b9c7bd', fontSize: '0.7em', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Zeitraum</span>
          <select value={windowDays} onChange={(e) => setWindowDays(Number(e.target.value))} style={selectStyle}>
            {FENSTER_OPTIONEN.map((o) => <option key={o.wert} value={o.wert}>{o.label}</option>)}
          </select>
        </label>
        <label style={{ display: 'flex', flexDirection: 'column', gap: '0.2rem' }}>
          <span style={{ color: '#b9c7bd', fontSize: '0.7em', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Plattform</span>
          <select value={platform} onChange={(e) => setPlatform(e.target.value)} style={selectStyle}>
            <option value="">Alle</option>
            {Object.entries(PLATFORM_LABEL).map(([wert, label]) => <option key={wert} value={wert}>{label}</option>)}
          </select>
        </label>
        <label style={{ display: 'flex', flexDirection: 'column', gap: '0.2rem' }}>
          <span style={{ color: '#b9c7bd', fontSize: '0.7em', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Wirkung</span>
          <select value={minLift} onChange={(e) => setMinLift(e.target.value)} style={selectStyle}>
            {LIFT_OPTIONEN.map((o) => <option key={o.wert} value={o.wert}>{o.label}</option>)}
          </select>
        </label>
        {HAUPT_FACETTEN.map((dimension) => (
          <FacettenSelect
            key={dimension}
            dimension={dimension}
            auswahl={facetten[dimension]}
            optionen={zaehlung[dimension] || []}
            onChange={setzeFacette}
          />
        ))}
      </div>
      <details style={{ margin: '0 0 1rem' }}>
        <summary style={{ color: '#b9c7bd', fontSize: '0.85em', cursor: 'pointer' }}>Mehr Filter</summary>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.75rem', margin: '0.6rem 0 0', alignItems: 'flex-end' }}>
          {WEITERE_FACETTEN.map((dimension) => (
            <FacettenSelect
              key={dimension}
              dimension={dimension}
              auswahl={facetten[dimension]}
              optionen={zaehlung[dimension] || []}
              onChange={setzeFacette}
            />
          ))}
        </div>
      </details>
      {fehler && (
        <p style={{ color: '#b9c7bd', margin: 0, fontSize: '0.95em' }}>
          Referenz-Suche momentan nicht verfügbar. Bitte später erneut versuchen.
        </p>
      )}
      {!fehler && daten === null && (
        <p style={{ color: '#b9c7bd', margin: 0, fontSize: '0.95em' }}>Lade Referenzen …</p>
      )}
      {!fehler && daten !== null && (
        <>
          <p style={{ color: '#b9c7bd', margin: '0 0 0.75rem', fontSize: '0.85em' }}>
            {daten.gesamt === 0
              ? 'Keine Treffer mit diesen Filtern.'
              : `${daten.gesamt} Treffer${daten.gesamt > daten.treffer.length ? `, die stärksten ${daten.treffer.length} angezeigt` : ''}.`}
            {filterAktiv && (
              <button
                type="button"
                onClick={() => { setFacetten({}); setPlatform(''); setMinLift(''); setAnzahl(8); }}
                style={{
                  background: 'none', border: 'none', color: '#ffa294', cursor: 'pointer',
                  fontSize: '1em', padding: 0, marginLeft: '0.5rem', textDecoration: 'underline',
                }}
              >
                Filter zurücksetzen
              </button>
            )}
          </p>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(230px, 1fr))', gap: '0.75rem' }}>
            {daten.treffer.map((treffer) => (
              <TrefferKarte key={treffer.post_url} treffer={treffer} />
            ))}
          </div>
          {daten.gesamt > daten.treffer.length && anzahl < 56 && (
            <div style={{ marginTop: '0.75rem' }}>
              <button
                type="button"
                onClick={() => setAnzahl(anzahl + 8)}
                style={{
                  background: 'none', border: '1px solid rgba(255,255,255,0.4)', color: 'white',
                  borderRadius: '8px', padding: '0.4rem 1rem', cursor: 'pointer', fontSize: '0.9em',
                }}
              >
                Mehr anzeigen
              </button>
            </div>
          )}
        </>
      )}
    </section>
  );
}
