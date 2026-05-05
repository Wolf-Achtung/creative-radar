import React, { useEffect, useState } from 'react';
import { endpoints } from './api/client';

// Pre-fetch labels — used when the URL pair-key arrives before the API
// response (or when the API errors). Mirrors ``PAIRS`` in
// ``backend/app/services/insight_engine.py`` 1:1; keep in sync when adding
// or renaming pairs. The Sprint-2 set covers all seven Tier-A DE+US TT
// pairs from migration ``e5d8f1a36b40`` — six enabled today, plus
// ``universalpictures`` shipped as a disabled placeholder.
const FALLBACK_LABEL = {
  warnerbros: 'warnerbros DE+US',
  sonypictures: 'sonypictures DE+US',
  primevideo: 'primevideo DE+US',
  disney: 'disney DE+US',
  netflix: 'netflix DE+US',
  paramountpictures: 'paramountpictures DE+US',
  universalpictures: 'universalpictures DE+US',
};

// Try to recognise the structured 503 body the backend sends for disabled
// pairs (``api/insights.py``). ``api/client.js`` throws ``new Error(body)``
// for non-OK responses, so the JSON arrives stringified in ``err.message``.
// Returns ``{reason}`` when matched, otherwise ``null``.
function parsePairNotActivated(errorMessage) {
  if (!errorMessage || typeof errorMessage !== 'string') return null;
  try {
    const parsed = JSON.parse(errorMessage);
    const detail = parsed?.detail ?? parsed;
    if (detail && detail.error === 'pair_not_activated') {
      return { reason: detail.reason || 'Pair ist aktuell deaktiviert.' };
    }
  } catch (_) {
    // Not JSON or doesn't match the structured body — fall through.
  }
  return null;
}

function formatNumber(value) {
  if (value === null || value === undefined || value === '') return '—';
  try { return new Intl.NumberFormat('de-DE').format(Number(value)); } catch (_) { return String(value); }
}

function formatPct(value) {
  if (value === null || value === undefined) return '—';
  return `${Number(value).toFixed(1)}%`;
}

function formatDateISO(value) {
  if (!value) return '—';
  try { return new Date(value).toLocaleDateString('de-DE'); } catch (_) { return String(value); }
}

function CoverageBanner({ report }) {
  const cov = report?.coverage_pct ?? 0;
  const tone = cov >= 70 ? 'good' : cov >= 30 ? 'warn' : 'bad';
  const label = cov >= 70 ? 'solide' : cov >= 30 ? 'moderat' : 'dünn';
  return (
    <div className={`coverage-banner cov-${tone}`}>
      <span className="eyebrow">Datenfenster {report.window_days} Tage · KW {report.iso_week}/{report.iso_year}</span>
      <span>Title-Coverage <strong>{formatPct(cov)}</strong> ({label})</span>
      {report.dry_run && <span className="pill warn">Dry-Run · LLM nicht gerufen</span>}
      {report.model && <span className="pill">{report.model}</span>}
      {report.cost_usd_estimate != null && !report.dry_run && (
        <span className="pill">Cost ≈ ${report.cost_usd_estimate.toFixed(3)}</span>
      )}
    </div>
  );
}

function ChannelStatsCard({ stats }) {
  if (!stats) return null;
  if (!stats.channel_found) {
    return (
      <div className="card insight-channel-card">
        <p className="section-kicker">{stats.market} · @{stats.handle}</p>
        <h3>Channel nicht in DB</h3>
        <p>Onboarding/Whitelist-Eintrag fehlt für diesen Pair-Kanal.</p>
      </div>
    );
  }
  const buckets = stats.duration_buckets || {};
  return (
    <div className="card insight-channel-card">
      <p className="section-kicker">{stats.market} · @{stats.handle}</p>
      <h3>{formatNumber(stats.posts_count)} Posts · {formatNumber(stats.assets_count)} Assets</h3>
      <div className="insight-kpi-row">
        <div><strong>{formatPct(stats.coverage_pct)}</strong><span>Coverage</span></div>
        <div><strong>{stats.avg_duration_seconds != null ? `${stats.avg_duration_seconds}s` : '—'}</strong><span>Ø Duration</span></div>
        <div><strong>{formatNumber(Math.round(stats.avg_engagement))}</strong><span>Ø Engagement</span></div>
        <div><strong>{Math.round(stats.avg_caption_length || 0)}</strong><span>Ø Caption-Länge</span></div>
      </div>
      <div className="insight-buckets">
        {['<15s', '15-30s', '30-60s', '>60s', 'unknown'].filter((b) => buckets[b]).map((b) => (
          <span className="pill" key={b}>{b}: {buckets[b]}</span>
        ))}
      </div>
      {stats.top_hashtags?.length > 0 && (
        <div className="insight-hashtags">
          <p className="section-kicker">Top Hashtags</p>
          <div>{stats.top_hashtags.map((h) => (
            <span className="pill" key={h.tag}>#{h.tag} · {h.count}</span>
          ))}</div>
        </div>
      )}
      {stats.top_posts?.length > 0 && (
        <div className="insight-toppost-list">
          <p className="section-kicker">Top Posts</p>
          <ol>
            {stats.top_posts.map((p) => (
              <li key={p.post_url}>
                <a href={p.post_url} target="_blank" rel="noreferrer">{p.title || 'Ohne Titel'}</a>
                {' · '}{formatNumber(p.engagement_sum)} Engagement
                {p.duration_seconds != null && ` · ${p.duration_seconds}s`}
                {p.caption_excerpt && <div className="insight-caption-excerpt">{p.caption_excerpt}</div>}
              </li>
            ))}
          </ol>
        </div>
      )}
    </div>
  );
}

