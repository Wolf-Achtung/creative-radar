import React, { useEffect, useState } from 'react';
import { endpoints } from './api/client';
import { DIMENSION_LABEL } from './PatternsBlock';

// Post-Check (Roadmap-Ausbau, 25.08.2026) — die Blickrichtungs-Umkehr:
// statt montags zu lesen, was gefehlt hat, prueft das Team den Entwurf
// VOR dem Posten. Caption einfuegen, optional Laenge/Cover/Format
// angeben, und jede Angabe wird gegen die aktuellen Befunde gehalten —
// dieselben Regeln wie das Muster-Panel (POST /api/insights/post-check,
// deterministisch, kein Modell-Call).
//
// Feature-Flag-Gate wie ueberall: der Block fragt /api/health
// (FEATURE_POST_CHECK_ENABLED) und rendert ohne Flag NICHTS.

const FORMAT_OPTIONEN = [
  ['', 'Weiß ich nicht'],
  ['trailer', 'Trailer'], ['teaser', 'Teaser'], ['clip', 'Clip'],
  ['behind_the_scenes', 'Behind-the-Scenes'], ['interview', 'Interview'],
  ['short', 'Short'], ['compilation', 'Compilation'], ['promo', 'Promo'],
];
const TON_OPTIONEN = [
  ['', 'Weiß ich nicht'],
  ['energetic', 'Energetisch'], ['emotional', 'Emotional'],
  ['humorous', 'Humorvoll'], ['suspenseful', 'Spannungsgetrieben'],
  ['informative', 'Informativ'], ['inspirational', 'Inspirierend'],
  ['edgy', 'Edgy'], ['neutral', 'Neutral'],
];

const BEFUND_ANZEIGE = {
  gut: { zeichen: '✓', farbe: '#1f7a45' },
  achtung: { zeichen: '!', farbe: '#b03d2e' },
  neutral: { zeichen: '·', farbe: '#6b6b6b' },
  kein_befund: { zeichen: '?', farbe: '#6b6b6b' },
};

const feldStyle = {
  background: '#173d3d', color: 'white', border: '1px solid rgba(255,255,255,0.3)',
  borderRadius: '6px', padding: '0.35rem 0.5rem', fontSize: '0.9em',
};

function CheckZeile({ check }) {
  const anzeige = BEFUND_ANZEIGE[check.befund] || BEFUND_ANZEIGE.neutral;
  return (
    <div style={{ display: 'flex', gap: '0.6rem', alignItems: 'baseline', padding: '0.35rem 0', borderBottom: '1px solid #eee6d8' }}>
      <span
        style={{
          color: 'white', background: anzeige.farbe, borderRadius: '50%',
          width: '1.3rem', height: '1.3rem', display: 'inline-flex',
          alignItems: 'center', justifyContent: 'center', fontWeight: 700,
          fontSize: '0.8em', flexShrink: 0,
        }}
      >
        {anzeige.zeichen}
      </span>
      <div>
        <p style={{ margin: 0, fontSize: '0.9em' }}>
          <span style={{ color: '#6b6b6b', fontSize: '0.85em' }}>
            {DIMENSION_LABEL[check.dimension] || check.dimension}:{' '}
          </span>
          {check.satz}
        </p>
        {check.tipp && (
          <p style={{ margin: '0.15rem 0 0', fontSize: '0.85em', color: '#1f7a45', fontWeight: 600 }}>
            {check.tipp}
          </p>
        )}
      </div>
    </div>
  );
}

