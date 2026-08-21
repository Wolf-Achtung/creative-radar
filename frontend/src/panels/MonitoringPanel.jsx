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
// kostenfrei (model='none'). Ergebnis landet im Muster-Panel und in
// der naechsten Playbook-Mail.
export function BriefingSection() {
  const [laeuft, setLaeuft] = useState(null); // 'genre' | 'title' | null
  const [ergebnis, setErgebnis] = useState(null);

  const generieren = (mode) => {
    setLaeuft(mode);
    setErgebnis(null);
    endpoints.adminPatternBriefingGenerate({ mode })
      .then((antwort) => setErgebnis({ ok: true, antwort }))
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
      + `(${a.cost_usd_cents} Cent).${verworfen} Sichtbar im Muster-Panel und in der nächsten Playbook-Mail.`;
  })();

  return (
    <Section title="Text-Bausteine generieren" kicker="Hooks & Captions aus den Mustern — Review-Lauf">
      <p style={{ margin: '0 0 0.5rem' }}>
        Erstellt die Text-Bausteine dieser Woche neu. Jeder Lauf kostet etwa
        5–10 Cent (Anthropic-Monatsdeckel greift); ohne belastbares Muster
        ist der Lauf kostenfrei.
      </p>
      <div className="section-actions">
        <button type="button" className="secondary" onClick={() => generieren('genre')} disabled={laeuft !== null}>
          {laeuft === 'genre' ? 'Generiert …' : 'Genre-Ebene generieren'}
        </button>
        <button type="button" className="secondary" onClick={() => generieren('title')} disabled={laeuft !== null}>
          {laeuft === 'title' ? 'Generiert …' : 'Titel-Ebene generieren'}
        </button>
      </div>
      {satz && <p style={{ marginTop: '0.5rem' }}>{satz}</p>}
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

export function MonitoringPanel() {
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