function CrossMarketCard({ matches }) {
  if (!matches || matches.length === 0) {
    return (
      <div className="card">
        <p className="section-kicker">Cross-Market Matches</p>
        <p>Keine de_us_match_key-Treffer im Fenster.</p>
      </div>
    );
  }
  return (
    <div className="card">
      <p className="section-kicker">Cross-Market Matches</p>
      <ol className="insight-cross-market">
        {matches.map((m) => (
          <li key={m.match_key}>
            <strong>{m.title || m.match_key}</strong>
            <div>
              DE: {formatNumber(m.de_engagement)}{m.de_duration_seconds != null && ` · ${m.de_duration_seconds}s`}
              {' / '}US: {formatNumber(m.us_engagement)}{m.us_duration_seconds != null && ` · ${m.us_duration_seconds}s`}
            </div>
            <div className="insight-cross-market-links">
              {m.de_post_url && <a href={m.de_post_url} target="_blank" rel="noreferrer">DE-Post</a>}
              {m.us_post_url && <a href={m.us_post_url} target="_blank" rel="noreferrer">US-Post</a>}
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}

function LLMOutput({ output, raw }) {
  if (!output) {
    return (
      <div className="card">
        <p className="section-kicker">LLM-Output</p>
        {raw ? (
          <>
            <p>JSON-Parsing fehlgeschlagen. Roh-Antwort des Modells:</p>
            <pre className="insight-raw">{raw}</pre>
          </>
        ) : (
          <p>Dry-Run aktiv — Opus 4.7 wurde nicht aufgerufen. Aggregation oben prüfen.</p>
        )}
      </div>
    );
  }
  return (
    <>
      <div className="card insight-headline">
        <p className="section-kicker">Headline</p>
        <h2 className="insight-h2">{output.headline}</h2>
        <p className="insight-tldr">{output.tldr}</p>
      </div>

      <div className="insight-grid-two">
        <div className="card">
          <p className="section-kicker">Trends</p>
          <ul className="insight-list">
            {output.trends.map((t, i) => (
              <li key={i}>
                <strong>{t.name}</strong>
                <div className="insight-evidence">Belegt durch: {t.evidence}</div>
                <div>→ {t.implication_for_creation}</div>
              </li>
            ))}
          </ul>
        </div>
        <div className="card">
          <p className="section-kicker">Actions</p>
          <ul className="insight-list">
            {output.actions.map((a, i) => (
              <li key={i}>
                <strong>{a.what}</strong>
                <div className="insight-evidence">Warum: {a.why}</div>
                <div className="insight-evidence">Für: {a.for_whom}</div>
              </li>
            ))}
          </ul>
        </div>
      </div>

      <div className="card">
        <p className="section-kicker">Cross-Market Insight</p>
        <p><strong>DE vs US:</strong> {output.cross_market_insight.de_vs_us}</p>
        <p><strong>Transfer-Opportunity:</strong> {output.cross_market_insight.transfer_opportunity}</p>
      </div>

      {(output.risks?.length > 0 || output.data_caveats?.length > 0) && (
        <div className="card insight-meta">
          {output.risks?.length > 0 && (
            <div>
              <p className="section-kicker">Risks</p>
              <ul>{output.risks.map((r, i) => <li key={i}>{r}</li>)}</ul>
            </div>
          )}
          {output.data_caveats?.length > 0 && (
            <div>
              <p className="section-kicker">Data Caveats</p>
              <ul>{output.data_caveats.map((c, i) => <li key={i}>{c}</li>)}</ul>
            </div>
          )}
        </div>
      )}
    </>
  );
}

// 60s slow-marker — purely informational ("dauert länger als erwartet"),
// the request itself is NOT aborted because Opus 4.7 can legitimately take
// 30-90s on the largest reports and aborting would surface a misleading
// "failed" state. Bump if Wolf raises max_tokens beyond 8k.
const SLOW_THRESHOLD_MS = 60_000;

export default function InsightWeekly({ pair }) {
  const [report, setReport] = useState(null);
  const [error, setError] = useState(null);
  const [status, setStatus] = useState('idle'); // 'idle' | 'loading' | 'slow' | 'done' | 'error'
  const [windowDays, setWindowDays] = useState(30);
  const [dryRun, setDryRun] = useState(false);

  async function load() {
    setStatus('loading');
    setError(null);

    // Slow-state-timer: switches the UI to a calmer "still working" message
    // after SLOW_THRESHOLD_MS so the user knows the call hasn't silently
    // wedged. Cleared in finally regardless of outcome.
    let slowTimer = setTimeout(() => setStatus((s) => (s === 'loading' ? 'slow' : s)), SLOW_THRESHOLD_MS);
    try {
      const data = await endpoints.insightsWeekly(pair, { windowDays, dryRun });
      setReport(data);
      setStatus('done');
    } catch (err) {
      setError(err.message || String(err));
      setStatus('error');
    } finally {
      clearTimeout(slowTimer);
    }
  }

  useEffect(() => { load(); /* eslint-disable-next-line */ }, [pair]);

  const loading = status === 'loading' || status === 'slow';

  const label = report?.pair_label || FALLBACK_LABEL[pair] || pair;

  return (
    <main className="page insight-page">
      <header className="hero">
        <p className="eyebrow">Weekly Insight Brief</p>
        <h1>{label}</h1>
        <p>Wochenreport für Trailerhaus-Creatives — Hooks, Schnitt, Format und Cross-Market-Transfer.</p>
        <div className="hero-actions">
          <label>
            Datenfenster (Tage)
            <input
              type="number"
              min={7}
              max={90}
              value={windowDays}
              onChange={(e) => setWindowDays(Number(e.target.value) || 30)}
              style={{ marginLeft: 8, width: 70 }}
            />
          </label>
          <label>
            <input type="checkbox" checked={dryRun} onChange={(e) => setDryRun(e.target.checked)} />
            {' '}Dry-Run (kein LLM-Call)
          </label>
          <button onClick={load} disabled={loading}>{loading ? 'Lädt …' : 'Neu generieren'}</button>
        </div>
      </header>

      {status === 'slow' && (
        <div className="card insight-slow-hint">
          <p className="section-kicker">Dauert länger</p>
          <p>Report dauert länger als erwartet, bitte warten — Opus 4.7 antwortet bei großen Aggregations gelegentlich erst nach 60-90s. Kein Abbruch nötig.</p>
        </div>
      )}

      {status === 'error' && (() => {
        const disabled = parsePairNotActivated(error);
        if (disabled) {
          return (
            <div className="card insight-error">
              <p className="section-kicker">Pair noch nicht aktiviert</p>
              <p>Dieser Pair ist noch nicht aktiviert. Grund: {disabled.reason}</p>
            </div>
          );
        }
        return (
          <div className="card insight-error">
            <p className="section-kicker">Fehler</p>
            <pre>{error}</pre>
            {report?.raw_llm_text && (
              <>
                <p className="section-kicker">Roh-Antwort des Modells (Fallback)</p>
                <pre className="insight-raw">{report.raw_llm_text}</pre>
              </>
            )}
          </div>
        );
      })()}

      {report && status !== 'error' && (
        <>
          <CoverageBanner report={report} />

          {report.aggregation?.notes?.length > 0 && (
            <div className="card insight-notes">
              <p className="section-kicker">Notes</p>
              <ul>{report.aggregation.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>
            </div>
          )}

          <LLMOutput output={report.llm_output} raw={report.raw_llm_text} />

          <div className="insight-grid-two">
            <ChannelStatsCard stats={report.aggregation?.de_channel} />
            <ChannelStatsCard stats={report.aggregation?.us_channel} />
          </div>

          <CrossMarketCard matches={report.aggregation?.cross_market_matches} />
        </>
      )}

      {!report && status === 'loading' && (
        <div className="card"><p>Generiere Report …</p></div>
      )}

      <footer className="footer-status">
        <a href="/">← zurück zum Hauptdashboard</a>
        {report && <span> · generiert {formatDateISO(report.generated_at)}</span>}
      </footer>
    </main>
  );
}
