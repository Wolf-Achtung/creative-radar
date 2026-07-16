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
} from '../format';
import { Section } from '../components/Section';

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

export function MonitoringPanel() {
  const [budgets, setBudgets] = useState({});
  const [costSummary, setCostSummary] = useState(null);
  const [cronRuns, setCronRuns] = useState(null);
  const [breakouts, setBreakouts] = useState(null);
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

      <Section title="Kosten nach Provider" kicker="Letzte 30 Tage">
        {status === 'loading' && <p className="muted small">Lädt …</p>}
        {status === 'done' && costSummary?.buckets && costSummary.buckets.length === 0 && (
          <p className="muted small">Keine Kosten im Zeitraum.</p>
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
                  <td>{formatUsdCents(b.cost_usd_cents)}</td>
                </tr>
              ))}
              <tr className="ops-cost-total">
                <td>Gesamt</td>
                <td>{formatNumber(costSummary.total_count)}</td>
                <td>{formatUsdCents(costSummary.total_cost_usd_cents)}</td>
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
                  <td>{formatNumber(b.views)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
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
