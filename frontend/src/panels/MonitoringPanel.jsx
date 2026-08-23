import React, { useEffect, useState } from 'react';
import { endpoints } from '../api/client';
import {
  clip,
  formatCronRunStatus,
  formatDateTime,
  formatMultiplier,
  formatNumber,
  formatPct1,
  formatUsdCents,
  formatUsdMillicents,
} from '../format';
import { Section } from '../components/Section';
import { UsageSection } from '../components/UsageSection';
import { DIMENSION_LABEL, WERT_LABEL } from '../PatternsBlock';

// Platin 2 (2026-07-13) — Admin Ops-Dashboard. Reine Anzeige-Komponente
// auf Basis bereits bestehender Endpoints (cost-summary, die drei
// */budget-status-Routen, cron/runs), die bislang nur per curl geprüft
// werden konnten. Lädt erst beim Öffnen des Tabs (kein Extra-Call auf
// jedem Admin-Seitenaufruf), eigener Fehlerzustand statt globalem
// error/message-State, damit ein Ladefehler hier nicht die restliche
// Admin-UI blockiert.
const BUDGET_CARDS = [
  { key: 'apify', label: 'Apify (Scraping)', fetcher: 'apifyBudgetStatus' },
  { key: 'anthropic', label: 'Anthropic (Briefs, Vision-Fallback)', fetcher: 'anthropicBudgetStatus' },
  { key: 'openai', label: 'OpenAI (Vision, Caption)', fetcher: 'openaiBudgetStatus' },
];

function BudgetStatusCard({ label, status }) {
  if (!status) return (
    <div className="ops-budget-card ops-budget-card-loading">
      <h4>{label}</h4>
      <p className="muted small">Lädt …</p>
    </div>
  );
  const level = status.hard_cap_exceeded ? 'hard' : status.soft_warn_exceeded ? 'soft' : 'ok';
  return (
    <div className={`ops-budget-card ops-budget-card-${level}`}>
      <h4>{label}</h4>
      <p className="ops-budget-value">{formatUsdCents(status.spent_usd_cents)} <span className="muted">/ {formatUsdCents(status.budget_usd_cents)}</span></p>
      <div className="ops-budget-bar-track">
        <div className="ops-budget-bar" style={{ width: `${Math.min(Math.round((status.pct_used || 0) * 100), 100)}%` }} />
      </div>
      <p className="muted small">
        {formatPct1(status.pct_used)} genutzt
        {status.hard_cap_exceeded && status.enforced && ' · Limit erreicht, weitere Läufe werden abgebrochen'}
        {status.hard_cap_exceeded && !status.enforced && ' · Limit erreicht (nicht erzwungen)'}
        {!status.hard_cap_exceeded && status.soft_warn_exceeded && ' · Warnschwelle erreicht'}
      </p>
    </div>
  );
}


// Playbook-Test-Mail (21.08.2026): Wolfs Pruef-Weg ohne curl. Der
// Button ruft POST /api/admin/playbook-mail/test und uebersetzt die
// Antwort in einen Klartext-Satz. Auf Staging warnt er, dass der
// Mailer abgeschaltet ist (DISABLE_EMAILS) — der echte Test gehoert
// nach Produktion.

// Text-Bausteine generieren (21.08.2026): der Review-Weg per Klick.
// Jeder Lauf ist ein echter Opus-Call (~5-10 Cent, zahlt in den
// Anthropic-Monatsdeckel ein); Leerlauf ohne belastbares Muster ist
// kostenfrei (model='none'). Review-Umbau am selben Tag: die Sektion
// zeigt das Ergebnis direkt hier an — nach der Generierung laedt sie
// GET /pattern-briefing/latest nach, die "Letztes Ergebnis"-Buttons
// holen den Stand jederzeit ohne neuen (kostenden) Lauf. Vorher war
// der Output in Production unsichtbar, solange das Nutzer-Panel hinter
// FEATURE_TRAILER_INTELLIGENCE_ENABLED steckt.

