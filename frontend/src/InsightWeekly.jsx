import React, { createContext, useContext, useEffect, useId, useMemo, useRef, useState } from 'react';
import { endpoints } from './api/client';
import StaleWarning from './StaleWarning';
import WeekBanner, { formatDateShort } from './WeekBanner';
import { GLOSSARY, GLOSSARY_ORDER, glossaryDefinition, glossaryFull } from './glossary';

// Pre-fetch labels — used when the URL pair-key arrives before the API
// response (or when the API errors). Mirrors ``PAIRS`` in
// ``backend/app/services/insight_engine.py`` 1:1; keep in sync when adding
// or renaming pairs. Sprint B3 (2026-05-12): alle Pairs sind 3-Markt
// DE+US+UK seit Phase A + universalpictures-Reaktivierung. Lionsgate
// ist US+UK-only (kein DE-Auftritt, Vertrieb via Leonine/Studiocanal).
const FALLBACK_LABEL = {
  warnerbros: 'Warner Bros · DE + US + UK',
  sonypictures: 'Sony Pictures · DE + US + UK',
  primevideo: 'Prime Video · DE + US + UK',
  disney: 'Disney · DE + US + UK',
  netflix: 'Netflix · DE + US + UK',
  paramountpictures: 'Paramount · DE + US + UK',
  universalpictures: 'Universal Pictures · DE + US + UK',
  paramountplus: 'Paramount+ · DE + US + UK',
  lionsgate: 'Lionsgate · US + UK',
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

// V3 Sprint 8 (A) — Info-Punkt am Begriff. Dünner Wrapper über das UNVERÄNDERTE
// HelpTooltip: nimmt einen Glossar-Key und rendert die Kurzerklärung aus der
// zentralen Quelle (glossary.js). So speisen sich Info-Punkte und der Gesamt-
// Glossar-Block aus derselben Quelle. Rendert nichts bei unbekanntem Key.
function GlossaryHint({ term }) {
  const entry = GLOSSARY[term];
  if (!entry) return null;
  return (
    <HelpTooltip
      text={glossaryDefinition(term)}
      label={`Was bedeutet ${entry.de.term}?`}
    />
  );
}

// V3 Sprint 8 (B) — Gesamt-Glossar als ausklappbarer Block, standardmäßig zu.
// Listet alle Begriffe (DE) aus derselben Quelle wie die Info-Punkte. Nutzt das
// native <details>/<summary> — dependency-frei, mobil-tauglich, ohne JS-State.
function GlossaryBlock() {
  return (
    <details className="card glossary-block">
      <summary className="glossary-block-summary">Begriffe erklärt</summary>
      <dl className="glossary-block-list">
        {GLOSSARY_ORDER.map((key) => {
          const entry = GLOSSARY[key];
          if (!entry) return null;
          return (
            <div key={key} className="glossary-block-entry">
              <dt className="glossary-block-term">{entry.de.term}</dt>
              <dd className="glossary-block-def">{glossaryFull(key)}</dd>
            </div>
          );
        })}
      </dl>
    </details>
  );
}

// Sprint 8: die früheren TOOLTIP_TEXTS sind in die zentrale Glossar-Quelle
// (glossary.js) migriert — Coverage/Aktivierung/Cross-Market lesen jetzt von
// dort. Eine Quelle für Info-Punkte UND den Gesamt-Glossar-Block.

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
          <span>Coverage<HelpTooltip text={glossaryDefinition('coverage')} label="Was bedeutet Coverage?" /></span>
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

// Sprint 28.05.2026 (IA-Umbau, Baustein 1) — Headline+TLDR als
// separate Kern-Karte. Bisher steckten sie als erstes Element im
// ``LLMOutput``-Fragment auf Position 8 — also UNTER der Top-Posts-
// Section (Position 7). Das war eine reine Render-Reihenfolge-Anomalie:
// Headline ist GF/CD-Lese, gehoert oben.
//
// Auch der null-/raw-Fallback fuer fehlende LLM-Antwort wandert hierher,
// damit der ``LLMOutput``-Pfad weiter unten sich auf befuellte
// Detail-Sektionen verlassen kann (Commit 2 schiebt die in Klapp-
// Container; ein None-Output dort wuerde leere Container produzieren).
function LLMHeadlineCard({ output, raw }) {
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
    <div className="card insight-headline">
      <p className="section-kicker">Headline</p>
      <h2 className="insight-h2">{output.headline}</h2>
      <p className="insight-tldr">{output.tldr}</p>
    </div>
  );
}