export default function PostCheckBlock() {
  const [aktiv, setAktiv] = useState(false);
  const [caption, setCaption] = useState('');
  const [dauer, setDauer] = useState('');
  const [titelImBild, setTitelImBild] = useState('');
  const [format, setFormat] = useState('');
  const [tonfall, setTonfall] = useState('');
  const [ergebnis, setErgebnis] = useState(null);
  const [fehler, setFehler] = useState(null);
  const [laeuft, setLaeuft] = useState(false);

  useEffect(() => {
    let cancelled = false;
    endpoints.health().then((health) => {
      if (!cancelled && health?.features?.post_check) setAktiv(true);
    }).catch(() => {});
    return () => { cancelled = true; };
  }, []);

  if (!aktiv) return null;

  const pruefen = async () => {
    setLaeuft(true);
    setFehler(null);
    try {
      const daten = await endpoints.postCheck({
        caption,
        duration_seconds: dauer ? Number(dauer) : null,
        titel_im_bild: titelImBild === '' ? null : titelImBild === 'ja',
        format: format || null,
        tonfall: tonfall || null,
      });
      setErgebnis(daten);
    } catch (err) {
      setFehler(err.message || String(err));
      setErgebnis(null);
    } finally {
      setLaeuft(false);
    }
  };

  return (
    <section style={{ background: '#1f4d4d', padding: '1.5rem 2rem 2rem 2rem', marginBottom: '1.5rem', borderRadius: '12px' }}>
      <p style={{ color: '#ffa294', fontSize: '0.75em', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.5rem', fontWeight: 600 }}>
        Post-Check · vor dem Posten
      </p>
      <h2 style={{ color: 'white', marginTop: 0, marginBottom: '0.75rem' }}>
        Entwurf prüfen, bevor er rausgeht
      </h2>
      <p style={{ color: '#c8d6cc', margin: '0 0 1rem', fontSize: '0.9em' }}>
        Caption einfügen, optional Länge und Cover angeben — der Check hält jede
        Angabe gegen das, was gerade funktioniert. Dieselben Regeln wie im
        Muster-Panel, sofort und ohne KI-Kosten.
      </p>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.6rem', maxWidth: '640px' }}>
        <textarea
          value={caption}
          onChange={(e) => setCaption(e.target.value)}
          placeholder="Caption-Entwurf hier einfügen …"
          rows={4}
          aria-label="Caption-Entwurf"
          style={{ ...feldStyle, resize: 'vertical', fontFamily: 'inherit' }}
        />
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.75rem' }}>
          <label style={{ display: 'flex', flexDirection: 'column', gap: '0.2rem' }}>
            <span style={{ color: '#b9c7bd', fontSize: '0.7em', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Videolänge (Sekunden)</span>
            <input
              type="number"
              min="1"
              value={dauer}
              onChange={(e) => setDauer(e.target.value)}
              placeholder="z. B. 45"
              style={{ ...feldStyle, width: '8rem' }}
            />
          </label>
          <label style={{ display: 'flex', flexDirection: 'column', gap: '0.2rem' }}>
            <span style={{ color: '#b9c7bd', fontSize: '0.7em', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Titel im Cover?</span>
            <select value={titelImBild} onChange={(e) => setTitelImBild(e.target.value)} style={feldStyle}>
              <option value="">Weiß ich nicht</option>
              <option value="ja">Ja</option>
              <option value="nein">Nein</option>
            </select>
          </label>
          <label style={{ display: 'flex', flexDirection: 'column', gap: '0.2rem' }}>
            <span style={{ color: '#b9c7bd', fontSize: '0.7em', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Format</span>
            <select value={format} onChange={(e) => setFormat(e.target.value)} style={feldStyle}>
              {FORMAT_OPTIONEN.map(([wert, label]) => <option key={wert} value={wert}>{label}</option>)}
            </select>
          </label>
          <label style={{ display: 'flex', flexDirection: 'column', gap: '0.2rem' }}>
            <span style={{ color: '#b9c7bd', fontSize: '0.7em', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Tonfall</span>
            <select value={tonfall} onChange={(e) => setTonfall(e.target.value)} style={feldStyle}>
              {TON_OPTIONEN.map(([wert, label]) => <option key={wert} value={wert}>{label}</option>)}
            </select>
          </label>
        </div>
        <div>
          <button
            type="button"
            onClick={pruefen}
            disabled={laeuft || !caption.trim()}
            style={{
              background: '#ffa294', color: '#1c1c1a', border: 'none', borderRadius: '8px',
              padding: '0.5rem 1.25rem', fontWeight: 700, fontSize: '0.95em',
              cursor: laeuft || !caption.trim() ? 'default' : 'pointer',
              opacity: laeuft || !caption.trim() ? 0.6 : 1,
            }}
          >
            {laeuft ? 'Prüft …' : 'Entwurf prüfen'}
          </button>
        </div>
      </div>
      {fehler && (
        <p style={{ color: '#ffa294', margin: '0.75rem 0 0', fontSize: '0.9em' }}>
          Der Check ist gerade nicht möglich: {fehler}
        </p>
      )}
      {ergebnis && (
        <div className="card" style={{ marginTop: '1rem', padding: '0.75rem 1rem', maxWidth: '640px' }}>
          <p style={{ margin: '0 0 0.5rem', fontWeight: 700, fontSize: '0.95em' }}>
            {ergebnis.zusammenfassung.achtung === 0
              ? 'Sieht gut aus — kein Punkt spricht gerade dagegen.'
              : `${ergebnis.zusammenfassung.achtung} ${ergebnis.zusammenfassung.achtung === 1 ? 'Punkt spricht' : 'Punkte sprechen'} gerade dagegen.`}
          </p>
          {ergebnis.checks.map((check) => (
            <CheckZeile key={`${check.dimension}:${check.wert}`} check={check} />
          ))}
          <p style={{ margin: '0.5rem 0 0', fontSize: '0.75em', color: '#6b6b6b' }}>
            Gemessen an {ergebnis.basis.posts} Posts aus {ergebnis.basis.kanaele} Kanälen
            der letzten {ergebnis.window_days} Tage. Beschreibt, was gerade läuft —
            kein Wirkungsbeweis.
          </p>
        </div>
      )}
    </section>
  );
}
