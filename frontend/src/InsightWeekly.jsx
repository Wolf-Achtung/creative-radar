import React, { useEffect, useId, useMemo, useRef, useState } from 'react';
import { endpoints } from './api/client';

// Pre-fetch labels — used when the URL pair-key arrives before the API
// response (or when the API errors). Mirrors ``PAIRS`` in
// ``backend/app/services/insight_engine.py`` 1:1; keep in sync when adding
// or renaming pairs. The Sprint-2 set covers all seven Tier-A DE+US TT
// pairs from migration ``e5d8f1a36b40`` — six enabled today, plus
// ``universalpictures`` shipped as a disabled placeholder.
const FALLBACK_LABEL = {
  warnerbros: 'Warner Bros · DE + US',
  sonypictures: 'Sony Pictures · DE + US',
  primevideo: 'Prime Video · DE + US',
  disney: 'Disney · DE + US',
  netflix: 'Netflix · DE + US',
  paramountpictures: 'Paramount · DE + US',
  universalpictures: 'Universal Pictures · DE + US',
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

// Sprint 1 (Persistenz): "Stand: 08.05.2026, 14:30" im Studio-Brief-Header.
// Macht für den GF sichtbar, ob er einen frischen oder einen aus dem Cache
// geladenen Brief liest. Format: dd.mm.yyyy, hh:mm in lokaler Zeit.
function formatStand(value) {
  if (!value) return '';
  try {
    const d = new Date(value);
    const date = d.toLocaleDateString('de-DE');
    const time = d.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' });
    return `${date}, ${time}`;
  } catch (_) {
    return String(value);
  }
}

// ---- Sprint 3: HelpTooltip --------------------------------------------
//
// Inline question-mark trigger that exposes a short explanation on hover
// (Desktop) and on tap (Mobile). Escape and click-outside close the
// open tooltip. The trigger is a real <button> for keyboard accessibility;
// the popup carries `role="tooltip"` and the trigger references it via
// `aria-describedby` while open.
//
// We don't use `title=` because that limits styling, can't carry rich
// content, and behaves inconsistently on touch devices.

function HelpTooltip({ text, label = 'Erklärung anzeigen' }) {
  const [open, setOpen] = useState(false);
  const tooltipId = useId();
  const wrapperRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    function handlePointerDown(e) {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target)) {
        setOpen(false);
      }
    }
    function handleKey(e) {
      if (e.key === 'Escape') setOpen(false);
    }
    document.addEventListener('mousedown', handlePointerDown);
    document.addEventListener('touchstart', handlePointerDown);
    document.addEventListener('keydown', handleKey);
    return () => {
      document.removeEventListener('mousedown', handlePointerDown);
      document.removeEventListener('touchstart', handlePointerDown);
      document.removeEventListener('keydown', handleKey);
    };
  }, [open]);

  return (
    <span className="help-tooltip-wrapper" ref={wrapperRef}>
      <button
        type="button"
        className="help-tooltip-trigger"
        aria-label={label}
        aria-describedby={open ? tooltipId : undefined}
        aria-expanded={open}
        onClick={(e) => { e.preventDefault(); setOpen((o) => !o); }}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        tabIndex={0}
      >
        ?
      </button>
      {open && (
        <span className="help-tooltip-content" id={tooltipId} role="tooltip">
          {text}
        </span>
      )}
    </span>
  );
}