// Wir-Segment Schritt 1 (21.08.2026): empfohlen → gemacht → gewirkt.
// Liest GET /api/admin/wir-segment — je MACHEN-Empfehlung des Muster-
// Berichts, wie viele Posts der eigenen Kanäle (in Quellen →
// „Wir-Kanäle markieren") das Muster spielen und wie sie laufen.
export function WirSegmentSection() {
  const [daten, setDaten] = useState(null);
  const [fehler, setFehler] = useState(null);

  useEffect(() => {
    let cancelled = false;
    endpoints.adminWirSegment()
      .then((antwort) => { if (!cancelled) setDaten(antwort); })
      .catch((err) => { if (!cancelled) setFehler(String(err?.message || err)); });
    return () => { cancelled = true; };
  }, []);

  return (
    <Section title="Wir-Segment" kicker="Empfohlen → gemacht → gewirkt">
      {fehler && <p className="error">Konnte das Wir-Segment nicht laden: {fehler}</p>}
      {!fehler && !daten && <p className="muted small">Lädt …</p>}
      {daten && daten.note && <p className="muted">{daten.note}</p>}
      {daten && !daten.note && (
        <>
          <p className="muted small">
            {daten.own_project_titles || 0} Wir-Projekte · {daten.own_channels} eigene
            Kanäle · {daten.eigene_posts_im_fenster} eigene
            Posts im {daten.window_days}-Tage-Fenster. „Gewirkt“ ist der
            Median-Lift eurer eigenen Posts in diesem Muster — daneben der
            Wert des Gesamtbestands zum Vergleich.
          </p>
          {daten.zeilen.length === 0 && (
            <p className="muted">Aktuell gibt es keine MACHEN-Empfehlung im Muster-Bericht.</p>
          )}
          {daten.zeilen.map((zeile) => {
            const dim = DIMENSION_LABEL[zeile.dimension] || zeile.dimension;
            const wert = WERT_LABEL[zeile.value] || zeile.value;
            return (
              <p key={`${zeile.dimension}:${zeile.value}`} style={{ margin: '0.25rem 0' }}>
                <strong>{dim}: {wert}</strong>
                {' — '}
                {zeile.gemacht === 0
                  ? 'von uns noch nicht gespielt.'
                  : `von uns ${zeile.gemacht}× gemacht, Median-Lift ${zeile.gewirkt_median_lift ?? '—'}x`}
                <span className="muted small">
                  {' '}(Gesamtbestand: {zeile.empfohlen_median_lift}x
                  {zeile.empfohlen_breakout_z != null ? `, z=${zeile.empfohlen_breakout_z}` : ''})
                </span>
              </p>
            );
          })}
        </>
      )}
    </Section>
  );
}