// Sprint 28.05.2026 (IA-Umbau, Baustein 2) — LLMOutput in drei
// Sub-Komponenten aufgeteilt, die jeweils in einen Klapp-Container
// wandern:
// - ``LLMDetailSections``  → Container A "Diese Woche im Detail"
// - ``LLMRoleSections``    → Container B "Kreativ-Empfehlungen"
//                            (Cutter/Motion/Producer + Vergleichbare-Posts;
//                            Commit 3 macht daraus einen Tab-Switcher)
// - ``LLMMetaSection``     → Container D "Methodik / Daten-Caveats"
//
// Render-Reihenfolge in Container A entspricht der Briefing-Vorgabe:
// Worum geht's → Diese Woche → Trends+Actions → Watch-Outs → Tonalitaet →
// Was machen die anderen. (Bisher war konkurrenz weiter oben; Briefing
// stellt es ans Ende des Detail-Containers, wo es zur Markt-Reflexion
// passt.)
function LLMDetailSections({ output }) {
  if (!output) return null;
  return (
    <>
      {output.aktuell_im_fokus?.length > 0 && (
        <div className="card">
          <p className="section-kicker">Worum geht's diese Woche</p>
          <ul className="insight-list">
            {output.aktuell_im_fokus.map((item, i) => (
              <li key={i}>
                {/* V0b: Der Filmname ist KEIN Link mehr — der frühere
                    <a href={item.post_url}> am Titel suggerierte eine
                    Film-Übersicht, führte aber auf einen einzelnen
                    externen Post. Der Name bleibt reiner Text; daneben
                    ein klar beschrifteter Einzel-Post-Link (nur wenn URL
                    vorhanden). */}
                <strong>{item.titel}</strong>
                {item.post_url && (
                  <a
                    href={item.post_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    style={{ marginLeft: '0.5rem', fontSize: '0.85em' }}
                  >
                    Top-Post ansehen ↗
                  </a>
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
    </>
  );
}

// Sprint 28.05.2026 (IA-Umbau, Baustein 3) — die drei Rollen-Bloecke als
// ARIA-konformer Tab-Switcher statt untereinander stehender Cards. Macht
// den "drei Berufs-Linsen auf denselben Befund"-Charakter zum Feature
// statt zum Echo (siehe IA-Befund Teil 2). Aktiver Tab via
// localStorage pro pairKey persistiert (analog zum Sort-Key-Muster).
//
// Tab-Definitionen ausgelagert damit der Tab-Switcher generisch bleibt
// und die Body-Render-Funktionen klar pro Rolle lesbar sind.
const ROLE_TABS = [
  {
    key: 'cutter',
    label: 'Cutter',
    field: 'fuer_cutter',
    render: (data) => (
      <>
        {data.schnitt_pace && (
          <p><strong>Rhythmus:</strong> {data.schnitt_pace}</p>
        )}
        {data.hook_strategie && (
          <p><strong>Hook-Strategie:</strong> {data.hook_strategie}</p>
        )}
        {data.empfohlene_laengen && (
          <p><strong>Empfohlene Längen:</strong> {data.empfohlene_laengen}</p>
        )}
        {/* Sprint 7-iter-2: Compliance-Listen (must_show / no_go) ersetzt
            durch was_diese_woche-Fließtext. Alte persistierte Briefe ohne
            das Feld rendern den Block einfach nicht — graceful degrade. */}
        {data.was_diese_woche && (
          <p><strong>Was diese Woche:</strong> {data.was_diese_woche}</p>
        )}
      </>
    ),
  },
  {
    key: 'motion',
    label: 'Motion-Designer',
    field: 'fuer_motion_designer',
    render: (data) => (
      <>
        {data.caption_style && (
          <p><strong>Caption-Stil:</strong> {data.caption_style}</p>
        )}
        {data.text_overlay && (
          <p><strong>Text-Overlay:</strong> {data.text_overlay}</p>
        )}
        {data.branding_einsatz && (
          <p><strong>Branding-Einsatz:</strong> {data.branding_einsatz}</p>
        )}
        {data.was_diese_woche && (
          <p><strong>Was diese Woche:</strong> {data.was_diese_woche}</p>
        )}
      </>
    ),
  },
  {
    key: 'producer',
    label: 'Creative Producer',
    field: 'fuer_creative_producer',
    render: (data) => (
      <>
        {data.strategische_pattern && (
          <p><strong>Strategische Pattern:</strong> {data.strategische_pattern}</p>
        )}
        {data.cross_market_chancen && (
          <p><strong>Cross-Market-Chancen:</strong> {data.cross_market_chancen}</p>
        )}
        {data.format_empfehlungen && (
          <p><strong>Format-Empfehlungen:</strong> {data.format_empfehlungen}</p>
        )}
        {data.was_diese_woche && (
          <p><strong>Was diese Woche:</strong> {data.was_diese_woche}</p>
        )}
      </>
    ),
  },
];

function RolesTabSwitcher({ output, pairKey, initialActiveTab }) {
  const printMode = useContext(PrintModeContext);
  // Wenn alle drei Rollen-Felder null sind, faellt der Switcher weg —
  // sonst zeigt er einen leeren Empty-State, was an dieser Stelle nur
  // Layout-Rauschen ist (Vergleichbare-Posts rendert separat als Footer).
  const anyData = ROLE_TABS.some((t) => output?.[t.field]);
  // Hook-Reihenfolge MUSS vor jedem conditional return stehen, sonst
  // bricht React zwischen Renders die Hook-Identitaet (Container kann
  // dynamisch zu- und aufklappen).
  const [activeKey, setActiveKey] = useLocalStorage(
    `creative-radar:roles-tab:${pairKey}`,
    initialActiveTab || 'cutter',
  );
  // Sprint 28.05.2026 (IA-Umbau, Baustein 4): wenn der Ansichts-Modus
  // einen neuen ``initialActiveTab`` reinreicht (Switch auf
  // "Cutter-Sicht" / "Producer-Sicht"), den aktiven Tab dort hin
  // springen lassen. ``viewMode='all'`` liefert ``undefined`` und der
  // Effekt skippt — User-Wahl bleibt persistiert.
  useEffect(() => {
    if (initialActiveTab && initialActiveTab !== activeKey) {
      setActiveKey(initialActiveTab);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialActiveTab]);
  if (!anyData) return null;

  const activeTab = ROLE_TABS.find((t) => t.key === activeKey) || ROLE_TABS[0];
  const activeData = output?.[activeTab.field];

  // Sprint 28.05.2026 (PDF-Baustein 2): im PrintMode rendert der
  // Switcher ALLE drei Rollen-Panels statt nur den aktiven. Sonst
  // wuerde das PDF die zwei nicht-aktiven Tabs verschlucken
  // (kritisches Briefing-Done-Kriterium). Die Tab-Leiste ist im Print-
  // CSS via display:none ausgeblendet — nur die drei Panels mit ihren
  // Labels als Sub-Headings sind sichtbar.
  if (printMode) {
    return (
      <div className="roles-tabs card roles-tabs-print">
        {ROLE_TABS.map((t) => {
          const data = output?.[t.field];
          return (
            <div key={t.key} className="roles-tab-panel roles-tab-panel-print">
              <h4 className="roles-print-label">Für den {t.label}</h4>
              {data ? t.render(data) : (
                <p className="roles-tab-empty-text">
                  Für die Rolle "{t.label}" liegt in diesem Brief keine
                  Empfehlung vor.
                </p>
              )}
            </div>
          );
        })}
      </div>
    );
  }

  return (
    <div className="roles-tabs card">
      <div className="roles-tabs-tablist" role="tablist" aria-label="Kreativ-Empfehlungen pro Rolle">
        {ROLE_TABS.map((t) => {
          const hasData = !!output?.[t.field];
          const isActive = t.key === activeKey;
          return (
            <button
              key={t.key}
              type="button"
              role="tab"
              aria-selected={isActive}
              aria-controls={`roles-panel-${t.key}`}
              id={`roles-tab-${t.key}`}
              className={`roles-tab ${isActive ? 'is-active' : ''} ${hasData ? '' : 'is-empty'}`}
              onClick={() => setActiveKey(t.key)}
            >
              {t.label}
              {!hasData && <span className="roles-tab-emptymark" aria-hidden="true"> ·</span>}
            </button>
          );
        })}
      </div>
      <div
        className="roles-tab-panel"
        role="tabpanel"
        id={`roles-panel-${activeTab.key}`}
        aria-labelledby={`roles-tab-${activeTab.key}`}
      >
        {activeData ? (
          activeTab.render(activeData)
        ) : (
          <p className="roles-tab-empty-text">
            Für die Rolle "{activeTab.label}" liegt in diesem Brief keine
            Empfehlung vor.
          </p>
        )}
      </div>
    </div>
  );
}

function LLMRoleSections({ output, pairKey, initialActiveTab }) {
  if (!output) return null;
  return (
    <>
      <RolesTabSwitcher
        output={output}
        pairKey={pairKey}
        initialActiveTab={initialActiveTab}
      />

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
    </>
  );
}

function LLMMetaSection({ output }) {
  if (!output) return null;
  if (!(output.risks?.length > 0 || output.data_caveats?.length > 0)) return null;
  return (
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
    // Sprint 28.05.2026 (Punkt 4) — Breakout-Score. Posts ohne Score
    // (Channel-Pool < 5 oder std=0) landen ans Ende, damit der
    // sortierte Block die scoreless Posts nicht aus dem Top-N
    // verdraengt.
    case 'breakout':        return (a, b) => {
      const aw = a.breakout_score?.weighted_score;
      const bw = b.breakout_score?.weighted_score;
      if (aw == null && bw == null) return 0;
      if (aw == null) return 1;
      if (bw == null) return -1;
      return bw - aw;
    };
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

// Sprint 28.05.2026 (IA-Umbau, Baustein 2) — Klapp-Container.
//
// Pragmatische Entscheidung: KEINE native ``<details>``. Begruendung:
// 1. PDF-Baustein 2 braucht einen ``beforeprint``-Handler, der alle
//    Container temporaer oeffnet. Mit nativem details muesste das ueber
//    ``document.querySelectorAll('details').forEach(d=>d.open=true)``
//    gehen — geht, ist aber unschoener als ein zentraler React-State.
// 2. Der Ansichts-Modus-Umschalter (Commit 4) steuert die
//    ``defaultOpen``-Props per Container. Mit React-State idiomatisch
//    via Prop-Pipeline; native details haetten wir per imperative
//    DOM-Manipulation steuern muessen.
// 3. Lazy-Mount des Klapp-Inhalts (``{isOpen && children}``) ist mit
//    React-State trivial; bei nativem details muss man eigentlich auch
//    React-State fahren, weil der Inhalt nicht magisch verschwindet —
//    das wuerde den details-Vorteil aufheben.
//
// State-Modell: ``defaultOpen``-Prop steuert den Initial-State; lokaler
// useState merkt sich ab dann den User-Toggle (Aenderungen am
// ``defaultOpen``-Prop nach Mount werden ignoriert — sonst kollidiert
// der ViewMode-Switch in Commit 4 mit User-Klicks). KEIN
// per-Container-localStorage: der ViewMode-Switch ist der autoritative
// Default-Setter, User-Klicks gelten bis Page-Refresh.
//
// A11y: button mit aria-expanded + aria-controls; chevron rotiert per
// CSS; Header-Tastatur-Aktivierung via Enter/Space (Button-Default).
// Sprint 28.05.2026 (PDF-Baustein 2) — Print-Mode-Context.
// PDF-Button setzt ``printMode=true``, CollapsibleCard und
// RolesTabSwitcher lesen es und rendern alle Inhalte (statt nur den
// aktiven Klapp-/Tab-State). So landen alle 20 Sektionen vollstaendig
// im DOM, BEVOR window.print() den Browser-Print-Dialog oeffnet —
// sonst koennte das @media-print-CSS nichts sichtbar machen, was React
// im aktuellen State nicht gemountet hat (Lazy-Mount-Pattern aus #192).
// Default false: normaler Browser-Modus arbeitet exakt wie vorher.
const PrintModeContext = createContext(false);

function CollapsibleCard({
  title,
  subtitle,
  defaultOpen = false,
  variant = 'card',
  children,
}) {
  const printMode = useContext(PrintModeContext);
  const [isOpen, setIsOpen] = useState(defaultOpen);
  // effectiveOpen: im PrintMode IMMER offen, sonst der User-State.
  // Aenderung gegenueber #192: die Render-Bedingung unten reagiert auf
  // ``effectiveOpen``, der State-Zyklus (User-Toggle, ViewMode-Sync)
  // bleibt unveraendert — d.h. wenn der Print-Dialog zurueckkommt
  // (afterprint leert printMode), faellt die Karte automatisch in
  // ihren vorherigen User-State.
  const effectiveOpen = printMode || isOpen;
  // Sprint 28.05.2026 (IA-Umbau, Baustein 4): wenn der Ansichts-Modus-
  // Umschalter den ``defaultOpen``-Prop aendert (z.B. von "all" auf
  // "cutter"), den Klapp-Zustand neu setzen. User-Toggles innerhalb
  // desselben ViewMode (gleicher defaultOpen-Wert) bleiben erhalten —
  // useEffect feuert nur, wenn die Dep tatsaechlich wechselt.
  useEffect(() => {
    setIsOpen(defaultOpen);
  }, [defaultOpen]);
  const contentId = useId();
  const headerId = useId();
  const className = variant === 'inner'
    ? `collapsible-inner ${effectiveOpen ? 'is-open' : ''}`
    : `card collapsible-card ${effectiveOpen ? 'is-open' : ''}`;
  return (
    <section className={className}>
      <button
        type="button"
        className="collapsible-header"
        aria-expanded={effectiveOpen}
        aria-controls={contentId}
        id={headerId}
        onClick={() => setIsOpen((v) => !v)}
      >
        <span className="collapsible-chevron" aria-hidden="true">▸</span>
        <span className="collapsible-title">{title}</span>
        {subtitle && <span className="collapsible-subtitle">{subtitle}</span>}
      </button>
      {effectiveOpen && (
        <div
          className="collapsible-content"
          id={contentId}
          role="region"
          aria-labelledby={headerId}
        >
          {children}
        </div>
      )}
    </section>
  );
}

// Sprint 5a: hierarchy redesign. The active sort metric is rendered as
// the primary number (large), the four remaining metrics drop into a
// muted secondary footer. Pre-Sprint-5a all five were rendered equal-
// weight with a colour-only highlight on the active sort.
// Sprint 28.05.2026 (Punkt 4): formatiert den Breakout-Multiplier als
// "4,7x" mit deutschem Komma. ``–`` wenn der Post keinen Score traegt
// (Channel-Pool < 5 Posts oder std=0). Die Sort-Funktion ``rankingSortFn``
// drueckt scoreless Posts ohnehin ans Ende, das ``–`` rendert daher nur
// wenn ein Post sichtbar aber score-los ist.
function formatBreakoutMultiplier(post) {
  const mult = post.breakout_score?.multiplier;
  if (mult == null) return '–';
  return `${mult.toFixed(1).replace('.', ',')}x`;
}

const RANKED_METRIC_DEFS = [
  { key: 'views',           label: 'Aufrufe',   shortLabel: 'Aufrufe',  read: (p) => formatRankedNumber(p.views) },
  { key: 'likes',           label: 'Reactions', shortLabel: 'Reactions', read: (p) => formatRankedNumber(p.likes) },
  { key: 'activation_rate', label: 'Aktivierung', shortLabel: 'Akt.',   read: (p) => formatRankedPercent(p.activation_rate) },
  { key: 'comments',        label: 'Kommentare', shortLabel: 'Komm.',   read: (p) => formatRankedNumber(p.comments) },
  { key: 'saves',           label: 'Saves',     shortLabel: 'Saves',    read: (p) => formatRankedNumber(p.saves) },
  // Sprint 28.05.2026 (Punkt 4) — Breakout-Score als Sortier-Option.
  // shortLabel "Breakout" damit die Sekundaer-Footer-Zeile kein
  // Sonderzeichen tragen muss (vermeidet Umbruch).
  { key: 'breakout',        label: 'Breakout',  shortLabel: 'Breakout', read: formatBreakoutMultiplier },
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
// per_platform, each holding the DE+US ChannelStatsCards. Backwards-
// compat: when per_platform is empty (older persisted briefs from before
// Sprint-4) the component falls back to the single-platform render path
// with the legacy aggregation.de_channel / us_channel fields.
//
// Empty platform blocks (no DE and no US data) are skipped entirely
// rather than rendered as empty shells — the YT-only-US pairs (Disney,
// Prime, Paramount) thus show one card instead of one card plus an
// empty-state placeholder.
//
// Sprint 28.05.2026 (Punkt 1): die CrossMarketCards (vorher pro
// Plattform hier gerendert) sind in CrossMarketHeadlineSection oben im
// Brief gebuendelt — Aussage + Belege jetzt direkt unter dem Hero.
function MultiPlatformStats({ aggregation }) {
  if (!aggregation) return null;
  const perPlatform = Array.isArray(aggregation.per_platform) ? aggregation.per_platform : [];

  if (perPlatform.length === 0) {
    // Legacy path — keep rendering exactly what Sprint-1/2/3 rendered,
    // plus Sprint B3 (2026-05-12) die UK-Spalte. Backwards-Compat: bei
    // Pre-B1-Briefs ist aggregation.uk_channel undefined/None und
    // ChannelStatsCard rendert ein "Kein Channel"-Placeholder.
    //
    // Sprint 28.05.2026 (Punkt 1): die CrossMarketCards (DE↔US, DE↔UK,
    // US↔UK) sind in die Headline-Sektion oben (CrossMarketHeadline-
    // Section) gewandert — hier nur noch ChannelStats.
    return (
      <div className="insight-grid-three">
        <ChannelStatsCard stats={aggregation.de_channel} />
        <ChannelStatsCard stats={aggregation.us_channel} />
        <ChannelStatsCard stats={aggregation.uk_channel} />
      </div>
    );
  }

  // Sprint 28.05.2026 (IA-Umbau, Baustein 2): pro Plattform jetzt ein
  // Sub-Klapp-Container statt einer dauerhaft-offenen Block-Hierarchie.
  // Default: alle Plattformen zu (Cutter braucht's selten, GF nie).
  // Lazy-Mount der Channel-Cards spart Initial-Render-Cost — die
  // ChannelStatsCard-Komponente macht Re-Sorting auf Top-Posts und
  // Hashtag-Counts pro Render, das summiert sich bei drei Plattformen
  // x drei Maerkten.
  return (
    <div className="multiplatform-stats">
      {perPlatform.map((p) => {
        const hasData = p.de_channel || p.us_channel || p.uk_channel;
        if (!hasData) return null;
        const label = PLATFORM_LABEL[p.platform] || p.platform;
        return (
          <CollapsibleCard
            key={p.platform}
            variant="inner"
            title={label}
            subtitle="DE / US / UK Channel-Stats"
            defaultOpen={false}
          >
            <div className={`platform-block platform-block-${p.platform}`}>
              <div className="insight-grid-three">
                {p.de_channel && <ChannelStatsCard stats={p.de_channel} />}
                {p.us_channel && <ChannelStatsCard stats={p.us_channel} />}
                {p.uk_channel && <ChannelStatsCard stats={p.uk_channel} />}
              </div>
            </div>
          </CollapsibleCard>
        );
      })}
    </div>
  );
}

// Sprint 28.05.2026 (Punkt 1) — "Drei Märkte, ein Film".
// Headline-Sektion oben im Brief: pro Achse (DE↔US, DE↔UK, US↔UK) die
// LLM-Aussage und die belegenden Matches beieinander. Loest die fruehere
// Verteilung des Cross-Market-Materials ueber LLMOutput (Prosa) und
// MultiPlatformStats (Cards) auf — der USP "derselbe Film, drei Maerkte"
// ist jetzt das Erste, was man im Brief sieht.
//
// Match-Aggregation ueber alle Plattformen: ``aggregation.per_platform``
// haelt pro TT/IG/YT die Matches; das Legacy-Top-Level-Feld
// ``aggregation.cross_market_matches`` mirror nur die ERSTE Plattform
// (siehe backend ``aggregate_pair`` Zeile ~2169). Fuer die Headline
// brauchen wir den vollen Cross-Market-Ueberblick → wir walken
// per_platform und sammeln pro Achse alle Matches, mit
// Plattform-Tag pro Eintrag. Persistierte Briefs vor Sprint 4 haben
// kein per_platform → wir fallen auf die top-level Felder zurueck.
//
// KEINE Dedupe nach match_key: ein "Mortal Kombat"-Match auf TT und IG
// sind reale, getrennte Datenpunkte mit unterschiedlichen Engagement-
// Profilen — das Zusammenfuehren wuerde Information loeschen.
function collectCrossMarketMatches(aggregation, axisField) {
  if (!aggregation) return [];
  const perPlatform = Array.isArray(aggregation.per_platform) ? aggregation.per_platform : [];
  if (perPlatform.length > 0) {
    const entries = [];
    for (const plat of perPlatform) {
      const matches = plat[axisField];
      if (Array.isArray(matches)) {
        for (const m of matches) {
          entries.push({ ...m, _platform: plat.platform });
        }
      }
    }
    return entries;
  }
  const legacy = aggregation[axisField];
  return Array.isArray(legacy) ? legacy.map((m) => ({ ...m, _platform: null })) : [];
}

// Ein Achsen-Block: Insight-Text (Aussage) + Match-Card (Belege).
// Wenn ``insightText`` und ``matches`` beide leer sind, rendert der
// Block einen Klartext-Fallback (analog #185) — kein leerer Slot.
function CrossMarketAxisBlock({ axisLabel, axisShort, insightText, matches }) {
  const hasMatches = matches.length > 0;
  if (!insightText && !hasMatches) {
    return (
      <div className="cm-axis-block cm-axis-empty">
        <h4 className="cm-axis-label">{axisLabel}</h4>
        <p className="cm-axis-empty-text">
          {`Keine Titel-Parallelen zwischen ${axisShort.a} und ${axisShort.b} in diesem Zeitraum.`}
        </p>
      </div>
    );
  }
  return (
    <div className="cm-axis-block">
      <h4 className="cm-axis-label">{axisLabel}</h4>
      {insightText && <p className="cm-axis-insight">{insightText}</p>}
      {hasMatches ? (
        <ol className="insight-cross-market">
          {matches.map((m, i) => (
            <li key={`${m.match_key}-${m._platform || 'legacy'}-${i}`}>
              <strong>{m.title || m.match_key}</strong>
              {m._platform && (
                <span className={`platform-pill platform-${m._platform} cm-axis-platform-pill`}>
                  {PLATFORM_LABEL[m._platform] || m._platform}
                </span>
              )}
              <div>
                {axisShort.a}: {formatNumber(m.de_engagement)}{m.de_duration_seconds != null && ` · ${m.de_duration_seconds}s`}
                {' / '}{axisShort.b}: {formatNumber(m.us_engagement)}{m.us_duration_seconds != null && ` · ${m.us_duration_seconds}s`}
              </div>
              <div className="insight-cross-market-links">
                {m.de_post_url && <a href={m.de_post_url} target="_blank" rel="noreferrer">{`${axisShort.a}-Post`}</a>}
                {m.us_post_url && <a href={m.us_post_url} target="_blank" rel="noreferrer">{`${axisShort.b}-Post`}</a>}
              </div>
            </li>
          ))}
        </ol>
      ) : (
        <p className="cm-axis-empty-text">
          {`Keine Titel-Parallelen zwischen ${axisShort.a} und ${axisShort.b} in diesem Zeitraum.`}
        </p>
      )}
    </div>
  );
}

function CrossMarketHeadlineSection({ aggregation, llmOutput }) {
  // Rules-of-Hooks: useMemo MUSS vor jedem conditional return laufen,
  // sonst aendert sich die Hook-Reihenfolge zwischen Renders. Wenn
  // ``aggregation`` null ist, liefern die Helper leere Listen und der
  // anythingToShow-Check unten schickt uns durch das null-Return.
  const deUsMatches = useMemo(
    () => collectCrossMarketMatches(aggregation, 'cross_market_matches'),
    [aggregation],
  );
  const deUkMatches = useMemo(
    () => collectCrossMarketMatches(aggregation, 'de_uk_matches'),
    [aggregation],
  );
  const usUkMatches = useMemo(
    () => collectCrossMarketMatches(aggregation, 'us_uk_matches'),
    [aggregation],
  );

  const insight = llmOutput?.cross_market_insight;
  const deUsText = insight?.de_vs_us;
  const deUkText = insight?.de_vs_uk;
  const usUkText = insight?.us_vs_uk;
  const transferOpportunity = insight?.transfer_opportunity;

  const anythingToShow =
    deUsMatches.length > 0 || deUkMatches.length > 0 || usUkMatches.length > 0 ||
    deUsText || deUkText || usUkText || transferOpportunity;
  if (!anythingToShow) return null;

  return (
    <CollapsibleCard
      title="Drei Märkte, ein Film — DE / US / UK im Vergleich"
      defaultOpen={false}
    >
      <div className="cm-axis-grid">
        <CrossMarketAxisBlock
          axisLabel="DE ↔ US"
          axisShort={{ a: 'DE', b: 'US' }}
          insightText={deUsText}
          matches={deUsMatches}
        />
        <CrossMarketAxisBlock
          axisLabel="DE ↔ UK"
          axisShort={{ a: 'DE', b: 'UK' }}
          insightText={deUkText}
          matches={deUkMatches}
        />
        <CrossMarketAxisBlock
          axisLabel="US ↔ UK"
          axisShort={{ a: 'US', b: 'UK' }}
          insightText={usUkText}
          matches={usUkMatches}
        />
      </div>
      {transferOpportunity && (
        <p className="cm-transfer-opportunity">
          <strong>Transfer-Opportunity:</strong> {transferOpportunity}
        </p>
      )}
    </CollapsibleCard>
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
      const channel = plat[`${market.toLowerCase()}_channel`];
      if (channel?.ranked_posts) posts.push(...channel.ranked_posts);
    }
    return posts;
  }
  // Legacy single-platform path for pre-Sprint-4 persisted briefs.
  const channel = aggregation[`${market.toLowerCase()}_channel`];
  if (!channel?.ranked_posts) return [];
  if (platformFilter !== 'all' && (channel.ranked_posts[0]?.platform || aggregation.platform) !== platformFilter) {
    return [];
  }
  return channel.ranked_posts;
}

// Sprint 28.05.2026 (Punkt 4) — backend-autoritative Breakout-Liste pro
// Channel. Spiegelt ``collectRankedPosts`` 1:1 fuer das Feld
// ``channel.breakouts`` (Top-3 RankedPosts nach
// ``breakout_score.weighted_score`` desc). Persistierte Briefs vor
// diesem Sprint haben das Feld nicht → Default leere Liste; die
// Section blendet sich dann fuer den jeweiligen Channel aus.
function collectBreakouts(aggregation, market, platformFilter) {
  if (!aggregation) return [];
  const perPlatform = Array.isArray(aggregation.per_platform) ? aggregation.per_platform : [];
  if (perPlatform.length > 0) {
    const posts = [];
    for (const plat of perPlatform) {
      if (platformFilter !== 'all' && plat.platform !== platformFilter) continue;
      const channel = plat[`${market.toLowerCase()}_channel`];
      if (channel?.breakouts) posts.push(...channel.breakouts);
    }
    // Mehrere Plattformen → erneut nach weighted_score sortieren, damit
    // die Top-N ueber alle gefilterten Plattformen sauber zusammenkommen
    // und nicht "alle TT Top-3, dann alle IG Top-3" als Reihenfolge
    // entsteht.
    posts.sort((a, b) => {
      const aw = a.breakout_score?.weighted_score ?? -Infinity;
      const bw = b.breakout_score?.weighted_score ?? -Infinity;
      return bw - aw;
    });
    return posts;
  }
  const channel = aggregation[`${market.toLowerCase()}_channel`];
  if (!channel?.breakouts) return [];
  if (platformFilter !== 'all' && (channel.breakouts[0]?.platform || aggregation.platform) !== platformFilter) {
    return [];
  }
  return channel.breakouts;
}

// Sprint 28.05.2026 (Punkt 4) — "Breakouts dieser Woche".
// Curated-View ueber den Top-Posts-Block: zeigt pro Markt die hoechsten
// relativen Ausreisser (multiplier), nicht die hoechsten Absolut-
// Engagement-Spitzen. Loest die Strukturverzerrung des Absolut-Rankings,
// in der grosse Accounts immer "gewinnen".
//
// Layout-Entscheidung: kompakte Variante (max 3 Cards pro Markt, kein
// Expand-Toggle). Wer mehr will, nutzt den Top-Posts-Block mit
// sortKey="breakout" — diese Section ist die Eintritts-Karte, der Block
// die Vertiefung. Wenn ueberall ``breakouts`` leer ist (Channels < 5
// Posts, Pre-Sprint-Brief), rendert die Section ``null`` und nimmt
// keinen Layout-Slot ein.
function BreakoutsSection({ aggregation, pairKey }) {
  const [platformFilter] = useLocalStorage(
    `creative-radar:ranking-platform:${pairKey}`,
    'all',
  );
  const deList = useMemo(
    () => collectBreakouts(aggregation, 'DE', platformFilter).slice(0, 3),
    [aggregation, platformFilter],
  );
  const usList = useMemo(
    () => collectBreakouts(aggregation, 'US', platformFilter).slice(0, 3),
    [aggregation, platformFilter],
  );
  const ukList = useMemo(
    () => collectBreakouts(aggregation, 'UK', platformFilter).slice(0, 3),
    [aggregation, platformFilter],
  );
  if (deList.length === 0 && usList.length === 0 && ukList.length === 0) {
    return null;
  }
  return (
    <CollapsibleCard title="Breakouts dieser Woche" defaultOpen={false}>
      <p className="breakouts-intro">
        Posts, die deutlich über dem Kanal-Schnitt liegen — relativ, nicht absolut.
        <GlossaryHint term="breakout" />
      </p>
      <div className="ranking-grid">
        <div className="ranking-column">
          <h4>DE</h4>
          {deList.length > 0
            ? deList.map((p, i) => (
                <RankedPostCard key={p.post_url || `de-break-${i}`} post={p} rank={i + 1} sortKey="breakout" platformFilter={platformFilter} />
              ))
            : <p className="ranking-empty">Keine Breakouts</p>}
        </div>
        <div className="ranking-column">
          <h4>US</h4>
          {usList.length > 0
            ? usList.map((p, i) => (
                <RankedPostCard key={p.post_url || `us-break-${i}`} post={p} rank={i + 1} sortKey="breakout" platformFilter={platformFilter} />
              ))
            : <p className="ranking-empty">Keine Breakouts</p>}
        </div>
        <div className="ranking-column">
          <h4>UK</h4>
          {ukList.length > 0
            ? ukList.map((p, i) => (
                <RankedPostCard key={p.post_url || `uk-break-${i}`} post={p} rank={i + 1} sortKey="breakout" platformFilter={platformFilter} />
              ))
            : <p className="ranking-empty">Keine Breakouts</p>}
        </div>
      </div>
    </CollapsibleCard>
  );
}

// Zeitraum-Filter (27.05.2026) — client-side Re-Filter auf die schon
// geladene ranked_posts-Liste. Der persistierte Brief enthaelt 30 Tage
// Daten (aggregate_pair window_days=30); ein 7d-Filter schneidet nur die
// juengsten 7 Tage daraus heraus, kein API-Call. Posts ohne published_at
// (Optional[datetime] im RankedPost-Schema) werden im 7d-Filter
// ausgeschlossen — der detected_at-OR-Branch aus dem Backend laesst sich
// hier nicht spiegeln, weil RankedPost nur published_at traegt; die
// betroffene Menge ist im Praxis-Bestand verschwindend klein und
// pragmatisch akzeptabel.
const RANGE_WINDOWS = {
  '7d':  7 * 24 * 60 * 60 * 1000,
  '30d': null,   // 30d = Status quo, kein Filter
};

function applyRangeFilter(posts, range) {
  const windowMs = RANGE_WINDOWS[range];
  if (windowMs == null) return posts;
  const cutoff = Date.now() - windowMs;
  return posts.filter((p) => {
    if (!p.published_at) return false;
    const ts = new Date(p.published_at).getTime();
    return Number.isFinite(ts) && ts >= cutoff;
  });
}

function TopRankingSection({ aggregation, pairKey }) {
  const printMode = useContext(PrintModeContext);
  const [sortKey, setSortKey] = useLocalStorage(
    `creative-radar:ranking-sort:${pairKey}`,
    'views',
  );
  const [platformFilter, setPlatformFilter] = useLocalStorage(
    `creative-radar:ranking-platform:${pairKey}`,
    'all',
  );
  const [rangeFilter, setRangeFilter] = useLocalStorage(
    `creative-radar:ranking-range:${pairKey}`,
    '30d',
  );
  const [expanded, setExpanded] = useState(false);

  const sortFn = useMemo(() => rankingSortFn(sortKey), [sortKey]);
  const deList = useMemo(
    () => applyRangeFilter(collectRankedPosts(aggregation, 'DE', platformFilter), rangeFilter),
    [aggregation, platformFilter, rangeFilter],
  );
  const usList = useMemo(
    () => applyRangeFilter(collectRankedPosts(aggregation, 'US', platformFilter), rangeFilter),
    [aggregation, platformFilter, rangeFilter],
  );
  const ukList = useMemo(
    () => applyRangeFilter(collectRankedPosts(aggregation, 'UK', platformFilter), rangeFilter),
    [aggregation, platformFilter, rangeFilter],
  );

  // Graceful degrade for older persisted briefs (pre-Sprint-2).
  if (deList.length === 0 && usList.length === 0 && ukList.length === 0) {
    // If the filter hides everything but there's data on other platforms /
    // anderen Zeitraeumen, keep the section visible with an empty-state
    // hint instead of a sudden disappear-on-select. ``'all'`` + ``'30d'``
    // ist der unfilterte Vergleichs-Stand.
    const hasAnyData = collectRankedPosts(aggregation, 'DE', 'all').length > 0
      || collectRankedPosts(aggregation, 'US', 'all').length > 0
      || collectRankedPosts(aggregation, 'UK', 'all').length > 0;
    if (!hasAnyData) return null;
  }

  // Sprint 28.05.2026 (PDF-Baustein 2): im Print alle Top-Posts (bis
  // zum ranked_posts-limit von 10) zeigen, sonst der "Mehr"-Toggle-
  // State des Users.
  const visibleCount = printMode || expanded ? 10 : 5;
  const deSorted = [...deList].sort(sortFn).slice(0, visibleCount);
  const usSorted = [...usList].sort(sortFn).slice(0, visibleCount);
  const ukSorted = [...ukList].sort(sortFn).slice(0, visibleCount);
  const showToggle = deList.length > 5 || usList.length > 5 || ukList.length > 5;
  const emptyLabel = rangeFilter === '7d'
    ? 'Keine Posts in den letzten 7 Tagen'
    : 'Keine Daten';

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
            value={rangeFilter}
            onChange={(e) => setRangeFilter(e.target.value)}
            className="ranking-sort-select"
            aria-label="Zeitraum"
          >
            <option value="30d">Letzte 30 Tage</option>
            <option value="7d">Letzte 7 Tage</option>
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
            <option value="breakout">Breakout (relativ)</option>
          </select>
          <HelpTooltip text={glossaryDefinition('activationRate')} label="Was bedeutet Aktivierungs-Rate?" />
        </div>
      </div>

      <div className="ranking-grid">
        <div className="ranking-column">
          <h4>DE</h4>
          {deSorted.length > 0
            ? deSorted.map((p, i) => (
                <RankedPostCard key={p.post_url || `de-${i}`} post={p} rank={i + 1} sortKey={sortKey} platformFilter={platformFilter} />
              ))
            : <p className="ranking-empty">{emptyLabel} für DE</p>}
        </div>
        <div className="ranking-column">
          <h4>US</h4>
          {usSorted.length > 0
            ? usSorted.map((p, i) => (
                <RankedPostCard key={p.post_url || `us-${i}`} post={p} rank={i + 1} sortKey={sortKey} platformFilter={platformFilter} />
              ))
            : <p className="ranking-empty">{emptyLabel} für US</p>}
        </div>
        <div className="ranking-column">
          <h4>UK</h4>
          {ukSorted.length > 0
            ? ukSorted.map((p, i) => (
                <RankedPostCard key={p.post_url || `uk-${i}`} post={p} rank={i + 1} sortKey={sortKey} platformFilter={platformFilter} />
              ))
            : <p className="ranking-empty">{emptyLabel} für UK</p>}
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

// ---- V3 Sprint 2: film-zentrierte Detailansicht --------------------------
//
// Inline-Expand in der Overview (KEIN Router): ein Dropdown speist sich aus
// den bereits geladenen ``aktuell_im_fokus``-Titeln (nur die mit gesetzter
// ``title_id`` — der deterministische Enrichment-Wert aus V3 Sprint 1). Die
// Auswahl lädt ``GET /api/insights/title/{id}/posts`` und rendert die Posts
// in drei festen Markt-Spalten DE/US/UK, je Spalte nach Plattform gruppiert.
//
// Die drei Spalten werden IMMER gerendert (Shape-Garantie des Backends +
// defensives Markt-Lookup), leere Märkte zeigen einen dezenten Hinweis statt
// zu verschwinden — so bleibt der DE/US/UK-Vergleich visuell stabil.

const TITLE_DETAIL_MARKETS = ['DE', 'US', 'UK'];

// V3 Sprint 3 — Top-Performer-Sortierung der Film-Detailseite. Optik/Labels
// gespiegelt von der Top-Posts-Ansicht (RANKED_METRIC_DEFS); client-seitig, da
// der Detail-Endpoint kein Limit/Pagination hat und die Post-Zahl pro Titel
// klein ist (kein Re-Fetch nötig). Default Engagement-Rate; das Backend liefert
// die erste Ausgabe bereits engagement-sortiert.
const FILM_SORT_OPTIONS = [
  { key: 'engagement', label: 'Engagement-Rate' },
  { key: 'views', label: 'Aufrufe' },
  { key: 'likes', label: 'Reactions' },
];

function filmDetailSortFn(key) {
  switch (key) {
    case 'views': return (a, b) => (b.views || 0) - (a.views || 0);
    case 'likes': return (a, b) => (b.likes || 0) - (a.likes || 0);
    case 'engagement':
    default:      return (a, b) => (b.engagement_rate || 0) - (a.engagement_rate || 0);
  }
}

// V3 Sprint 4 — Markt-Vergleichsleiste. Pure: aus EINEM TitleMarketPosts
// ({ market, platforms[] }) die drei Aggregat-Kennzahlen der Leiste ableiten.
// Kein JSX, keine Seiteneffekte.
//
// - posts: Summe aller Posts über alle Plattform-Buckets des Markts.
// - views: Σ (views or 0) über alle Posts.
// - engagementRate: Σ(likes+comments) / Σ(views), NUR über Posts mit views>0
//   (gespiegelt vom Backend ``_post_engagement_rate``; Post-ER NICHT mitteln).
//   Wenn kein Post mit views>0 existiert → null (Nenner 0) → Anzeige "—".
function computeMarketSummary(marketData) {
  const platforms = Array.isArray(marketData?.platforms) ? marketData.platforms : [];
  let posts = 0;
  let views = 0;
  let engagementNumerator = 0;   // Σ(likes+comments) nur über views>0-Posts
  let engagementDenominator = 0; // Σ(views) nur über views>0-Posts
  for (const pg of platforms) {
    const list = Array.isArray(pg?.posts) ? pg.posts : [];
    for (const post of list) {
      posts += 1;
      const v = post.views || 0;
      views += v;
      if (v > 0) {
        engagementNumerator += (post.likes || 0) + (post.comments || 0);
        engagementDenominator += v;
      }
    }
  }
  const engagementRate =
    engagementDenominator > 0 ? engagementNumerator / engagementDenominator : null;
  return { posts, views, engagementRate };
}

// V3 Sprint 5 — Markt-Delta gegen DE (Intra-Fenster). Pure, division-sicher:
// nimmt die Summary EINES Markts und die DE-Referenz-Summary und liefert den
// Abstand zu DE. Kein JSX, keine Formatierung (die liegt im UI).
//
// - viewsFactor: marktViews / deViews. ``null`` wenn DE fehlt oder deViews<=0
//   (Division durch 0 → nicht berechenbar). marktViews=0 ist erlaubt → 0.
// - erDeltaPp: (marktER - deER) in Prozentpunkten. ``null`` wenn die DE-ER
//   oder die eigene ER null ist (kein Post mit views>0).
function computeMarketDelta(marketSummary, deSummary) {
  const deViews = deSummary?.views;
  const viewsFactor =
    deSummary && deViews > 0 ? (marketSummary?.views || 0) / deViews : null;

  const deER = deSummary?.engagementRate;
  const marketER = marketSummary?.engagementRate;
  const erDeltaPp =
    deER != null && marketER != null ? (marketER - deER) * 100 : null;

  return { viewsFactor, erDeltaPp };
}

function FilmPostCard({ post }) {
  const platform = post.platform || 'tiktok';
  const { relative, absolute } = formatRelativeDate(post.published_at);
  // Exakt der funktionierende Pfad der RankedPostCard (Top-Posts): mit
  // asset_id über den ``/api/thumbnails/{asset_id}``-Proxy, der plattform-
  // spezifische Referer setzt und den TikTok/Instagram-Hotlinkschutz
  // schlägt. Der generische ``/api/img``-Proxy (proxyImageUrl) sendet einen
  // leeren Referer und wird 403-geblockt — deshalb NICHT hier. Fehlt das
  // asset_id (sollte mit V3 Sprint 2 nie), fällt es auf die rohe CDN-URL
  // zurück; der onError-Pfad schaltet bei 403/404 aufs Akronym um.
  const thumbSrc = post.asset_id
    ? `/api/thumbnails/${post.asset_id}`
    : post.thumbnail_url;
  const [imageFailed, setImageFailed] = useState(!thumbSrc);
  return (
    <a
      href={post.post_url || '#'}
      target="_blank"
      rel="noopener noreferrer"
      className="film-post-card"
    >
      <div
        className={
          imageFailed
            ? `film-post-thumb platform-${platform} fallback-active`
            : `film-post-thumb platform-${platform}`
        }
      >
        {thumbSrc && !imageFailed && (
          <img src={thumbSrc} alt="" loading="lazy" onError={() => setImageFailed(true)} />
        )}
        {imageFailed && (
          <div className="thumbnail-fallback">
            <span className="thumbnail-fallback-content fallback-acronym">
              {PLATFORM_ACRONYM[platform] || 'TT'}
            </span>
          </div>
        )}
      </div>
      <div className="film-post-body">
        <span className={`platform-pill platform-${platform}`}>
          {PLATFORM_LABEL[platform] || platform}
        </span>
        <div className="film-post-metric">
          <strong>{post.views != null ? formatRankedNumber(post.views) : '—'}</strong>
          <span>Aufrufe</span>
        </div>
        <div className="film-post-stats">
          <span
            className="film-post-stat film-post-stat-rate"
            title="Engagement-Rate = (Reactions + Kommentare) / Aufrufe"
          >
            {post.engagement_rate != null ? formatRankedPercent(post.engagement_rate) : '—'} Engagement
          </span>
          {post.likes != null && (
            <span className="film-post-stat">{formatRankedNumber(post.likes)} Reactions</span>
          )}
          {post.comments != null && (
            <span className="film-post-stat">{formatRankedNumber(post.comments)} Komm.</span>
          )}
        </div>
        {relative && (
          <span className="film-post-date" title={absolute}>{relative}</span>
        )}
      </div>
    </a>
  );
}

function FilmMarketColumn({ market, marketData, sortKey }) {
  const platforms = Array.isArray(marketData?.platforms) ? marketData.platforms : [];
  const groups = platforms.filter((pg) => pg.posts?.length > 0);
  const sortFn = filmDetailSortFn(sortKey);
  return (
    <div className="film-market-column">
      <h4>{market}</h4>
      {groups.length > 0 ? (
        groups.map((pg) => (
          <div key={pg.platform} className="film-platform-group">
            <p className="film-platform-label">{PLATFORM_LABEL[pg.platform] || pg.platform}</p>
            <div className="film-post-list">
              {[...pg.posts].sort(sortFn).map((post, i) => (
                <FilmPostCard key={post.post_url || `${market}-${pg.platform}-${i}`} post={post} />
              ))}
            </div>
          </div>
        ))
      ) : (
        <p className="film-market-empty">Keine Posts</p>
      )}
    </div>
  );
}

// V3 Sprint 5 — Anzeige-Helfer für das Markt-Delta gegen DE. Reine
// Formatierung der computeMarketDelta-Rohwerte; null → "—".
// Deutsche Komma-Konvention (wie formatBreakoutMultiplier).
function formatViewsFactor(factor) {
  if (factor == null) return '—';
  return `${factor.toFixed(1).replace('.', ',')}× DE`;
}
function formatErDeltaPp(pp) {
  if (pp == null) return '—';
  const sign = pp < 0 ? '−' : '+'; // echtes Minuszeichen − bei negativ
  return `${sign}${Math.abs(pp).toFixed(1).replace('.', ',')} pp`;
}

// V3 Sprint 4 — Markt-Vergleichsleiste. Eine Kachel pro Markt (gleiche
// Reihenfolge wie das Grid), je drei Kennzahlen: Posts, Σ Views, aggregierte
// Engagement-Rate. Leere Märkte bleiben sichtbar (Werte "—"). Aggregation via
// computeMarketSummary; ER über formatRankedPercent (konsistent zur Karte),
// Views über formatRankedNumber (wie die Karten-Headline).
//
// V3 Sprint 5 — Markt-Delta gegen DE: US/UK zeigen zusätzlich ihren Abstand
// zur DE-Referenz (Views-Faktor + ER-Prozentpunkte, via computeMarketDelta).
// DE ist die Basis und zeigt kein Delta (nur ein "Referenz"-Label). Kein
// KW-über-KW-Trend (eigener Sprint).
function FilmMarketSummaryBar({ markets, marketByName }) {
  // DE ist die feste Referenz für alle Deltas (Intra-Fenster, gegen DE).
  const deSummary = computeMarketSummary(marketByName.DE);
  return (
    <div className="film-market-summary-bar">
      {markets.map((m, idx) => {
        const summary = computeMarketSummary(marketByName[m]);
        const { posts, views, engagementRate } = summary;
        const isReference = m === 'DE';
        const delta = isReference ? null : computeMarketDelta(summary, deSummary);
        return (
          <div key={m} className="film-market-summary-cell">
            <h5 className="film-market-summary-market">{m}</h5>
            <div className="film-market-summary-metrics">
              <div className="film-market-summary-metric">
                <strong>{formatNumber(posts)}</strong>
                <span>Posts</span>
              </div>
              <div className="film-market-summary-metric">
                <strong>{posts > 0 ? formatRankedNumber(views) : '—'}</strong>
                <span>Aufrufe</span>
              </div>
              <div className="film-market-summary-metric">
                <strong>{engagementRate != null ? formatRankedPercent(engagementRate) : '—'}</strong>
                <span>Engagement{idx === 0 && <GlossaryHint term="er" />}</span>
              </div>
            </div>
            {isReference ? (
              <p className="film-market-summary-delta film-market-summary-delta-base">
                Referenzmarkt
              </p>
            ) : (
              <p className="film-market-summary-delta">
                <span title="Aufrufe relativ zur DE-Referenz">
                  {formatViewsFactor(delta.viewsFactor)}
                </span>
                <span title="Engagement-Rate-Abstand zu DE in Prozentpunkten">
                  {formatErDeltaPp(delta.erDeltaPp)} ER
                </span>
              </p>
            )}
          </div>
        );
      })}
    </div>
  );
}

// Dropdown-Quelle: aktuell_im_fokus-Titel mit title_id (dedupliziert,
// Reihenfolge wie im Brief). Ohne solche Titel rendert die Sektion nichts.
function FilmDetailSection({ fokusItems }) {
  const titleOptions = useMemo(() => {
    const seen = new Map();
    for (const item of fokusItems || []) {
      if (item?.title_id && !seen.has(item.title_id)) {
        seen.set(item.title_id, item.titel || 'Unbenannter Titel');
      }
    }
    return Array.from(seen, ([id, name]) => ({ id, name }));
  }, [fokusItems]);

  const [selectedId, setSelectedId] = useState('');
  const [data, setData] = useState(null);
  const [status, setStatus] = useState('idle'); // 'idle' | 'loading' | 'done' | 'error'
  const [error, setError] = useState(null);
  // Sort-Wahl persistiert (Pattern wie TopRankingSection); Default Engagement-Rate.
  const [sortKey, setSortKey] = useLocalStorage('filmDetailSort', 'engagement');

  useEffect(() => {
    if (!selectedId) {
      setData(null);
      setStatus('idle');
      setError(null);
      return undefined;
    }
    // cancelled-Guard: schneller Wechsel des Dropdowns darf keine veraltete
    // Antwort über eine neuere schreiben.
    let cancelled = false;
    setStatus('loading');
    setError(null);
    endpoints
      .titlePosts(selectedId)
      .then((res) => {
        if (!cancelled) { setData(res); setStatus('done'); }
      })
      .catch((err) => {
        if (!cancelled) { setError(err.message || String(err)); setStatus('error'); }
      });
    return () => { cancelled = true; };
  }, [selectedId]);

  if (titleOptions.length === 0) return null;

  const marketByName = {};
  if (Array.isArray(data?.markets)) {
    for (const m of data.markets) marketByName[m.market] = m;
  }

  return (
    <section className="card film-detail-section">
      <div className="film-detail-header">
        <h3>Film-Detailansicht</h3>
        <select
          className="film-detail-select"
          value={selectedId}
          onChange={(e) => setSelectedId(e.target.value)}
          aria-label="Film auswählen"
        >
          <option value="">Film auswählen …</option>
          {titleOptions.map((t) => (
            <option key={t.id} value={t.id}>{t.name}</option>
          ))}
        </select>
        <select
          className="ranking-sort-select"
          value={sortKey}
          onChange={(e) => setSortKey(e.target.value)}
          aria-label="Sortierung"
        >
          {FILM_SORT_OPTIONS.map((o) => (
            <option key={o.key} value={o.key}>{o.label}</option>
          ))}
        </select>
        <GlossaryHint term="er" />
      </div>

      {status === 'idle' && (
        <p className="film-detail-hint">
          Wähle einen Titel, um alle Posts nach Markt (DE / US / UK) und Plattform zu sehen.
        </p>
      )}
      {status === 'loading' && (
        <p className="film-detail-hint">Lade Posts …</p>
      )}
      {status === 'error' && (
        <p className="film-detail-error">Konnte Posts nicht laden: {error}</p>
      )}
      {status === 'done' && data && (
        data.total_posts === 0 ? (
          <p className="film-detail-empty">
            Für „{data.title_original || 'diesen Titel'}" liegen in diesem Zeitraum noch keine Posts vor.
          </p>
        ) : (
          <>
            <p className="film-detail-meta">
              <strong>{data.title_original || 'Titel'}</strong> · {formatNumber(data.total_posts)} Posts
            </p>
            <FilmMarketSummaryBar markets={TITLE_DETAIL_MARKETS} marketByName={marketByName} />
            <div className="film-market-grid">
              {TITLE_DETAIL_MARKETS.map((m) => (
                <FilmMarketColumn key={m} market={m} marketData={marketByName[m]} sortKey={sortKey} />
              ))}
            </div>
          </>
        )
      )}
    </section>
  );
}

// 60s slow-marker — purely informational ("dauert länger als erwartet"),
// the request itself is NOT aborted because Opus 4.7 can legitimately take
// 30-90s on the largest reports and aborting would surface a misleading
// "failed" state. Bump if Wolf raises max_tokens beyond 8k.
const SLOW_THRESHOLD_MS = 60_000;


// Sprint 28.05.2026 (PDF-Baustein 2) — Print-Mode-State + Trigger.
// Workflow:
// 1. User klickt "Als PDF speichern"-Button → ``setPrintMode(true)``.
// 2. React rendert um (alle CollapsibleCards via PrintModeContext
//    aufgeklappt, alle drei Rollen-Tabs sichtbar, Lazy-Mount-Inhalte
//    sind jetzt im DOM).
// 3. Zwei requestAnimationFrame-Frames warten, damit der Layout-Pass
//    durch ist — sonst ruft window.print() den Browser auf, BEVOR
//    React den neuen DOM gerendert hat.
// 4. ``window.print()`` oeffnet den nativen Druckdialog.
// 5. ``afterprint``-Event-Listener feuert nach Schliessen des Dialogs
//    (Druck-OK ODER Abbruch — beide rufen afterprint) → ``setPrintMode(false)``.
//    Damit faellt jede CollapsibleCard automatisch in ihren vorherigen
//    User-State zurueck (effectiveOpen = isOpen || printMode, also nur
//    noch isOpen). Der Tab-Switcher zeigt wieder nur den aktiven Tab.
//
// Wenn der User ohne den Button via Ctrl+P druckt, greift Print-Mode
// NICHT — das ist akzeptabel: Ctrl+P ist Best-Effort, der Button ist
// der offizielle Pfad.
// V3 Sprint 6 — deskriptive Markt-Zeitreihe. Eigener Read-only-Call
// (sibling zum insightsWeekly-Call), rein darstellend: tatsächliche
// Wochenwerte je Markt (DE/US/UK) über die Brief-Wochen des Pairs.
// Dependency-frei: CSS-Balken, eine Spalte je ISO-Woche, lückenlose Achse
// (fehlende KW = leere Spalte). KEINE Trendlinie, KEINE "wächst/fällt"-
// Aussage, KEINE Prognose (Sprint 7). Σ Views als Balkenhöhe (gemeinsam
// über alle Märkte skaliert, damit DE/US/UK vergleichbar sind), die
// aggregierte ER als Tooltip + kleine Sekundärzahl.
const TIMELINE_MARKETS = ['DE', 'US', 'UK'];

// Dezente Zweitzeile: gerundete Aufrufe pro KW-Balken (deutsche Schreibweise,
// kompakt, damit die schmale Spalte nicht bricht). Views sind hier NICHT die
// Balkenhöhe (das ist die ER) — nur sekundärer Kontext unter dem ER-Label.
function formatViewsCompact(views) {
  const v = Number(views) || 0;
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1).replace('.', ',')} Mio`;
  if (v >= 1_000) return `${Math.round(v / 1_000)} Tsd`;
  return String(v);
}

function MarketTimelineSection({ pair }) {
  const [data, setData] = useState(null);
  const [status, setStatus] = useState('idle'); // 'idle' | 'loading' | 'done' | 'error'

  useEffect(() => {
    let cancelled = false;
    setStatus('loading');
    endpoints
      .insightsTimeline(pair)
      .then((res) => { if (!cancelled) { setData(res); setStatus('done'); } })
      .catch(() => { if (!cancelled) { setStatus('error'); } });
    return () => { cancelled = true; };
  }, [pair]);

  const weeks = Array.isArray(data?.weeks) ? data.weeks : [];
  // Gemeinsamer ER-Maßstab über ALLE Märkte/Wochen (>0, Division-Schutz),
  // damit die Balkenhöhen von DE/US/UK visuell vergleichbar sind. Höhe und
  // Label zeigen dieselbe Metrik (Engagement-Rate) — NICHT Views.
  const maxEr = useMemo(() => {
    let mx = 0;
    const markets = data?.markets || {};
    for (const m of TIMELINE_MARKETS) {
      for (const p of markets[m] || []) mx = Math.max(mx, p.er || 0);
    }
    return mx || 1;
  }, [data]);

  // Rendert nichts, wenn keine Brief-Wochen vorliegen — wie die anderen
  // graceful-degradierenden Sektionen.
  if (status === 'error') return null;
  if (status === 'done' && weeks.length === 0) return null;

  return (
    <CollapsibleCard title="Markt-Zeitreihe" defaultOpen={false}>
      {status === 'done' && (
        <p className="market-timeline-weekcount">
          {weeks.length} {weeks.length === 1 ? 'Woche' : 'Wochen'}
        </p>
      )}
      <p className="market-timeline-caveat">
        Aufrufe = aktueller Stand, nicht historisch fixiert. Die Wochenwerte
        spiegeln den heutigen Datenstand, nicht den Stand zum jeweiligen
        Wochenzeitpunkt.
      </p>

      {status === 'loading' && <p className="market-timeline-hint">Lade Zeitreihe …</p>}

      {status === 'done' && (
        <div className="market-timeline-grid">
          {TIMELINE_MARKETS.map((m) => {
            const points = (data.markets && data.markets[m]) || [];
            return (
              <div key={m} className="market-timeline-row">
                <h4 className="market-timeline-market">{m}</h4>
                <div className="market-timeline-bars">
                  {weeks.map((wk, i) => {
                    const p = points[i] || { views: 0, er: null, posts: 0 };
                    // Balkenhöhe = ER (gemeinsamer Maßstab). Kein Balken, wenn
                    // keine ER (er=null, z.B. kein Post mit views>0).
                    const pct = p.er != null ? Math.round((p.er / maxEr) * 100) : 0;
                    const erText = p.er != null ? formatRankedPercent(p.er) : '—';
                    const title =
                      `KW ${wk.iso_week}/${wk.iso_year} · ${m}\n` +
                      `${erText} Engagement\n` +
                      `${formatNumber(p.views || 0)} Aufrufe\n` +
                      `${formatNumber(p.posts || 0)} Posts`;
                    return (
                      <div key={`${wk.iso_year}-${wk.iso_week}`} className="market-timeline-col" title={title}>
                        <div className="market-timeline-bar-track">
                          <div
                            className={p.er != null ? 'market-timeline-bar' : 'market-timeline-bar market-timeline-bar-empty'}
                            style={{ height: `${p.er != null ? Math.max(pct, 2) : 0}%` }}
                          />
                        </div>
                        <span className="market-timeline-er">{erText}</span>
                        {p.posts > 0 && (
                          <span className="market-timeline-views">{formatViewsCompact(p.views)}</span>
                        )}
                        <span className="market-timeline-week">KW{wk.iso_week}</span>
                      </div>
                    );
                  })}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </CollapsibleCard>
  );
}

// V3 Sprint 7 — ER-Prognose pro Markt (ADMIN-ONLY). Option A: Sektion bei der
// Zeitreihe, aber endpoint-gegated: der Forecast-POST verlangt eine Admin-
// Session (require_admin_session). Ohne Session → 401 → die Sektion rendert
// NICHTS (kein Skelett, kein Leck). Lineare Regression über die ER-Reihe +
// LLM-Einordnung kommen fertig vom Backend. R² ist bewusst SICHTBAR — die
// Beurteilbarkeit ist der Grund, warum die Prognose vorerst nur Admin sieht.
// Spätere User-Freischaltung = dieses Gate entfernen (ein Schalter), kein Umzug.
const FORECAST_MARKETS = ['DE', 'US', 'UK'];

function formatR2(r2) {
  if (r2 == null) return '—';
  return (Number(r2) || 0).toFixed(2).replace('.', ',');
}

function ErForecastSection({ pair }) {
  const [data, setData] = useState(null);
  const [status, setStatus] = useState('loading'); // 'loading' | 'done' | 'hidden'

  useEffect(() => {
    let cancelled = false;
    setStatus('loading');
    endpoints
      .insightsForecast(pair)
      .then((res) => { if (!cancelled) { setData(res); setStatus('done'); } })
      // 401 (kein Admin) ODER jeder andere Fehler → Sektion bleibt unsichtbar.
      .catch(() => { if (!cancelled) { setStatus('hidden'); } });
    return () => { cancelled = true; };
  }, [pair]);

  // Nichts rendern, solange unklar oder kein Admin — keine Anzeige für User.
  if (status !== 'done' || !data) return null;

  const next = data.next_week;
  const markets = data.markets || {};

  return (
    <section className="card er-forecast-section">
      <div className="er-forecast-header">
        <h3>ER-Prognose (Admin)<GlossaryHint term="forecast" /></h3>
        {next && (
          <span className="er-forecast-target">für KW {next.iso_week}/{next.iso_year}</span>
        )}
      </div>
      <p className="er-forecast-note">
        Lineare Regression über die Engagement-Rate der bisherigen Wochen, dazu
        eine Einordnung. Dünne Datenbasis ({data.n_axis_weeks}{' '}
        {data.n_axis_weeks === 1 ? 'Woche' : 'Wochen'}) — das Bestimmtheitsmaß
        R²<GlossaryHint term="r2" /> zeigt, wie verlässlich die Linie ist
        (1,00 = perfekt, niedrig = wenig belastbar). Nur im Admin-Bereich sichtbar.
      </p>

      <div className="er-forecast-grid">
        {FORECAST_MARKETS.map((m) => {
          const f = markets[m] || { status: 'insufficient_data', n_points: 0 };
          return (
            <div key={m} className="er-forecast-cell">
              <h4 className="er-forecast-market">{m}</h4>
              {f.status === 'ok' ? (
                <>
                  <div className="er-forecast-value">
                    <strong>{formatRankedPercent(f.forecast_er)}</strong>
                    <span>Prognose ER</span>
                  </div>
                  <div className="er-forecast-meta">
                    <span className={`er-forecast-dir er-forecast-dir-${f.direction}`}>
                      {f.direction}
                    </span>
                    <span className="er-forecast-r2" title="Bestimmtheitsmaß (Güte der Regression)">
                      R² {formatR2(f.r2)}
                    </span>
                    <span className="er-forecast-n">{f.n_points} Wochen</span>
                  </div>
                </>
              ) : (
                <p className="er-forecast-insufficient">
                  Zu wenig Daten ({f.n_points} valide {f.n_points === 1 ? 'Woche' : 'Wochen'}) —
                  keine Prognose.
                </p>
              )}
            </div>
          );
        })}
      </div>

      {data.einordnung && (
        <p className="er-forecast-einordnung">{data.einordnung}</p>
      )}
    </section>
  );
}

export default function InsightWeekly({ pair }) {
  const [report, setReport] = useState(null);
  const [printMode, setPrintMode] = useState(false);
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

  // Sprint 28.05.2026 (PDF-Baustein 2): afterprint restored den
  // normalen Modus, sobald der Druckdialog schliesst (OK oder Abbruch).
  // ``beforeprint`` koennen wir NICHT als Auto-Trigger nutzen, weil
  // der Event sync ist und keine React-Re-Renders abwartet — das
  // browser-eigene Ctrl+P drueckt also den nicht-printmode-Zustand
  // (aktueller Sicht). Der "Als PDF speichern"-Button ist der
  // empfohlene Pfad und triggert printMode programmatisch vor dem
  // window.print()-Call.
  useEffect(() => {
    const onAfterPrint = () => setPrintMode(false);
    window.addEventListener('afterprint', onAfterPrint);
    return () => window.removeEventListener('afterprint', onAfterPrint);
  }, []);

  async function handleExportPdf() {
    setPrintMode(true);
    // Zwei RAF-Frames damit React den DOM voll rendert + Layout abgeschlossen
    // ist, bevor window.print() blockiert. Ein Frame reicht in Praxis
    // meistens, zwei ist defensiv (Stagger fuer langsamere Geraete).
    await new Promise((r) => requestAnimationFrame(r));
    await new Promise((r) => requestAnimationFrame(r));
    window.print();
    // Browser feuert afterprint nach Schliessen des Dialogs — der
    // useEffect oben raeumt den printMode dann auf. Wenn afterprint aus
    // irgendeinem Grund nicht feuert (sehr alte Browser), bleibt die
    // Seite im PrintMode — User muesste Reload machen. Akzeptabel,
    // weil moderne Browser den Event zuverlaessig feuern.
  }

  const loading = status === 'loading' || status === 'slow';

  const label = FALLBACK_LABEL[pair] || report?.pair_label || pair;

  return (
    <PrintModeContext.Provider value={printMode}>
    <main className={`page insight-page ${printMode ? 'is-print' : ''}`}>
      <header className="hero">
        <div className="hero__left">
          {/* Rueckweg zur Startseite — vorher Sackgasse aus der Pair-Brief-
              Ansicht, kein klickbares Logo. Schlichter Text-Link ueber dem
              Eyebrow, fuegt sich in das bestehende Hero-Layout ein. */}
          <a href="/" className="hero__back-link">← Übersicht</a>
          <p className="eyebrow">STUDIO-REVIEW</p>
          <h1>{label}</h1>
          <p>Social-Media-Wochenanalyse — DE, US, UK im Vergleich.</p>
        </div>
        {report && (
          <div className="hero__meta">
            <p className="hero__meta-week">KW {report.iso_week}</p>
            {report.generated_at && (
              <p className="hero__meta-updated">
                Aktualisiert: {formatDateShort(report.generated_at)}
              </p>
            )}
          </div>
        )}
      </header>

      {report && (
        <WeekBanner
          isoYear={report.iso_year}
          isoWeek={report.iso_week}
          generatedAt={report.generated_at}
          windowDays={report.window_days}
        />
      )}

      {report && status !== 'error' && (
        <div className="viewmode-toolbar">
          {/* PDF-Export-Button (PDF-Baustein 2): setzt printMode → Re-Render
              → window.print(). Im Print-CSS ist .pdf-export-btn ausgeblendet,
              sodass der Button selbst nicht im Ausdruck erscheint. Der frühere
              Ansicht-Schalter (Alle/Cutter/Producer) ist in Sprint 9a-Nach-
              besserung entfernt; die Detail-Karten starten fest zugeklappt. */}
          <button
            type="button"
            className="pdf-export-btn"
            onClick={handleExportPdf}
            aria-label="Brief als PDF speichern"
          >
            Als PDF speichern
          </button>
        </div>
      )}

      {status === 'slow' && (
        <div className="card insight-slow-hint">
          <p>Der Report wird jetzt erstellt – das dauert bis zu 5 Minuten…</p>
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
          <StaleWarning generatedAt={report.generated_at} />

          {/* Sprint 28.05.2026 (IA-Umbau, Baustein 1) — Kern-Reihenfolge:
              Headline+TLDR → Top-Posts → Drei Maerkte → Breakouts. Davor:
              Hero/Banner/StaleWarning (Meta + Caveats). Die
              LLM-Detail-Sektionen rendern weiter unten in LLMOutput
              (kommen in Commit 2 in Klapp-Container). */}
          <LLMHeadlineCard output={report.llm_output} raw={report.raw_llm_text} />

          {/* V3 Sprint 2 — film-zentrierte Detailansicht. Dropdown speist
              sich aus den aktuell_im_fokus-Titeln mit title_id; Auswahl lädt
              die Posts-pro-Titel-Übersicht inline. Rendert nichts, wenn kein
              Fokus-Titel eine title_id trägt. */}
          {report.llm_output?.aktuell_im_fokus?.length > 0 && (
            <FilmDetailSection fokusItems={report.llm_output.aktuell_im_fokus} />
          )}

          <TopRankingSection
            aggregation={report.aggregation}
            pairKey={pair}
          />

          <CrossMarketHeadlineSection
            aggregation={report.aggregation}
            llmOutput={report.llm_output}
          />

          {/* V3 Sprint 6 — deskriptive Markt-Zeitreihe über die Brief-Wochen
              des Pairs. Eigener read-only Call, rendert nichts ohne Wochen. */}
          <MarketTimelineSection pair={pair} />

          {/* V3 Sprint 7 — ER-Prognose (admin-only, endpoint-gegated): rendert
              nichts ohne Admin-Session (Forecast-POST → 401). */}
          <ErForecastSection pair={pair} />

          <BreakoutsSection
            aggregation={report.aggregation}
            pairKey={pair}
          />

          {/* Sprint 9a-Nachbesserung: feste Default-Klappzustände (alle zu).
              Der frühere ViewMode-Schalter ist entfernt. Container-Inhalt
              bleibt lazy-mounted über CollapsibleCard's ``{isOpen && children}``;
              die Print-Integration (effectiveOpen = printMode || isOpen) hält
              sie im PDF-Export vollständig. */}
          {report.llm_output && (
            <>
              <CollapsibleCard
                title="Diese Woche im Detail"
                subtitle="Worum geht's · Pattern · Trends + Actions · Watch-Outs · Tonalität · Konkurrenz"
                defaultOpen={false}
              >
                <LLMDetailSections output={report.llm_output} />
              </CollapsibleCard>

              <CollapsibleCard
                title="Kreativ-Empfehlungen"
                subtitle="Cutter · Motion-Designer · Creative Producer · Vergleichbare Posts"
                defaultOpen={false}
              >
                <LLMRoleSections
                  output={report.llm_output}
                  pairKey={pair}
                />
              </CollapsibleCard>
            </>
          )}

          <CollapsibleCard
            title="Plattform-Details"
            subtitle="TikTok · Instagram · YouTube — Channel-Stats pro Markt"
            defaultOpen={false}
          >
            <MultiPlatformStats aggregation={report.aggregation} />
          </CollapsibleCard>

          {report.llm_output && (
            <CollapsibleCard
              title="Methodik / Daten-Caveats"
              subtitle="Risks · Data Caveats"
              defaultOpen={false}
            >
              <LLMMetaSection output={report.llm_output} />
            </CollapsibleCard>
          )}

          {/* V3 Sprint 8 (B) — Gesamt-Glossar am Ende der Insight-Ansicht,
              ausklappbar, aus derselben Quelle wie die Info-Punkte. */}
          <GlossaryBlock />
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
    </PrintModeContext.Provider>
  );
}