const TOOLTIP_TEXTS = {
  coverage:
    'Coverage: Anteil der Posts, die einem konkreten Filmtitel zugeordnet werden konnten. Zeigt, wie zielgerichtet ein Channel kommuniziert.',
  crossMarketMatch:
    'Cross-Market Matches: Posts, die in DE und US denselben Filmtitel bewerben — ermöglicht den direkten Performance-Vergleich beider Märkte.',
  activationRate:
    'Aktivierungs-Rate: Wie viele der Zuschauer reagiert haben (Like, Kommentar, Save). 5–10 % sind auf TikTok normal, darüber stark.',
};

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
        <div>
          <strong>{formatPct(stats.coverage_pct)}</strong>
          <span>Coverage<HelpTooltip text={TOOLTIP_TEXTS.coverage} label="Was bedeutet Coverage?" /></span>
        </div>
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
                <a href={p.post_url} target="_blank" rel="noreferrer">{p.title || (p.caption_excerpt ? (p.caption_excerpt.length > 60 ? p.caption_excerpt.slice(0, 60).trim() + '…' : p.caption_excerpt) : 'Ohne Titel')}</a>
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
        <p className="section-kicker">
          Cross-Market Matches
          <HelpTooltip text={TOOLTIP_TEXTS.crossMarketMatch} label="Was sind Cross-Market Matches?" />
        </p>
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
          <p>Dry-Run aktiv — kein LLM-Call. Aggregation oben prüfen.</p>
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

      {output.aktuell_im_fokus?.length > 0 && (
        <div className="card">
          <p className="section-kicker">Worum geht's diese Woche</p>
          <ul className="insight-list">
            {output.aktuell_im_fokus.map((item, i) => (
              <li key={i}>
                {item.post_url ? (
                  <a
                    href={item.post_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    style={{ color: 'inherit', textDecoration: 'underline' }}
                  >
                    <strong>{item.titel}</strong>
                  </a>
                ) : (
                  <strong>{item.titel}</strong>
                )}
                <span style={{ color: '#888', marginLeft: '0.5rem', fontSize: '0.9em' }}>
                  · {item.markt} · {item.format_typ}
                  {item.release_datum && ` · ${item.release_datum}`}
                  {item.verdict && ` · ${item.verdict}`}
                </span>
                <div className="insight-evidence">{item.kennzahl}</div>
              </li>
            ))}
          </ul>
        </div>
      )}

      {output.ganz_konkret?.length > 0 && (
        <div className="card">
          <p className="section-kicker">Diese Woche: was funktioniert gut, was nicht</p>
          <ol className="insight-list">
            {output.ganz_konkret.map((item, i) => (
              <li key={i}>
                {item.bezug && (
                  <div style={{
                    fontSize: '0.75em',
                    color: '#888',
                    textTransform: 'uppercase',
                    letterSpacing: '0.05em',
                    marginBottom: '0.25rem',
                  }}>
                    {item.bezug}
                  </div>
                )}
                <strong>{item.pattern}</strong>
                <div className="insight-evidence">Lern-Take: {item.lern_take}</div>
                {item.frage && (
                  <div className="insight-evidence" style={{ fontStyle: 'italic', marginTop: '0.25rem' }}>
                    Frage: {item.frage}
                  </div>
                )}
              </li>
            ))}
          </ol>
        </div>
      )}

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

      {output.konkurrenz && (
        <div className="card">
          <p className="section-kicker">Was machen die anderen?</p>
          {output.konkurrenz.was_alle_machen && (
            <p><strong>Was alle machen:</strong> {output.konkurrenz.was_alle_machen}</p>
          )}
          {output.konkurrenz.format_trend && (
            <p><strong>Format-Trend:</strong> {output.konkurrenz.format_trend}</p>
          )}
          {output.konkurrenz.genre_beobachtung && (
            <p><strong>Genre-Beobachtung:</strong> {output.konkurrenz.genre_beobachtung}</p>
          )}
          {output.konkurrenz.neu_seit_letzten_wochen && (
            <p><strong>Neu seit letzten Wochen:</strong> {output.konkurrenz.neu_seit_letzten_wochen}</p>
          )}
        </div>
      )}

      {output.fuer_cutter && (
        <div className="card">
          <p className="section-kicker">Für den Cutter</p>
          {output.fuer_cutter.schnitt_pace && (
            <p><strong>Rhythmus:</strong> {output.fuer_cutter.schnitt_pace}</p>
          )}
          {output.fuer_cutter.hook_strategie && (
            <p><strong>Hook-Strategie:</strong> {output.fuer_cutter.hook_strategie}</p>
          )}
          {output.fuer_cutter.empfohlene_laengen && (
            <p><strong>Empfohlene Längen:</strong> {output.fuer_cutter.empfohlene_laengen}</p>
          )}
          {/* Sprint 7-iter-2: Compliance-Listen (must_show / no_go) ersetzt
              durch was_diese_woche-Fließtext. Alte persistierte Briefe ohne
              das Feld rendern den Block einfach nicht — graceful degrade. */}
          {output.fuer_cutter.was_diese_woche && (
            <p><strong>Was diese Woche:</strong> {output.fuer_cutter.was_diese_woche}</p>
          )}
        </div>
      )}

      {output.fuer_motion_designer && (
        <div className="card">
          <p className="section-kicker">Für den Motion-Designer</p>
          {output.fuer_motion_designer.caption_style && (
            <p><strong>Caption-Stil:</strong> {output.fuer_motion_designer.caption_style}</p>
          )}
          {output.fuer_motion_designer.text_overlay && (
            <p><strong>Text-Overlay:</strong> {output.fuer_motion_designer.text_overlay}</p>
          )}
          {output.fuer_motion_designer.branding_einsatz && (
            <p><strong>Branding-Einsatz:</strong> {output.fuer_motion_designer.branding_einsatz}</p>
          )}
          {output.fuer_motion_designer.was_diese_woche && (
            <p><strong>Was diese Woche:</strong> {output.fuer_motion_designer.was_diese_woche}</p>
          )}
        </div>
      )}

      {output.fuer_creative_producer && (
        <div className="card">
          <p className="section-kicker">Für den Creative Producer</p>
          {output.fuer_creative_producer.strategische_pattern && (
            <p><strong>Strategische Pattern:</strong> {output.fuer_creative_producer.strategische_pattern}</p>
          )}
          {output.fuer_creative_producer.cross_market_chancen && (
            <p><strong>Cross-Market-Chancen:</strong> {output.fuer_creative_producer.cross_market_chancen}</p>
          )}
          {output.fuer_creative_producer.format_empfehlungen && (
            <p><strong>Format-Empfehlungen:</strong> {output.fuer_creative_producer.format_empfehlungen}</p>
          )}
          {output.fuer_creative_producer.was_diese_woche && (
            <p><strong>Was diese Woche:</strong> {output.fuer_creative_producer.was_diese_woche}</p>
          )}
        </div>
      )}

      {output.tonalitaet?.length > 0 && (
        <div className="card">
          <p className="section-kicker">Tonalität</p>
          <ul className="insight-list">
            {output.tonalitaet.map((t, i) => (
              <li key={i}>
                <strong>{t.adjektiv}</strong>
                <div className="insight-evidence">{t.begruendung}</div>
              </li>
            ))}
          </ul>
        </div>
      )}

      {output.watch_outs?.length > 0 && (
        <div className="card">
          <p className="section-kicker">Watch-Outs</p>
          <ul className="insight-list">
            {output.watch_outs.map((w, i) => (
              <li key={i}>
                <strong>{w.watch_out}</strong>
                <div className="insight-evidence">Konsequenz: {w.konsequenz}</div>
              </li>
            ))}
          </ul>
        </div>
      )}

      {output.vergleichbare_posts?.length > 0 && (
        <div className="card">
          <p className="section-kicker">Vergleichbare Posts</p>
          <ul className="insight-list">
            {output.vergleichbare_posts.map((p, i) => (
              <li key={i}>
                <strong>@{p.handle}</strong> · {p.performance_kpi}
                <div className="insight-evidence">{p.relevanz_grund}</div>
                {p.post_id && p.post_id.startsWith('http') && (
                  <div className="insight-evidence">
                    <a href={p.post_id} target="_blank" rel="noopener noreferrer">Post öffnen</a>
                  </div>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

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

// ---- Sprint 2: TopRankingSection -----------------------------------------
//
// Sortable Top-N posts per channel (DE + US side-by-side). Backend ships the
// top-10 in engagement_sum desc; the Frontend re-sorts client-side without a
// backend round-trip. The selected sort key is persisted per pair via
// localStorage so Wolf's per-pair preference survives reloads.
//
// The "show more" toggle is intentionally NOT persisted — every reload starts
// at top-5, so the brief always opens with the most important posts above the
// fold and the long tail is one click away.
//
// Graceful degrade: when both channels lack ranked_posts (older persisted
// briefs from before Sprint 2), the section returns null instead of rendering
// an empty shell. The Phase-3 force-regenerate fills in the data.

function rankingSortFn(key) {
  switch (key) {
    case 'likes':           return (a, b) => (b.likes || 0)           - (a.likes || 0);
    case 'activation_rate': return (a, b) => (b.activation_rate || 0) - (a.activation_rate || 0);
    case 'comments':        return (a, b) => (b.comments || 0)        - (a.comments || 0);
    case 'saves':           return (a, b) => (b.saves || 0)           - (a.saves || 0);
    case 'views':
    default:                return (a, b) => (b.views || 0)           - (a.views || 0);
  }
}

function formatRankedNumber(n) {
  const value = Number(n) || 0;
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}k`;
  return String(value);
}

function formatRankedPercent(rate) {
  const value = Number(rate) || 0;
  return `${(value * 100).toFixed(1)}%`;
}

// Sprint 5a: split into { relative, absolute } so the card can show the
// short relative form prominently and expose the absolute date via a
// title-attribute tooltip. Returns empty strings for null/invalid input.
function formatRelativeDate(isoDate) {
  if (!isoDate) return { relative: '', absolute: '' };
  const date = new Date(isoDate);
  if (Number.isNaN(date.getTime())) return { relative: '', absolute: '' };
  const now = new Date();
  const days = Math.floor((now - date) / (1000 * 60 * 60 * 24));
  const absolute = date.toLocaleDateString('de-DE', {
    day: '2-digit', month: '2-digit', year: 'numeric',
  });
  let relative;
  if (days <= 0) relative = 'heute';
  else if (days === 1) relative = 'gestern';
  else if (days < 7) relative = `vor ${days} Tagen`;
  else if (days < 30) relative = `vor ${Math.floor(days / 7)} Wochen`;
  else relative = `vor ${Math.floor(days / 30)} Monaten`;
  return { relative, absolute };
}

// Persistent state via localStorage. Silent fail when storage is full or
// disabled (private-mode Safari) so the UI keeps working — the user just
// loses the persistence on this one device.
function useLocalStorage(key, defaultValue) {
  const [value, setValue] = useState(() => {
    try {
      const stored = window.localStorage.getItem(key);
      return stored !== null ? stored : defaultValue;
    } catch (_) {
      return defaultValue;
    }
  });
  useEffect(() => {
    try {
      window.localStorage.setItem(key, value);
    } catch (_) {
      // localStorage full or disabled — drop write, keep UI working.
    }
  }, [key, value]);
  return [value, setValue];
}

// Sprint 5a: hierarchy redesign. The active sort metric is rendered as
// the primary number (large), the four remaining metrics drop into a
// muted secondary footer. Pre-Sprint-5a all five were rendered equal-
// weight with a colour-only highlight on the active sort.
const RANKED_METRIC_DEFS = [
  { key: 'views',           label: 'Aufrufe',   shortLabel: 'Aufrufe',  read: (p) => formatRankedNumber(p.views) },
  { key: 'likes',           label: 'Reactions', shortLabel: 'Reactions', read: (p) => formatRankedNumber(p.likes) },
  { key: 'activation_rate', label: 'Aktivierung', shortLabel: 'Akt.',   read: (p) => formatRankedPercent(p.activation_rate) },
  { key: 'comments',        label: 'Kommentare', shortLabel: 'Komm.',   read: (p) => formatRankedNumber(p.comments) },
  { key: 'saves',           label: 'Saves',     shortLabel: 'Saves',    read: (p) => formatRankedNumber(p.saves) },
];

function getPrimaryMetric(post, sortKey) {
  const def = RANKED_METRIC_DEFS.find((m) => m.key === sortKey) || RANKED_METRIC_DEFS[0];
  return { key: def.key, value: def.read(post), label: def.label };
}

function getSecondaryMetrics(post, sortKey) {
  return RANKED_METRIC_DEFS
    .filter((def) => def.key !== sortKey)
    .map((def) => ({ key: def.key, value: def.read(post), label: def.shortLabel }));
}

// Plattform → 2-Letter-Akronym, das im Thumbnail-Fallback erscheint, wenn
// post.thumbnail_url fehlt oder das Bild beim Laden 404'd. Brand-Farben
// werden per CSS gesetzt (gleiche Tokens wie .platform-pill).
const PLATFORM_ACRONYM = { tiktok: 'TT', instagram: 'IG', youtube: 'YT' };

// Sprint 5d — Caption-Sanitizer für die Stufe-3-Fallback-Variante.
// TikTok/Instagram-CDN-URLs laufen oft nach 6-24h ab, viele Cards landen
// also auf dem Fallback-Pfad. Wenn ein Filmtitel fehlt, ist die Caption
// häufig die einzige sinnvolle Restinformation — aber nur, wenn sie nicht
// nur aus Hashtags, Emojis und URLs besteht.
//
// Strategie: Hashtags, @-Mentions, URLs und Emojis (BMP + Misc Symbols)
// rauswerfen, dann auf mindestens 3 echte Wörter (>=2 Zeichen) prüfen. Bei
// weniger fällt der Slot auf das Plattform-Akronym (Stufe 4) zurück, weil
// "#fyp #foryou" als Pseudo-Caption schlechter wirkt als ein klares
// Platzhalter-Akronym.
function sanitizeCaption(captionExcerpt) {
  if (!captionExcerpt) return null;
  let cleaned = captionExcerpt.replace(/#\w+/g, '');
  cleaned = cleaned.replace(/@\w+/g, '');
  cleaned = cleaned.replace(/[\u{1F300}-\u{1F9FF}\u{2600}-\u{26FF}\u{2700}-\u{27BF}]/gu, '');
  cleaned = cleaned.replace(/https?:\/\/\S+/g, '');
  cleaned = cleaned.replace(/\s+/g, ' ').trim();
  const words = cleaned.split(/\s+/).filter((w) => w.length >= 2);
  if (words.length < 3) return null;
  return words.slice(0, 6).join(' ');
}

function RankedPostCard({ post, rank, sortKey, platformFilter }) {
  const { relative, absolute } = formatRelativeDate(post.published_at);
  const showPlatformPill = platformFilter === 'all';
  const platform = post.platform || 'tiktok';
  const primaryMetric = getPrimaryMetric(post, sortKey);
  const secondaryMetrics = getSecondaryMetrics(post, sortKey);
  // Sprint 5c — wenn das Brief-Schema asset_id mitbringt, gehen wir über
  // den Thumbnail-Proxy (/api/thumbnails/{asset_id}). Der setzt
  // plattform-spezifische Referer-Header und cached server-seitig, sodass
  // TikTok/Instagram nicht mehr per Browser-Hotlink-Protection blocken.
  // Briefe von vor Sprint 5c haben kein asset_id → wir laden direkt von
  // der CDN-URL (Sprint-5b-Verhalten); der ``onError``-Pfad weiter unten
  // schaltet bei 403/404 auf den Plattform-Akronym-Fallback um.
  const thumbnailSrc = post.asset_id
    ? `/api/thumbnails/${post.asset_id}`
    : post.thumbnail_url;
  const hasTitle = !!post.title_local;
  // Tooltip nur wenn beide Titel vorhanden UND verschieden — die häufigen
  // Fälle (kein title_original; identische Lokalisierung) erzeugen keine
  // Hover-Indirektion.
  const titleTooltip =
    post.title_original && post.title_original !== post.title_local
      ? `Original: ${post.title_original}`
      : undefined;

  // Sprint 5d — Hybrid-Fallback-Stack. Stufe 1 (Bild) bleibt der
  // Default; wenn das <img> nie kommt (kein Source) oder onError feuert
  // (Source-URL abgelaufen, Proxy 404), wird der vorberechnete
  // ``fallbackContent`` sichtbar. Reihenfolge: Filmtitel > sanitisierte
  // Caption > Plattform-Akronym. Die Sprint-5b-Inline-Title-Zeile in der
  // Card-Body bleibt parallel sichtbar — bei Stufe-2-Fallback erscheint
  // der Titel doppelt (groß im Slot, klein als Body-Bestätigung), das
  // ist Absicht.
  const captionFallback = sanitizeCaption(post.caption_excerpt);
  const fallbackContent = post.title_local
    ? { type: 'title', text: post.title_local }
    : captionFallback
    ? { type: 'caption', text: captionFallback }
    : { type: 'acronym', text: PLATFORM_ACRONYM[platform] || 'TT' };

  // imageFailed kippt auf true bei <img onError> oder ist sofort true,
  // wenn gar keine Source-URL existiert. State ist React-managed statt
  // DOM-mutiert (Sprint 5b/5c hatten classList-Mutation), damit die
  // Fallback-Klasse im Markup steht und CSS deterministisch greift.
  const [imageFailed, setImageFailed] = useState(!thumbnailSrc);

  return (
    <a
      href={post.post_url || '#'}
      target="_blank"
      rel="noopener noreferrer"
      className="ranked-post-card"
    >
      <div className="ranked-post-rank-slot">#{rank}</div>

      <div
        className={
          imageFailed
            ? `ranked-post-thumbnail-slot platform-${platform} fallback-active fallback-${fallbackContent.type}`
            : `ranked-post-thumbnail-slot platform-${platform}`
        }
      >
        {thumbnailSrc && !imageFailed && (
          <img
            src={thumbnailSrc}
            alt=""
            loading="lazy"
            onError={() => setImageFailed(true)}
          />
        )}
        {imageFailed && (
          <div className="thumbnail-fallback">
            <span className={`thumbnail-fallback-content fallback-${fallbackContent.type}`}>
              {fallbackContent.text}
            </span>
          </div>
        )}
      </div>

      <div className="ranked-post-body">
        <div className="ranked-post-meta">
          {showPlatformPill && (
            <span className={`platform-pill platform-${platform}`}>{platform}</span>
          )}
          {relative && (
            <span className="ranked-post-date" title={absolute}>{relative}</span>
          )}
        </div>

        {hasTitle && (
          <span className="ranked-post-title-inline" title={titleTooltip}>
            {post.title_local}
          </span>
        )}

        {post.caption_excerpt && (
          <p className="ranked-post-caption">{post.caption_excerpt}</p>
        )}

        <div className="ranked-post-primary-metric">
          <strong>{primaryMetric.value}</strong>
          <span>{primaryMetric.label}</span>
        </div>

        <div className="ranked-post-secondary-metrics">
          {secondaryMetrics.map((m) => (
            <span key={m.key} className="ranked-metric-secondary">
              {m.value} <span>{m.label}</span>
            </span>
          ))}
        </div>
      </div>
    </a>
  );
}

const PLATFORM_LABEL = { tiktok: 'TikTok', instagram: 'Instagram', youtube: 'YouTube' };

// Sprint 4 — multi-platform stats block. One platform-block per entry in
// per_platform, each holding the DE+US ChannelStatsCards plus a
// per-platform CrossMarketCard. Backwards-compat: when per_platform is
// empty (older persisted briefs from before Sprint-4) the component
// falls back to the single-platform render path with the legacy
// aggregation.de_channel / us_channel / cross_market_matches fields.
//
// Empty platform blocks (no DE and no US data) are skipped entirely
// rather than rendered as empty shells — the YT-only-US pairs (Disney,
// Prime, Paramount) thus show one card instead of one card plus an
// empty-state placeholder.
function MultiPlatformStats({ aggregation }) {
  if (!aggregation) return null;
  const perPlatform = Array.isArray(aggregation.per_platform) ? aggregation.per_platform : [];

  if (perPlatform.length === 0) {
    // Legacy path — keep rendering exactly what Sprint-1/2/3 rendered.
    return (
      <>
        <div className="insight-grid-two">
          <ChannelStatsCard stats={aggregation.de_channel} />
          <ChannelStatsCard stats={aggregation.us_channel} />
        </div>
        <CrossMarketCard matches={aggregation.cross_market_matches} />
      </>
    );
  }

  return (
    <div className="multiplatform-stats">
      {perPlatform.map((p) => {
        const hasData = p.de_channel || p.us_channel;
        if (!hasData) return null;
        const label = PLATFORM_LABEL[p.platform] || p.platform;
        return (
          <div key={p.platform} className={`platform-block platform-block-${p.platform}`}>
            <h3 className="platform-block-title">
              <span className={`platform-pill platform-${p.platform}`}>{label}</span>
            </h3>
            <div className="insight-grid-two">
              {p.de_channel && <ChannelStatsCard stats={p.de_channel} />}
              {p.us_channel && <ChannelStatsCard stats={p.us_channel} />}
            </div>
            {p.cross_market_matches?.length > 0 && (
              <CrossMarketCard matches={p.cross_market_matches} />
            )}
          </div>
        );
      })}
    </div>
  );
}

// Sprint 4 — collect ranked_posts from all platforms (or a single one
// when filtered). Walks per_platform if present, otherwise falls back to
// aggregation.de_channel / us_channel for older persisted briefs (Sprint
// 1 contract: a brief written before Sprint-4 has no per_platform field).
function collectRankedPosts(aggregation, market, platformFilter) {
  if (!aggregation) return [];
  const perPlatform = Array.isArray(aggregation.per_platform) ? aggregation.per_platform : [];
  if (perPlatform.length > 0) {
    const posts = [];
    for (const plat of perPlatform) {
      if (platformFilter !== 'all' && plat.platform !== platformFilter) continue;
      const channel = market === 'DE' ? plat.de_channel : plat.us_channel;
      if (channel?.ranked_posts) posts.push(...channel.ranked_posts);
    }
    return posts;
  }
  // Legacy single-platform path for pre-Sprint-4 persisted briefs.
  const channel = market === 'DE' ? aggregation.de_channel : aggregation.us_channel;
  if (!channel?.ranked_posts) return [];
  if (platformFilter !== 'all' && (channel.ranked_posts[0]?.platform || aggregation.platform) !== platformFilter) {
    return [];
  }
  return channel.ranked_posts;
}

function TopRankingSection({ aggregation, pairKey }) {
  const [sortKey, setSortKey] = useLocalStorage(
    `creative-radar:ranking-sort:${pairKey}`,
    'views',
  );
  const [platformFilter, setPlatformFilter] = useLocalStorage(
    `creative-radar:ranking-platform:${pairKey}`,
    'all',
  );
  const [expanded, setExpanded] = useState(false);

  const sortFn = useMemo(() => rankingSortFn(sortKey), [sortKey]);
  const deList = useMemo(
    () => collectRankedPosts(aggregation, 'DE', platformFilter),
    [aggregation, platformFilter],
  );
  const usList = useMemo(
    () => collectRankedPosts(aggregation, 'US', platformFilter),
    [aggregation, platformFilter],
  );

  // Graceful degrade for older persisted briefs (pre-Sprint-2).
  if (deList.length === 0 && usList.length === 0) {
    // If the filter hides everything but there's data on other platforms,
    // keep the section visible with an empty-state hint instead of a
    // sudden disappear-on-select.
    const hasAnyData = collectRankedPosts(aggregation, 'DE', 'all').length > 0
      || collectRankedPosts(aggregation, 'US', 'all').length > 0;
    if (!hasAnyData) return null;
  }

  const visibleCount = expanded ? 10 : 5;
  const deSorted = [...deList].sort(sortFn).slice(0, visibleCount);
  const usSorted = [...usList].sort(sortFn).slice(0, visibleCount);
  const showToggle = deList.length > 5 || usList.length > 5;

  return (
    <section className="ranking-section card">
      <div className="ranking-header">
        <h3>Top-Posts</h3>
        <div className="ranking-controls">
          <select
            value={platformFilter}
            onChange={(e) => setPlatformFilter(e.target.value)}
            className="ranking-sort-select"
            aria-label="Plattform"
          >
            <option value="all">Alle Plattformen</option>
            <option value="tiktok">TikTok</option>
            <option value="instagram">Instagram</option>
            <option value="youtube">YouTube</option>
          </select>
          <select
            value={sortKey}
            onChange={(e) => setSortKey(e.target.value)}
            className="ranking-sort-select"
            aria-label="Sortierung"
          >
            <option value="views">Aufrufe</option>
            <option value="likes">Reactions</option>
            <option value="activation_rate">Aktivierungs-Rate</option>
            <option value="comments">Kommentare</option>
            <option value="saves">Saves</option>
          </select>
          <HelpTooltip text={TOOLTIP_TEXTS.activationRate} label="Was bedeutet Aktivierungs-Rate?" />
        </div>
      </div>

      <div className="ranking-grid">
        <div className="ranking-column">
          <h4>DE</h4>
          {deSorted.length > 0
            ? deSorted.map((p, i) => (
                <RankedPostCard key={p.post_url || `de-${i}`} post={p} rank={i + 1} sortKey={sortKey} platformFilter={platformFilter} />
              ))
            : <p className="ranking-empty">Keine Daten für DE</p>}
        </div>
        <div className="ranking-column">
          <h4>US</h4>
          {usSorted.length > 0
            ? usSorted.map((p, i) => (
                <RankedPostCard key={p.post_url || `us-${i}`} post={p} rank={i + 1} sortKey={sortKey} platformFilter={platformFilter} />
              ))
            : <p className="ranking-empty">Keine Daten für US</p>}
        </div>
      </div>

      {showToggle && (
        <button
          type="button"
          className="ranking-toggle"
          onClick={() => setExpanded((v) => !v)}
        >
          {expanded ? 'Weniger anzeigen' : 'Weitere 5 anzeigen'}
        </button>
      )}
    </section>
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

  const label = FALLBACK_LABEL[pair] || report?.pair_label || pair;

  return (
    <main className="page insight-page">
      <header className="hero">
        <p className="eyebrow">STUDIO-REVIEW</p>
        <h1>{label}</h1>
        <p>Was diese Woche funktioniert, was nicht und wie wir's nutzen.</p>
        {report?.generated_at && (
          <p className="insight-meta-stand">Stand: {formatStand(report.generated_at)}</p>
        )}
      </header>

      {status === 'slow' && (
        <div className="card insight-slow-hint">
          <p className="section-kicker">Einen Moment bitte</p>
          <p>Der Report wird gerade erstellt — das kann einen Moment dauern.</p>
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

          {report.aggregation?.notes?.length > 0 && (
            <div className="card insight-notes">
              <p className="section-kicker">Notes</p>
              <ul>{report.aggregation.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>
            </div>
          )}

          <TopRankingSection
            aggregation={report.aggregation}
            pairKey={pair}
          />

          <LLMOutput output={report.llm_output} raw={report.raw_llm_text} />

          <MultiPlatformStats aggregation={report.aggregation} />
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