// Kampagnen-Timing (22.08.2026): wann startet welcher Kanal die
// Trailer-Welle relativ zum Release. Rechnet ausschliesslich auf
// vorhandenen Daten (Release-Dates + Post-Zeitstempel + Zuordnung) —
// die Auswertung waechst mit der Zuordnungs-Arbeit in der Queue.
export function KampagnenTimingSection() {
  const [daten, setDaten] = useState(null);
  const [fehler, setFehler] = useState(null);

  useEffect(() => {
    let cancelled = false;
    endpoints.kampagnenTiming()
      .then((antwort) => { if (!cancelled) setDaten(antwort); })
      .catch((err) => { if (!cancelled) setFehler(String(err?.message || err)); });
    return () => { cancelled = true; };
  }, []);

  return (
    <Section title="Kampagnen-Timing" kicker="Wann startet die Trailer-Welle?">
      {fehler && <p className="error">Konnte das Kampagnen-Timing nicht laden: {fehler}</p>}
      {!fehler && !daten && <p className="muted small">Lädt …</p>}
      {daten && daten.note && <p className="muted">{daten.note}</p>}
      {daten && !daten.note && (
        <>
          <p className="muted small">
            {daten.titel_ausgewertet} Titel mit Release-Datum und genug
            zugeordneten Posts · {daten.posts_ausgewertet} Posts im Band
            von 26 Wochen vor bis 8 Wochen nach Release. Positive Tage =
            vor dem Kinostart. Beschreibt das Markt-Verhalten — kein
            Beweis, dass früher besser ist.
          </p>
          {daten.kanaele.length > 0 && (
            <table>
              <thead>
                <tr>
                  <th>Kanal</th><th>Markt</th><th>Titel</th><th>Posts</th>
                  <th>Kampagnenstart (Median)</th>
                </tr>
              </thead>
              <tbody>
                {daten.kanaele.map((k) => (
                  <tr key={`${k.handle}:${k.market}`}>
                    <td>@{k.handle}</td>
                    <td>{k.market}</td>
                    <td>{k.titel}</td>
                    <td>{k.posts}</td>
                    <td>{Math.round(k.median_kampagnenstart_tage / 7)} Wochen vor Release</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {daten.titel.length > 0 && (
            <details>
              <summary>Die {daten.titel.length} aktivsten Kampagnen im Detail</summary>
              {daten.titel.map((t) => (
                <p key={`${t.title_original}:${t.release}`} style={{ margin: '0.25rem 0' }}>
                  <strong>{t.title_original}</strong>
                  <span className="muted small">
                    {' '}— Release {t.release}, {t.posts} Posts auf {t.kanaele} Kanälen,
                    Start {Math.round(t.kampagnenstart_vorlauf_tage / 7)} Wochen vorher,
                    letzter Post {t.letzter_post_vorlauf_tage >= 0
                      ? `${t.letzter_post_vorlauf_tage} Tage vor`
                      : `${Math.abs(t.letzter_post_vorlauf_tage)} Tage nach`} Release
                  </span>
                </p>
              ))}
            </details>
          )}
        </>
      )}
    </Section>
  );
}

// TikTok-Sound-Trends (22.08.2026): welche Sounds die Posts im Fenster
// tragen — auf TikTok ist der Sound ein eigener Trend-Faktor, und die
// musicMeta lagen seit jeher ungenutzt im raw_payload.
export function SoundTrendsSection() {
  const [daten, setDaten] = useState(null);
  const [fehler, setFehler] = useState(null);

  useEffect(() => {
    let cancelled = false;
    endpoints.soundTrends()
      .then((antwort) => { if (!cancelled) setDaten(antwort); })
      .catch((err) => { if (!cancelled) setFehler(String(err?.message || err)); });
    return () => { cancelled = true; };
  }, []);

  return (
    <Section title="Sound-Trends" kicker="Welche Sounds tragen die TikTok-Treffer?">
      {fehler && <p className="error">Konnte die Sound-Trends nicht laden: {fehler}</p>}
      {!fehler && !daten && <p className="muted small">Lädt …</p>}
      {daten && daten.note && <p className="muted">{daten.note}</p>}
      {daten && !daten.note && (
        <>
          <p className="muted small">
            {daten.posts_mit_sound} von {daten.tiktok_posts_im_fenster} TikTok-Posts
            im {daten.window_days}-Tage-Fenster tragen Sound-Metadaten.
            „Lift“ ist derselbe kanal-normierte Wert wie im Muster-Bericht.
          </p>
          <table>
            <thead>
              <tr>
                <th>Sound</th><th>Posts</th><th>Kanäle</th><th>Median-Lift</th><th>Top-Beispiel</th>
              </tr>
            </thead>
            <tbody>
              {daten.sounds.map((s) => (
                <tr key={`${s.name}:${s.author || ''}`}>
                  <td>
                    {s.name}
                    {s.author ? <span className="muted small"> · {s.author}</span> : null}
                    {s.original ? <span className="pill"> Original-Sound</span> : null}
                  </td>
                  <td>{s.posts}</td>
                  <td className="muted small">{s.kanaele.map((k) => `@${k}`).join(', ')}</td>
                  <td>{s.median_lift != null ? `${s.median_lift}x` : '—'}</td>
                  <td>
                    {s.beispiel_post_url ? (
                      <a href={s.beispiel_post_url} target="_blank" rel="noreferrer">öffnen ↗</a>
                    ) : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </Section>
  );
}

const EBENE_LABEL = { genre: 'Genre-Ebene', title: 'Titel-Ebene' };

function BriefingAnzeige({ anzeige }) {
  const ebene = EBENE_LABEL[anzeige.mode] || anzeige.mode;
  if (anzeige.fehler) {
    return (
      <p className="error" style={{ marginTop: '0.75rem' }}>
        Konnte das letzte Ergebnis ({ebene}) nicht laden: {anzeige.fehler}
      </p>
    );
  }
  if (anzeige.leer) {
    return (
      <p className="muted" style={{ marginTop: '0.75rem' }}>
        {ebene}: Für diese Ebene wurde noch kein Briefing generiert.
      </p>
    );
  }
  const d = anzeige.daten || {};
  const bausteine = d.llm_output?.bausteine || [];
  const caveats = d.llm_output?.data_caveats || [];
  return (
    <div style={{ marginTop: '0.75rem' }}>
      <p style={{ margin: '0 0 0.5rem', fontWeight: 600 }}>
        {ebene} · KW {d.iso_week}/{d.iso_year} · Modell: {d.model}
      </p>
      {bausteine.length === 0 && (
        <p className="muted">
          Der Lauf hat keine Bausteine hinterlassen — kein belastbares
          Muster im Fenster, oder alle Bausteine wurden mangels Belegen
          verworfen.
        </p>
      )}
      {bausteine.map((b, i) => (
        <div
          key={i}
          style={{
            border: '1px solid rgba(127, 127, 127, 0.35)',
            borderRadius: '8px',
            padding: '0.75rem',
            marginBottom: '0.75rem',
          }}
        >
          <p style={{ margin: '0 0 0.25rem', fontWeight: 600 }}>{b.muster}</p>
          {b.begruendung && (
            <p className="muted small" style={{ margin: '0 0 0.5rem' }}>{b.begruendung}</p>
          )}
          {[
            ['Hooks (DE)', b.hooks_de],
            ['Hooks (EN)', b.hooks_en],
            ['Captions (DE)', b.captions_de],
            ['Captions (EN)', b.captions_en],
          ].map(([label, liste]) => (
            Array.isArray(liste) && liste.length > 0 && (
              <div key={label} style={{ marginBottom: '0.5rem' }}>
                <p className="small" style={{ margin: '0 0 0.15rem', fontWeight: 600 }}>{label}</p>
                <ul style={{ margin: 0, paddingLeft: '1.2rem' }}>
                  {liste.map((eintrag, j) => (
                    <li key={j} className="small">{eintrag}</li>
                  ))}
                </ul>
              </div>
            )
          ))}
          {Array.isArray(b.hashtags) && b.hashtags.length > 0 && (
            <p className="small" style={{ margin: '0 0 0.5rem' }}>
              {b.hashtags.map((h) => `#${h}`).join(' ')}
            </p>
          )}
          {Array.isArray(b.cited_post_ids) && b.cited_post_ids.length > 0 && (
            <p className="small muted" style={{ margin: 0 }}>
              Belege:{' '}
              {b.cited_post_ids.map((url, j) => (
                <a key={j} href={url} target="_blank" rel="noreferrer" style={{ marginRight: '0.5rem' }}>
                  Post {j + 1}
                </a>
              ))}
            </p>
          )}
        </div>
      ))}
      {caveats.length > 0 && (
        <p className="muted small" style={{ margin: 0 }}>
          Einschränkungen: {caveats.join(' · ')}
        </p>
      )}
    </div>
  );
}

export function BriefingSection() {
  const [laeuft, setLaeuft] = useState(null); // 'genre' | 'title' | null
  const [ergebnis, setErgebnis] = useState(null);
  // { mode, daten } | { mode, leer: true } | { mode, fehler }
  const [anzeige, setAnzeige] = useState(null);

  const anzeigen = (mode) => {
    endpoints.adminPatternBriefingLatest({ mode })
      .then((daten) => setAnzeige({ mode, daten }))
      .catch((err) => {
        const fehler = String(err?.message || err);
        // Die beiden 404-Texte des Endpoints enthalten beide
        // "kein Pattern-Briefing" — alles andere ist ein echter Fehler.
        if (/kein pattern-briefing/i.test(fehler)) {
          setAnzeige({ mode, leer: true });
        } else {
          setAnzeige({ mode, fehler });
        }
      });
  };

  const generieren = (mode) => {
    setLaeuft(mode);
    setErgebnis(null);
    endpoints.adminPatternBriefingGenerate({ mode })
      .then((antwort) => {
        setErgebnis({ ok: true, antwort });
        anzeigen(mode);
      })
      .catch((err) => setErgebnis({ ok: false, fehler: String(err?.message || err) }))
      .finally(() => setLaeuft(null));
  };

  const satz = (() => {
    if (!ergebnis) return null;
    if (!ergebnis.ok) return `Aufruf fehlgeschlagen: ${ergebnis.fehler}`;
    const a = ergebnis.antwort || {};
    if (a.model === 'none') {
      return 'Kein belastbares Muster im Fenster — kein LLM-Aufruf, keine Kosten. '
        + '(Genres füllen sich mit dem Title-Sync, Titel brauchen genug zugeordnete Posts.)';
    }
    if (!a.llm_output_present) {
      return 'Generierung fehlgeschlagen — die Antwort hat die Prüfung nicht bestanden. Details im Log.';
    }
    const verworfen = a.citation_dropped
      ? ` ${a.citation_dropped} Baustein(e) verworfen, weil Belege fehlten.`
      : '';
    return `${a.bausteine} Baustein(e) für KW ${a.iso_week}/${a.iso_year} erstellt `
      + `(${a.cost_usd_cents} Cent).${verworfen} Das Ergebnis steht direkt hierunter.`;
  })();

  return (
    <Section title="Text-Bausteine generieren" kicker="Hooks & Captions aus den Mustern — Review-Lauf">
      <p style={{ margin: '0 0 0.5rem' }}>
        Erstellt die Text-Bausteine dieser Woche neu. Jeder Lauf kostet etwa
        5–20 Cent (Anthropic-Monatsdeckel greift); ohne belastbares Muster
        ist der Lauf kostenfrei. Das Ergebnis erscheint direkt hier —
        „Letztes Ergebnis“ zeigt den gespeicherten Stand ohne neuen Lauf.
      </p>
      <div className="section-actions">
        <button type="button" className="secondary" onClick={() => generieren('genre')} disabled={laeuft !== null}>
          {laeuft === 'genre' ? 'Generiert …' : 'Genre-Ebene generieren'}
        </button>
        <button type="button" className="secondary" onClick={() => generieren('title')} disabled={laeuft !== null}>
          {laeuft === 'title' ? 'Generiert …' : 'Titel-Ebene generieren'}
        </button>
        <button type="button" className="secondary" onClick={() => anzeigen('genre')} disabled={laeuft !== null}>
          Letztes Ergebnis: Genre
        </button>
        <button type="button" className="secondary" onClick={() => anzeigen('title')} disabled={laeuft !== null}>
          Letztes Ergebnis: Titel
        </button>
      </div>
      {satz && <p style={{ marginTop: '0.5rem' }}>{satz}</p>}
      {anzeige && <BriefingAnzeige anzeige={anzeige} />}
    </Section>
  );
}

export function PlaybookMailSection() {
  const [laeuft, setLaeuft] = useState(false);
  const [ergebnis, setErgebnis] = useState(null);

  const senden = () => {
    setLaeuft(true);
    setErgebnis(null);
    endpoints.adminPlaybookMailTest()
      .then((antwort) => setErgebnis({ ok: true, antwort }))
      .catch((err) => setErgebnis({ ok: false, fehler: String(err?.message || err) }))
      .finally(() => setLaeuft(false));
  };

  const satz = (() => {
    if (!ergebnis) return null;
    if (!ergebnis.ok) return `Aufruf fehlgeschlagen: ${ergebnis.fehler}`;
    const a = ergebnis.antwort || {};
    if (a.skipped && a.reason === 'no_recipients') {
      return 'Keine Empfänger gesetzt — PLAYBOOK_MAIL_RECIPIENTS in den Railway-Variablen pflegen.';
    }
    if (a.skipped && a.reason === 'nichts_zu_berichten') {
      return 'Nichts zu berichten — aktuell keine Befunde, Bewegungen oder Bausteine.';
    }
    const basis = `Mail gesendet an ${a.sent ?? 0} Empfänger` + (a.failed ? `, ${a.failed} fehlgeschlagen` : '') + '.';
    if (a.emails_disabled) {
      return `${basis} ABER: Der Mail-Versand ist in dieser Umgebung abgeschaltet (DISABLE_EMAILS) — es kam nichts an. Diesen Test in Produktion ausführen.`;
    }
    return basis;
  })();

  return (
    <Section title="Playbook-Test-Mail" kicker="Empfehlungen ins Postfach — Prüf-Lauf">
      <p style={{ margin: '0 0 0.5rem' }}>
        Sendet die Playbook-Mail sofort an die eingetragenen Empfänger
        (PLAYBOOK_MAIL_RECIPIENTS) — unabhängig vom Trailer-Intelligence-Flag.
      </p>
      <div className="section-actions">
        <button type="button" className="secondary" onClick={senden} disabled={laeuft}>
          {laeuft ? 'Sendet …' : 'Test-Mail jetzt senden'}
        </button>
      </div>
      {satz && <p style={{ marginTop: '0.5rem' }}>{satz}</p>}
    </Section>
  );
}

export function MonitoringPanel({ features = {} } = {}) {
  const [budgets, setBudgets] = useState({});
  const [costSummary, setCostSummary] = useState(null);
  const [cronRuns, setCronRuns] = useState(null);
  const [breakouts, setBreakouts] = useState(null);
  // Nutzungs-Auswertung lebt seit 2026-07-20 in der geteilten
  // UsageSection-Komponente (auch von /nutzung genutzt) — hier nur
  // noch eingebettet, reloadKey triggert ihren Re-Fetch mit.
  const [status, setStatus] = useState('loading'); // 'loading' | 'done' | 'error'
  // UX-Audit Befund 13 (2026-07-14): der Panel lud nur einmal beim Öffnen
  // des Tabs; der globale "Aktualisieren"-Button im Footer erreicht diesen
  // lokalen State nicht. ``reloadKey`` re-triggert den Lade-Effect über
  // den neuen Panel-eigenen Refresh-Button.
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      Promise.all(BUDGET_CARDS.map((c) => endpoints[c.fetcher]().catch(() => null))),
      endpoints.costSummary({ groupBy: 'provider' }).catch(() => null),
      endpoints.cronRuns(10).catch(() => null),
      endpoints.breakouts({ limit: 15 }).catch(() => null),
    ]).then(([budgetResults, cost, runs, breakoutFeed]) => {
      if (cancelled) return;
      const byKey = {};
      BUDGET_CARDS.forEach((c, i) => { byKey[c.key] = budgetResults[i]; });
      setBudgets(byKey);
      setCostSummary(cost);
      setCronRuns(Array.isArray(runs) ? runs : []);
      setBreakouts(breakoutFeed?.entries ?? []);
      setStatus('done');
    }).catch(() => { if (!cancelled) setStatus('error'); });
    return () => { cancelled = true; };
  }, [reloadKey]);

  return (
    <>
      <BriefingSection />
      <WirSegmentSection />
      {/* Feature-Flag-Gates (Arbeitsregel 23.08.2026): neue Auswertungen
          erscheinen nur, wo /api/health -> features sie anbietet —
          Staging zuerst, Production nach Wolfs Freigabe. */}
      {features.kampagnen_timing && <KampagnenTimingSection />}
      {features.sound_trends && <SoundTrendsSection />}
      <PlaybookMailSection />
      <Section title="Budgets diesen Monat" kicker="Kalendermonat, UTC">
        {status === 'error' && <p className="error">Konnte Budget-/Kostendaten nicht laden.</p>}
        <div className="ops-budget-grid">
          {BUDGET_CARDS.map((c) => (
            <BudgetStatusCard key={c.key} label={c.label} status={budgets[c.key]} />
          ))}
        </div>
        <div className="section-actions">
          <button
            type="button"
            className="secondary"
            onClick={() => { setStatus('loading'); setReloadKey((k) => k + 1); }}
            disabled={status === 'loading'}
          >
            {status === 'loading' ? 'Lädt …' : 'Daten aktualisieren'}
          </button>
        </div>
      </Section>

      {/* Kicker war "Letzte 30 Tage" — falsch: cost-summary nimmt ohne
          from_date/to_date den laufenden Kalendermonat (_default_window
          in api/admin.py), genau wie die Budget-Karten darueber. Am
          Monatsersten steht hier darum korrekterweise fast nichts. */}
      <Section title="Kosten nach Provider" kicker="Kalendermonat, UTC">
        {status === 'loading' && <p className="muted small">Lädt …</p>}
        {status === 'done' && costSummary?.buckets && costSummary.buckets.length === 0 && (
          <p className="muted small">Noch keine Kosten in diesem Kalendermonat.</p>
        )}
        {status === 'done' && costSummary?.buckets && costSummary.buckets.length > 0 && (
          <table className="ops-cost-table">
            <thead>
              <tr><th>Provider</th><th>Aufrufe</th><th>Kosten</th></tr>
            </thead>
            <tbody>
              {costSummary.buckets.map((b) => (
                <tr key={b.key}>
                  <td>{b.key}</td>
                  <td>{formatNumber(b.count)}</td>
                  {/* Millicents statt Cents: OpenAI-Zeilen runden pro Call
                      auf 0 ct und ergaben in der Summe faelschlich 0,00 $.
                      ``??`` faengt aeltere Backend-Versionen ab, die das
                      Feld noch nicht liefern. */}
                  <td>{b.cost_usd_millicents != null
                    ? formatUsdMillicents(b.cost_usd_millicents)
                    : formatUsdCents(b.cost_usd_cents)}</td>
                </tr>
              ))}
              <tr className="ops-cost-total">
                <td>Gesamt</td>
                <td>{formatNumber(costSummary.total_count)}</td>
                <td>{costSummary.total_cost_usd_millicents != null
                  ? formatUsdMillicents(costSummary.total_cost_usd_millicents)
                  : formatUsdCents(costSummary.total_cost_usd_cents)}</td>
              </tr>
            </tbody>
          </table>
        )}
      </Section>

      <Section title="Breakouts" kicker="Über alle Pairs, ≥2x Kanal-Schnitt">
        {status === 'loading' && <p className="muted small">Lädt …</p>}
        {status === 'done' && breakouts && breakouts.length === 0 && (
          <p className="muted small">Aktuell keine Ausreisser über der Schwelle.</p>
        )}
        {status === 'done' && breakouts && breakouts.length > 0 && (
          <table className="ops-breakouts-table">
            <thead>
              <tr><th>Pair</th><th>Markt</th><th>Post</th><th>Vielfaches</th><th>Aufrufe</th></tr>
            </thead>
            <tbody>
              {breakouts.map((b) => (
                <tr key={b.post_url}>
                  <td>{b.pair_label}</td>
                  <td>{b.market}</td>
                  <td className="ops-breakouts-caption">
                    <a href={b.post_url} target="_blank" rel="noreferrer">
                      {clip(b.caption_excerpt || b.post_url, 70)}
                    </a>
                  </td>
                  <td className="ops-breakouts-multiplier">{formatMultiplier(b.multiplier)}</td>
                  {/* "—" statt 0, wenn die Plattform fuer das Post-Format
                      keine Aufruf-Zahl liefert (Vielfaches basiert auf
                      Interaktionen, nicht Aufrufen). */}
                  <td>{b.views ? formatNumber(b.views) : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Section>

      <Section title="Nutzung" kicker="Wer nutzt was — letzte 30 Tage">
        <UsageSection reloadKey={reloadKey} />
      </Section>

      <Section title="Letzte Cron-Läufe" kicker="Neueste zuerst">
        {status === 'loading' && <p className="muted small">Lädt …</p>}
        {status === 'done' && cronRuns && cronRuns.length === 0 && (
          <p className="muted small">Noch keine Läufe protokolliert.</p>
        )}
        {status === 'done' && cronRuns && cronRuns.length > 0 && (
          <table className="ops-runs-table">
            <thead>
              <tr><th>Gestartet</th><th>Status</th><th>Dauer</th><th>Hinweis</th></tr>
            </thead>
            <tbody>
              {cronRuns.map((r) => (
                <tr key={r.id} className={r.status === 'completed' ? '' : 'ops-runs-row-warn'}>
                  <td>{formatDateTime(r.started_at)}</td>
                  <td>{formatCronRunStatus(r.status)}</td>
                  <td>{r.duration_seconds != null ? `${Math.round(r.duration_seconds / 60)} Min.` : '—'}</td>
                  <td>{r.error_message || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Section>
    </>
  );
}
