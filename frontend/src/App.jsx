import React, { useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { endpoints } from './api/client';
import './styles.css';

const STATUS_OPTIONS = ['all', 'new', 'needs_review', 'approved', 'highlight', 'rejected'];
const NAV_ITEMS = ['Report erstellen', 'Treffer prüfen', 'Quellen'];

const ACTION_HELP = [
  ['Für Report freigeben', 'Der Treffer ist relevant und kommt in den Report-Anhang.'],
  ['Als Top-Fund markieren', 'Besonders stark oder strategisch wichtig; erscheint prominent im Weekly Report.'],
  ['Später prüfen', 'Noch unsicher; bleibt in der Prüfliste und wird nicht in den Report übernommen.'],
  ['Aussortieren', 'Für diese Beobachtung nicht brauchbar; wird ausgeblendet und nicht berichtet.'],
  ['Bild/Text analysieren', 'Bild/Text automatisch auswerten: Titel-/Claim-Platzierung, Kinetic, OCR.'],
];

function getAssetDisplayTitle(asset, titles) {
  const hint = inferTitleHint(asset, titles);
  if (asset.title_name || asset.title_id) {
    return `Automatisch erkannt: ${hint.label}`;
  }
  if (hint.label && hint.label !== 'Filmtitel noch offen' && hint.label !== 'Filmtitel offen') {
    return `Vorschlag: ${hint.label}`;
  }
  return 'Filmtitel bitte zuordnen';
}

function Section({ title, kicker, children, className = '' }) {
  return (
    <section className={`card ${className}`}>
      {kicker && <p className="section-kicker">{kicker}</p>}
      <h2>{title}</h2>
      {children}
    </section>
  );
}

function formatNumber(value) {
  if (value === null || value === undefined || value === '') return '—';
  try { return new Intl.NumberFormat('de-DE').format(Number(value)); } catch (_) { return String(value); }
}

function formatDate(value) {
  if (!value) return 'Datum offen';
  try { return new Date(value).toLocaleDateString('de-DE'); } catch (_) { return 'Datum offen'; }
}

function clip(text, max = 260) {
  if (!text) return '';
  return text.length > max ? `${text.slice(0, max).trim()} …` : text;
}

function normalizeHandle(value) {
  const clean = (value || '').trim().replace(/^@/, '').replace(/\/$/, '');
  if (clean.includes('tiktok.com/@')) return clean.split('tiktok.com/@')[1].split('/')[0];
  return clean;
}

function textPool(asset) {
  return [
    asset.title_name,
    asset.title_local,
    asset.franchise,
    asset.placement_title_text,
    asset.caption,
    asset.ai_summary_de,
    asset.ai_summary_en,
    asset.ai_trend_notes,
    asset.ocr_text,
    asset.kinetic_text,
  ].filter(Boolean).join(' ');
}

function titleFromHashtag(tag) {
  const stripped = tag.replace(/^#/, '').replace(/movie|film|official|trailer|themovie$/gi, '');
  const words = stripped
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .replace(/[_-]+/g, ' ')
    .trim();
  return words.length >= 3 ? words : '';
}

function inferTitleHint(asset, titles) {
  if (asset.title_name || asset.placement_title_text) {
    return { label: asset.title_name || asset.placement_title_text, source: 'zugeordnet/erkannt' };
  }
  const pool = textPool(asset);
  const poolLower = pool.toLowerCase();
  const matchedTitle = titles.find((title) => {
    const candidates = [title.title_original, title.title_local, title.franchise, ...(title.keywords || [])].filter(Boolean);
    return candidates.some((candidate) => poolLower.includes(String(candidate).toLowerCase()));
  });
  if (matchedTitle) return { label: matchedTitle.title_original, source: 'Vorschlag aus Text/Caption' };

  const hashtags = [...pool.matchAll(/#([A-Za-z][A-Za-z0-9_]{3,})/g)].map((m) => titleFromHashtag(m[0])).filter(Boolean);
  const useful = hashtags.find((tag) => !/fyp|viral|cinema|only in theaters|trailer/i.test(tag));
  if (useful) return { label: useful, source: 'Vorschlag aus Hashtag' };

  const quoted = pool.match(/[„“"]([^„“"]{3,44})[„“"]/);
  if (quoted?.[1]) return { label: quoted[1], source: 'Vorschlag aus Text' };
  return { label: 'Filmtitel noch offen', source: 'Bitte zuordnen' };
}

function ImagePreview({ sources = [] }) {
  const validSources = sources.filter(Boolean);
  const [index, setIndex] = useState(0);

  useEffect(() => { setIndex(0); }, [validSources.join('|')]);

  if (!validSources[index]) return <div className="preview-placeholder"><strong>Kein Bild gesichert</strong><small>Bildquelle konnte nicht gespeichert werden.</small></div>;
  return <img src={validSources[index]} alt="Creative Vorschau" onError={() => setIndex((current) => current + 1)} />;
}

function MetricStrip({ asset }) {
  const metrics = [
    ['Views', asset.visible_views],
    ['Likes', asset.visible_likes],
    ['Shares', asset.visible_shares],
    ['Comments', asset.visible_comments],
  ];
  return (
    <div className="metric-strip" aria-label="Öffentlich sichtbare Kennzahlen">
      {metrics.map(([label, value]) => (
        <span key={label}><b>{formatNumber(value)}</b><small>{label}</small></span>
      ))}
    </div>
  );
}

function TodoCard({ assets, openReview, missingTitles, reportCandidates, approved, highlights, onGoReview, onGoReport, onBatchAnalyze }) {
  const reportGap = [];
  if (approved === 0) reportGap.push('1. Mindestens ein Top-Fund');
  if (missingTitles > 4) reportGap.push('2. Zu viele Treffer ohne Filmtitel-Zuordnung');
  if (openReview > 0) reportGap.push(`3. ${openReview} Treffer sind noch nicht entschieden`);

  return (
    <Section title="Heute zu tun" kicker="Geführter Workflow" className="todo-card">
      <p className="muted small"><strong>Empfohlener Testlauf (MVP):</strong> 1) Titelquellen aktualisieren 2) Bestehende Treffer neu zuordnen 3) 5 neue TikTok-Treffer prüfen 4) KI-Bildanalyse starten 5) 1 Top-Fund markieren 6) Weekly Report aktualisieren.</p>
      <div className="workflow-steps">
        <div><b>1</b><span>Neue Treffer ansehen</span></div>
        <div><b>2</b><span>KI-Bildanalyse starten</span></div>
        <div><b>3</b><span>Relevanz / Top-Fund entscheiden</span></div>
        <div><b>4</b><span>Weekly Report erzeugen</span></div>
      </div>
      <div className="todo-grid">
        <div className="todo-item"><strong>{openReview}</strong><span>neue Treffer warten auf Prüfung</span></div>
        <div className="todo-item"><strong>{missingTitles}</strong><span>Treffer brauchen Filmtitel-Zuordnung</span></div>
        <div className="todo-item"><strong>{reportCandidates}</strong><span>Treffer sind für den Report geeignet</span></div>
      </div>
      <div className="todo-actions">
        <button className="primary" onClick={onBatchAnalyze}>Alle neuen Assets visuell prüfen</button>
        <button className="secondary" onClick={onGoReview}>Jetzt Treffer prüfen</button>
      </div>
      <p className="muted small">{reportGap.length ? `Report noch nicht bereit. Es fehlen noch: ${reportGap.join(' ')}` : 'Report kann jetzt sinnvoll erstellt oder aktualisiert werden.'}</p>
    </Section>
  );
}

function ImportantFinds({ assets, titles }) {
  const weekly = [...assets]
    .filter((asset) => asset.review_status === 'highlight' || asset.is_highlight || asset.review_status === 'approved')
    .sort((a, b) => new Date(b.published_at || b.created_at || 0) - new Date(a.published_at || a.created_at || 0))
    .slice(0, 6);

  return (
    <Section title="Empfohlene Funde für den nächsten Report" kicker="Automatisch vorgeschlagen">
      {weekly.length === 0 ? <p className="muted">Noch keine kuratierten Funde vorhanden.</p> : (
        <div className="find-grid">
          {weekly.map((asset) => {
            const hint = inferTitleHint(asset, titles);
            return (
              <article key={asset.id} className="find-card">
                <ImagePreview sources={[asset.visual_evidence_url, asset.screenshot_url, asset.thumbnail_url, asset.visual_source_url]} />
                <div>
                  <p className="find-title">{hint.label}</p>
                  <p className="muted small">{asset.channel_name || 'Unbekannter Kanal'} · {asset.channel_market || 'UNKNOWN'} · {formatDate(asset.published_at || asset.created_at)}</p>
                  <p className="small">{clip(asset.ai_summary_de || asset.caption || 'Noch keine Zusammenfassung verfügbar.', 120)}</p>
                </div>
              </article>
            );
          })}
        </div>
      )}
    </Section>
  );
}

function ReportStatus({ approved, highlights, openReview, missingTitles }) {
  const minApproved = 3;
  const maxMissingTitles = 4;
  const ready = approved >= minApproved && highlights >= 1 && missingTitles <= maxMissingTitles;
  const missingApproved = Math.max(0, minApproved - approved);
  const missingHighlights = Math.max(0, 1 - highlights);
  const missingTitleAssignments = Math.max(0, missingTitles - maxMissingTitles);
  const gaps = [];
  if (missingApproved > 0) gaps.push(`${missingApproved} weitere freigegebene Treffer`);
  if (missingHighlights > 0) gaps.push(`${missingHighlights} Highlight`);
  if (missingTitleAssignments > 0) gaps.push(`Filmtitel-Zuordnung bei ${missingTitleAssignments} Treffern`);
  return (
    <Section title="Report-Status" kicker="Was noch fehlt">
      <div className="status-pills">
        <span className={`pill ${ready ? 'good' : 'warn'}`}>{ready ? 'Report bereit' : 'Report noch nicht bereit'}</span>
        <span className="pill">{approved} freigegeben</span>
        <span className="pill">{highlights} Highlights</span>
        <span className="pill">{openReview} noch zu prüfen</span>
        <span className="pill">{missingTitles} mit offener Filmtitel-Zuordnung</span>
      </div>
      <p className="muted small">
        {ready
          ? 'Alle Mindestkriterien erfüllt: Der Weekly Report ist belastbar.'
          : `Report noch nicht bereit. Es fehlen noch: ${gaps.join(' · ')}`}
      </p>
    </Section>
  );
}

function ReviewGuide() {
  return (
    <div className="review-guide">
      <div>
        <h3>So entsteht der Weekly Report</h3>
        <ol>
          <li><strong>Filmtitel prüfen:</strong> Ist der Treffer einem Film oder einer Franchise zuordenbar?</li>
          <li><strong>Relevanz entscheiden:</strong> Ist es ein brauchbares Creative-Muster?</li>
          <li><strong>Highlight setzen:</strong> Nur besonders auffällige oder strategisch wichtige Treffer markieren.</li>
          <li><strong>Visual prüfen:</strong> Bei Titel/Claim/Kinetic-Verdacht die Bild-/Text-Prüfung starten.</li>
        </ol>
      </div>
      <div className="action-help">
        <h3>Was bedeuten die Aktionen?</h3>
        {ACTION_HELP.map(([label, text]) => <p key={label}><strong>{label}:</strong> {text}</p>)}
      </div>
    </div>
  );
}

function AssetCard({ asset, titles, busy, onReview, onAnalyzeVisual, onAssignTitle, onReportMissingTitle, recentlyCreatedTitleName }) {
  const platform = asset.platform || asset.channel_platform || asset.media_type || 'instagram';
  const hasTitle = Boolean(asset.title_name || asset.placement_title_text || asset.title_id);
  const visualChecked = asset.visual_analysis_status === "done";
  const workflowSteps = [
    { done: hasTitle, doneLabel: 'Filmtitel bestätigt', openLabel: '1 Filmtitel bestätigen' },
    { done: visualChecked, doneLabel: 'Visual geprüft', openLabel: '2 Visual prüfen' },
    { done: false, doneLabel: 'Entscheidung getroffen', openLabel: '3 Entscheidung treffen' },
  ];
  const hint = inferTitleHint(asset, titles);
  const displayStatus = {
    new: 'Noch zu prüfen',
    needs_review: 'Noch zu prüfen',
    approved: 'Freigegeben',
    highlight: 'Als Highlight markiert',
    rejected: 'Nicht relevant',
  }[asset.review_status] || asset.review_status;
  const canUseSuggestion = hint.label && !['Filmtitel noch offen', 'Filmtitel offen'].includes(hint.label);
  const normalizedHint = (hint.label || '').trim().toLowerCase();
  const recommendedTitles = titles.filter((title) => {
    const pool = [title.title_original, title.title_local, title.franchise, ...(title.aliases || []), ...(title.keywords || [])]
      .filter(Boolean)
      .join(' ')
      .toLowerCase();
    const target = [hint.label, asset.placement_title_text, asset.caption, asset.ocr_text].filter(Boolean).join(' ').toLowerCase();
    return target && pool && (target.includes((title.title_original || '').toLowerCase()) || pool.includes(hint.label.toLowerCase()));
  }).slice(0, 5);
  const whitelistMatchesHint = canUseSuggestion && titles.some((title) => (
    [title.title_original, title.title_local, ...(title.aliases || [])]
      .filter(Boolean)
      .map((value) => String(value).trim().toLowerCase())
      .includes(normalizedHint)
  ));
  const showAssignmentSelect = hasTitle || whitelistMatchesHint || recommendedTitles.length > 0;
  const titleHintLabel = recentlyCreatedTitleName ? 'Bestätigter Filmtitel' : (hasTitle ? 'Automatisch erkannt' : 'Vermuteter Filmtitel');
  const titleHintSource = recentlyCreatedTitleName ? 'neu angelegt und zugeordnet' : hint.source;
  const titleHintValue = recentlyCreatedTitleName || hint.label;
  const visualStatusLabel = {
    done: 'Analyse abgeschlossen',
    running: 'KI analysiert Bild/Text',
    pending: 'Bildanalyse offen',
    text_fallback: '⚠ Nur Textanalyse',
    no_source: 'Kein Bild verfügbar',
    fetch_failed: '⚠ Bildquelle nicht erreichbar',
    error: '⚠ Bildanalyse fehlgeschlagen',
  }[asset.visual_analysis_status] || 'Bildanalyse offen';

  return (
    <article className="asset-card">
      <div className="asset-preview"><ImagePreview sources={[asset.visual_evidence_url, asset.screenshot_url, asset.thumbnail_url, asset.visual_source_url]} /></div>
      <div className="asset-content">
        <div className="asset-topline">
          <span className="asset-title">{getAssetDisplayTitle(asset, titles)}</span>
          <span className="pill">{platform}</span>
          <span className="pill">{displayStatus}</span>
          {asset.has_title_placement && <span className="pill">Titel-/Claim-Platzierung</span>}
          {asset.has_kinetic && <span className="pill">Bewegter Text</span>}
        </div>
        <p className="asset-meta">{asset.channel_name || 'Unbekannter Kanal'} · {asset.channel_market || 'UNKNOWN'} · {formatDate(asset.published_at || asset.detected_at || asset.created_at)}</p>
        <MetricStrip asset={asset} />
        <div className={`title-hint ${hasTitle ? 'assigned' : 'open'}`}>
          <span>{titleHintLabel}</span>
          <strong>{titleHintValue}</strong>
          <small>{titleHintSource}</small>
        </div>
        {!hasTitle && showAssignmentSelect && <p className="title-instruction">Mögliche Titelzuordnung gefunden. Bitte bestätigen oder neu zuordnen.</p>}
        {!hasTitle && !showAssignmentSelect && <p className="title-instruction">Film ist nicht in der Titelliste? Bitte zur Prüfung vormerken.</p>}
        {showAssignmentSelect && (
          <label className="title-select">
            Filmtitel-Zuordnung
            <select value={asset.title_id || ''} onChange={(e) => onAssignTitle(asset, e.target.value)} disabled={busy}>
              <option value="">Bitte Filmtitel auswählen</option>
              {recommendedTitles.length > 0 && <optgroup label="Empfohlene Matches">{recommendedTitles.map((title) => <option key={`rec-${title.id}`} value={title.id}>{title.title_original}</option>)}</optgroup>}
              <optgroup label="Titelliste (alle aktiv)">{titles.map((title) => <option key={title.id} value={title.id}>{title.title_original} {title.source ? `(${title.source})` : ''}</option>)}</optgroup>
            </select>
          </label>
        )}
        <button className="secondary ghost" type="button" onClick={() => onReportMissingTitle(asset, hint)} disabled={busy}>
          Titel zur Prüfung vormerken
        </button>
        <div className="card-steps" aria-label="Review-Reihenfolge">
          {workflowSteps.map((step) => (
            <span key={step.openLabel} className={`card-step ${step.done ? 'done' : ''}`}>
              {step.done ? `✓ ${step.doneLabel}` : step.openLabel}
            </span>
          ))}
        </div>
        {!hasTitle && showAssignmentSelect && <p className="missing-hint">Nächster Schritt: Filmtitel auswählen (oder Rematch ausführen). Erst dann werden DE/US-Vergleich und Report-Auswertung belastbar.</p>}
        <div className="analysis-box">
          <p className="analysis-head">KI-Bild/Text-Analyse</p>
          <p className="analysis-status">{visualStatusLabel}</p>
          <p className="analysis-main">{clip(asset.visual_notes || asset.ai_summary_de || 'Noch keine Analyse vorhanden. Bitte KI-Bildanalyse starten.', 220)}</p>
          <p className="analysis-meta">Titel-/Claim-Platzierung: {asset.placement_position || 'nicht prüfbar'} · CTA: {asset.asset_type === 'CTA Post' || asset.asset_type === 'Ticket CTA' ? 'erkennbar' : 'nicht erkannt'} · Eignung: {asset.review_status === 'approved' || asset.review_status === 'highlight' ? 'reportfähig' : 'noch offen'} · Sicherheit: {asset.visual_confidence_score ? `${Math.round(asset.visual_confidence_score*100)}%` : 'nicht prüfbar'}</p>
          <p className="analysis-meta">
            OCR: {asset.ocr_text ? clip(asset.ocr_text, 60) : '—'} · Titel/Claim: {asset.has_title_placement ? (asset.placement_title_text || 'erkannt') : 'nein'} · Kinetic: {asset.has_kinetic ? (asset.kinetic_type || 'ja') : 'nein'}
          </p>
        </div>
        <div className="asset-links">
          {asset.post_url && <a href={asset.post_url} target="_blank" rel="noreferrer">Original öffnen</a>}
          <details>
            <summary>Mehr Details</summary>
            {asset.caption && <p><strong>Caption:</strong> {asset.caption}</p>}
            {asset.ai_trend_notes && <p><strong>Muster:</strong> {asset.ai_trend_notes}</p>}
            {(asset.ocr_text || asset.visual_notes || asset.kinetic_text) && <p><strong>Bild-/Text-Prüfung:</strong> {[asset.ocr_text, asset.visual_notes, asset.kinetic_text].filter(Boolean).join(' · ')}</p>}
          </details>
        </div>
      </div>
      <div className="asset-actions">
        <button onClick={() => onReview(asset, 'approved')} disabled={busy} title="Relevant; in den Report-Anhang übernehmen.">Für Report freigeben</button>
        <button onClick={() => onReview(asset, 'highlight')} disabled={busy} title="Besonders wichtiger Treffer; prominent im Weekly Report zeigen.">Als Top-Fund markieren</button>
        <button onClick={() => onReview(asset, 'needs_review')} disabled={busy} title="Noch unsicher; bleibt in der Prüfliste.">Später prüfen</button>
        <button onClick={() => onReview(asset, 'rejected')} disabled={busy} title="Nicht relevant; nicht in den Report übernehmen.">Aussortieren</button>
        <button className="secondary" onClick={() => onAnalyzeVisual(asset)} disabled={busy} title="Bild/Text auswerten: Titel, Claim, Kinetic, OCR.">{asset.visual_analysis_status === "done" ? "Bildanalyse aktualisieren" : asset.visual_analysis_status === "text_fallback" ? "Bildquelle prüfen" : asset.visual_analysis_status === "no_source" ? "Kein Bild verfügbar – Textanalyse möglich" : "KI-Bildanalyse starten"}</button>
      </div>
    </article>
  );
}

function HomePanel({ assets, titles, openReview, missingTitles, reportCandidates, approved, highlights, setActiveTab, onBatchAnalyze }) {
  return (
    <>
      <TodoCard
        assets={assets}
        openReview={openReview}
        missingTitles={missingTitles}
        reportCandidates={reportCandidates}
        approved={approved.length}
        highlights={highlights.length}
        onGoReview={() => setActiveTab('Treffer prüfen')}
        onGoReport={() => setActiveTab('Report erstellen')}
        onBatchAnalyze={() => onBatchAnalyze()}
      />
      <ImportantFinds assets={assets} titles={titles} />
      <ReportStatus approved={approved.length} highlights={highlights.length} openReview={openReview} missingTitles={missingTitles} />
    </>
  );
}

function ReviewPanel({
  assets,
  titles,
  visibleAssets,
  filters,
  setFilters,
  busy,
  onReview,
  onAnalyzeVisual,
  onAssignTitle,
  onReportMissingTitle,
  recentlyCreatedByAssetId,
}) {
  return (
    <Section title="Treffer prüfen" kicker={`${visibleAssets.length} von ${assets.length} Treffern sichtbar`}><p className="muted small">Hier korrigieren Sie einzelne Treffer. Reports können auch automatisch aus Vorschlägen erstellt werden.</p>
      <ReviewGuide />
      <div className="filterbar">
        <select value={filters.status} onChange={(e) => setFilters({ ...filters, status: e.target.value })}>
          {STATUS_OPTIONS.map((status) => <option key={status} value={status}>{status === 'all' ? 'Alle Status' : status}</option>)}
        </select>
        <select value={filters.platform} onChange={(e) => setFilters({ ...filters, platform: e.target.value })}>
          <option value="all">Alle Kanäle</option>
          <option value="instagram">Instagram</option>
          <option value="tiktok">TikTok</option>
        </select>
        <select value={filters.market} onChange={(e) => setFilters({ ...filters, market: e.target.value })}>
          <option value="all">Alle Märkte</option>
          <option value="DE">DE</option>
          <option value="US">US</option>
          <option value="INT">INT</option>
          <option value="UNKNOWN">UNKNOWN</option>
        </select>
        <input value={filters.query} onChange={(e) => setFilters({ ...filters, query: e.target.value })} placeholder="Suche nach Film, Kanal, Claim …" />
      </div>
      {assets.length === 0 && <p>Noch keine Treffer. Bitte zuerst Kanäle prüfen.</p>}
      {visibleAssets.map((asset) => (
        <AssetCard
          key={asset.id}
          asset={asset}
          titles={titles}
          busy={busy}
          onReview={onReview}
          onAnalyzeVisual={onAnalyzeVisual}
          onAssignTitle={onAssignTitle}
          onReportMissingTitle={onReportMissingTitle}
          recentlyCreatedTitleName={recentlyCreatedByAssetId[asset.id] || ''}
        />
      ))}
    </Section>
  );
}

function ComparisonPanel({ assets }) {
  const unassignedCount = assets.filter((asset) => !(asset.title_name || asset.placement_title_text || asset.title_id)).length;
  const grouped = useMemo(() => {
    const map = new Map();
    assets.forEach((asset) => {
      const key = asset.title_name || asset.placement_title_text || 'Ohne Filmtitel-Zuordnung';
      if (!map.has(key)) map.set(key, { de: [], us: [] });
      const lane = (asset.channel_market === 'DE') ? 'de' : 'us';
      map.get(key)[lane].push(asset);
    });
    return [...map.entries()].map(([title, values]) => ({ title, ...values }));
  }, [assets]);

  return (
    <Section title="DE/US Vergleich" kicker="Muster auf einen Blick">
      {unassignedCount > 0 && (
        <p className="compare-warning">
          Achtung: {unassignedCount} Treffer ohne Filmtitel-Zuordnung werden als „Ohne Filmtitel-Zuordnung“ geführt und können den Vergleich verzerren.
        </p>
      )}
      {grouped.length === 0 ? <p className="muted">Noch keine Vergleichsdaten vorhanden.</p> : (
        <div className="compare-list">
          {grouped.map((group) => (
            <article key={group.title} className="compare-card">
              <h3>{group.title}</h3>
              <div className="compare-columns">
                <div>
                  <p className="compare-label">DE</p>
                  <p>{group.de.length} Treffer</p>
                  <p className="small muted">Titel-/Claim-Platzierung: {group.de.filter((a) => a.has_title_placement).length} · Bewegter Text: {group.de.filter((a) => a.has_kinetic).length}</p>
                </div>
                <div>
                  <p className="compare-label">US/INT</p>
                  <p>{group.us.length} Treffer</p>
                  <p className="small muted">Titel-/Claim-Platzierung: {group.us.filter((a) => a.has_title_placement).length} · Bewegter Text: {group.us.filter((a) => a.has_kinetic).length}</p>
                </div>
              </div>
              <p className="small">Zusammenfassung: {group.de.length > group.us.length ? 'DE spielt das Motiv aktuell stärker aus.' : group.de.length < group.us.length ? 'US/INT setzt das Motiv aktuell stärker ein.' : 'DE und US/INT sind aktuell ähnlich stark vertreten.'}</p>
            </article>
          ))}
        </div>
      )}
    </Section>
  );
}

function ReportsPanel({ report, busy, suggestion, form, setForm, onSuggest, onGenerateSuggestedReport, onRelaxFiltersAndSuggest }) {
  const reportTypes = [
    { key: 'weekly_overview', label: 'Wochenüberblick', desc: 'Die wichtigsten Creative-Funde der letzten Tage. Für Geschäftsführung, Marketing und schnelle Wochenupdates.' },
    { key: 'de_us_comparison', label: 'DE/US Vergleich', desc: 'Vergleicht deutsche und internationale Creatives: Titel, Claims, CTAs, Kinetics und visuelle Platzierung.' },
    { key: 'visual_kinetics', label: 'Bild/Text & Kinetics', desc: 'Dokumentiert sichtbaren Text, Claims, Overlays, CTAs, Kinetics und Screenshots.' },
  ];
  const selectedType = reportTypes.find((t) => t.key === form.report_type);
  const hasSuggestedAssets = Boolean(suggestion?.assets?.length);
  const step = !suggestion ? 2 : !hasSuggestedAssets ? 3 : !report ? 4 : 5;
  return (
    <Section title="Welchen Report möchtest du erstellen?" kicker="Report-Zentrale">
      <div className="step-bar">{['1 Report-Typ wählen', '2 Vorschlag erstellen', '3 Vorschlag prüfen', '4 Report erzeugen', '5 Download'].map((label, index) => (
        <span key={label} className={`step-pill ${step === index + 1 ? 'active' : ''}`}>{label}</span>
      ))}</div>
      <div className="find-grid report-types">{reportTypes.map((t) => {
        const active = form.report_type === t.key;
        return (
          <button key={t.key} type="button" className={`find-card report-type-card ${active ? 'active-choice' : ''}`} onClick={() => setForm({ ...form, report_type: t.key })} role="radio" aria-checked={active}>
            <div>
              {active && <p className="selected-marker">✓ Ausgewählt</p>}
              <p className="find-title">{t.label}</p>
              <p className="small muted">{t.desc}</p>
            </div>
          </button>
        );
      })}</div>
      <p className="selected-report">Gewählter Report: <strong>{selectedType?.label}</strong></p>
      <div className="form-grid">
        <label>Zeitraum<select value={form.date_range} onChange={(e) => setForm({ ...form, date_range: e.target.value })}><option value="7d">letzte 7 Tage</option><option value="14d">14 Tage</option><option value="30d">30 Tage</option></select></label>
        <label>Kanäle<select value={form.channel || "all"} onChange={(e) => setForm({ ...form, channel: e.target.value })}><option value="all">alle</option><option value="tiktok">TikTok</option><option value="instagram">Instagram</option></select></label>
        <label>Märkte<select value={form.market} onChange={(e) => setForm({ ...form, market: e.target.value })}><option value="all">alle</option><option value="DE">DE</option><option value="US">US</option><option value="INT">INT</option></select></label>
        <label>Max. Assets<select value={form.limit} onChange={(e) => setForm({ ...form, limit: Number(e.target.value) })}><option value={5}>5</option><option value={10}>10</option><option value={20}>20</option></select></label>
      </div>
      <div className="section-actions"><button className="primary" onClick={onSuggest} disabled={busy}>Report-Vorschlag erstellen</button></div>
      {suggestion && (<>
        <h3>Report-Vorschlag</h3>
        {!hasSuggestedAssets ? (
          <div className="report-empty">
            <p><strong>Keine passenden Treffer gefunden.</strong></p>
            <p className="small muted">Kein technischer Fehler. Es gibt nur keine Treffer, die zu den aktuellen Filtern passen.</p>
            <p className="small muted">Die aktuellen Filter sind sehr eng:</p>
            <ul className="small muted"><li>Zeitraum: {form.date_range === '7d' ? 'letzte 7 Tage' : form.date_range === '14d' ? '14 Tage' : '30 Tage'}</li><li>Kanal: {form.channel}</li><li>Markt: {form.market}</li></ul>
            <div className="section-actions"><button type="button" className="primary" onClick={onRelaxFiltersAndSuggest} disabled={busy}>Mit lockeren Filtern erneut suchen</button><button type="button" className="secondary" onClick={() => setForm({ ...form, date_range: '30d', channel: 'all', market: 'all', limit: 10 })} disabled={busy}>Filter zurücksetzen</button></div>
          </div>
        ) : (
          <>
            <p className="muted small">Creative Radar empfiehlt {suggestion.selected} Assets für diesen Report.</p>
            <p className="muted small">Geprüft: {suggestion.checked} · Geeignet: {suggestion.eligible} · Vorgeschlagen: {suggestion.selected} · Ausgeschlossen wegen fehlendem Bild: {suggestion.excluded_no_image || 0} · Ausgeschlossen wegen fehlendem Titel: {suggestion.excluded_no_title || 0}</p>
            <div className="find-grid">{suggestion.assets.map((asset) => (<article key={asset.asset_id} className="find-card"><ImagePreview sources={[asset.visual_evidence_url]} /><div><p className="find-title">{asset.title || 'Ohne Titel'}</p><p className="muted small">{asset.channel} · {asset.market} · Eignung: {asset.score >= 0.75 ? 'hoch' : asset.score >= 0.5 ? 'mittel' : 'niedrig'}</p><p className="small">{asset.reason}</p><p className="small muted">Qualitätshinweise: {(asset.quality_notes || []).join(' · ') || 'Keine Hinweise'}</p></div></article>))}</div>
          </>
        )}
        <div className="section-actions"><button className="primary" onClick={onGenerateSuggestedReport} disabled={busy || suggestion.assets.length===0}>Report erzeugen</button></div>
        {!hasSuggestedAssets && <p className="small muted">Report kann erst erzeugt werden, wenn ein Vorschlag Assets enthält.</p>}
      </>)}
      {report ? <details><summary>Aktuellen Report anzeigen</summary><p className="muted small">Report wurde erstellt.</p><div className="section-actions"><a href="/api/reports/latest/download.html">HTML herunterladen</a><a href="/api/reports/latest/download.md">Markdown herunterladen</a></div><iframe title="report" srcDoc={report.html_content || ''} /></details> : <p className="muted">Noch kein Report erzeugt.</p>}
    </Section>
  );
}

function SourcesPanel({
  busy,
  channelFile,
  setChannelFile,
  onImportChannelFile,
  monitorForm,
  setMonitorForm,
  onInstagram,
  tiktokForm,
  setTiktokForm,
  onTikTok,
  whitelistStats,
  titleCandidates,
  onSyncTitleSources,
  onRematchAssets,
}) {
  return (
    <>
      <Section title="Quellen prüfen" kicker="Kanäle und Monitoring">
        <p className="muted small source-intro">Aktuell läuft der Test bewusst klein mit Warner Bros. Weitere Kanäle erst ergänzen, wenn Review, Filmtitel-Zuordnung und Weekly Report sauber funktionieren.</p>
        <div className="source-grid">
          <div className="source-card">
            <h3>TikTok</h3>
            <label>Username oder Profil-URL
              <input value={tiktokForm.username} onChange={(e) => setTiktokForm({ ...tiktokForm, username: e.target.value })} placeholder="warnerbros" />
            </label>
            <label>Videos pro Profil
              <input type="number" min="1" max="20" value={tiktokForm.results_limit_per_channel} onChange={(e) => setTiktokForm({ ...tiktokForm, results_limit_per_channel: e.target.value })} />
            </label>
            <button onClick={onTikTok} disabled={busy || !tiktokForm.username}>TikTok prüfen</button>
          </div>
          <div className="source-card muted-source">
            <h3>Instagram</h3>
            <label>Max. Kanäle
              <input type="number" min="1" max="25" value={monitorForm.max_channels} onChange={(e) => setMonitorForm({ ...monitorForm, max_channels: e.target.value })} />
            </label>
            <label>Posts/Reels pro Kanal
              <input type="number" min="1" max="20" value={monitorForm.results_limit_per_channel} onChange={(e) => setMonitorForm({ ...monitorForm, results_limit_per_channel: e.target.value })} />
            </label>
            <button onClick={onInstagram} disabled={busy}>Instagram prüfen</button>
          </div>
        </div>
      </Section>

      <Section title="Titel-Whitelist" kicker="Automatische Titelquellen">
        <p className="muted small">Automatisch gepflegte Titelliste aus TMDb und geprüften Kandidaten.</p>
        <div className="status-pills">
          <span className="pill">Aktive Titel: {whitelistStats?.active_titles ?? '—'}</span>
          <span className="pill">Letzter Sync: {whitelistStats?.last_sync ? formatDate(whitelistStats.last_sync) : '—'}</span>
          <span className="pill">Neue Titel diese Woche: {whitelistStats?.new_titles_this_week ?? '—'}</span>
          <span className="pill">Offene Titelkandidaten: {whitelistStats?.open_title_candidates ?? titleCandidates.length}</span>
        </div>
        <div className="section-actions">
          <button className="primary" onClick={onSyncTitleSources} disabled={busy}>Titelquellen aktualisieren</button>
          <button className="secondary" onClick={onRematchAssets} disabled={busy}>Bestehende Treffer neu zuordnen</button>
        </div>
      </Section>
      <details className="card">
        <summary>Kanalliste importieren</summary>
        <form className="form-grid" onSubmit={onImportChannelFile}>
          <label className="wide">Excel-Datei
            <input type="file" accept=".xlsx,.xlsm" onChange={(event) => setChannelFile(event.target.files?.[0] || null)} />
          </label>
          <div className="wide"><button type="submit" disabled={busy || !channelFile}>Import starten</button></div>
        </form>
      </details>
    </>
  );
}

function App() {
  const [health, setHealth] = useState(null);
  const [channels, setChannels] = useState([]);
  const [titles, setTitles] = useState([]);
  const [assets, setAssets] = useState([]);
  const [report, setReport] = useState(null);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [activeTab, setActiveTab] = useState('Report erstellen');
  const [channelFile, setChannelFile] = useState(null);
  const [monitorForm, setMonitorForm] = useState({ max_channels: 5, results_limit_per_channel: 5, only_whitelist_matches: true });
  const [tiktokForm, setTiktokForm] = useState({ username: 'warnerbros', max_channels: 1, results_limit_per_channel: 5, only_whitelist_matches: false });
  const [filters, setFilters] = useState({ status: 'all', platform: 'all', market: 'all', query: '' });
  const [recentlyCreatedByAssetId, setRecentlyCreatedByAssetId] = useState({});
  const [titleCandidates, setTitleCandidates] = useState([]);
  const [whitelistStats, setWhitelistStats] = useState(null);
  const [batchFeedback, setBatchFeedback] = useState(null);
  const [reportSuggestion, setReportSuggestion] = useState(null);
  const [reportForm, setReportForm] = useState({ report_type: 'weekly_overview', date_range: '30d', market: 'all', channel: 'all', limit: 10 });

  const sortedTitles = useMemo(() => {
    const map = new Map();
    [...titles].forEach((title) => {
      const key = (title.title_original || "").trim().toLowerCase();
      if (!map.has(key) || (!map.get(key).tmdb_id && title.tmdb_id)) map.set(key, title);
    });
    return [...map.values()].sort((a, b) => a.title_original.localeCompare(b.title_original));
  }, [titles]);
  const approved = assets.filter((asset) => asset.include_in_report || asset.review_status === 'approved' || asset.review_status === 'highlight');
  const highlights = assets.filter((asset) => asset.is_highlight || asset.review_status === 'highlight');
  const openReview = assets.filter((asset) => asset.review_status === 'new' || asset.review_status === 'needs_review').length;
  const missingTitles = assets.filter((asset) => !(asset.title_name || asset.placement_title_text || asset.title_id)).length;
  const reportCandidates = assets.filter((asset) => asset.review_status === 'approved' || asset.review_status === 'highlight' || asset.include_in_report).length;

  const visibleAssets = useMemo(() => {
    const query = filters.query.trim().toLowerCase();
    return assets.filter((asset) => {
      const platform = asset.platform || asset.channel_platform || asset.media_type;
      if (filters.status !== 'all' && asset.review_status !== filters.status) return false;
      if (filters.platform !== 'all' && platform !== filters.platform) return false;
      if (filters.market !== 'all' && asset.channel_market !== filters.market) return false;
      if (!query) return true;
      return [asset.title_name, asset.placement_title_text, asset.channel_name, asset.asset_type, asset.ai_summary_de, asset.ai_trend_notes, asset.caption, asset.ocr_text, asset.kinetic_text]
        .filter(Boolean).join(' ').toLowerCase().includes(query);
    });
  }, [assets, filters]);

  async function run(fn) {
    setBusy(true); setError(''); setMessage('');
    try { await fn(); } catch (err) { setError(err.message || String(err)); } finally { setBusy(false); }
  }

  async function load() {
    const [h, c, t, a, stats, candidates] = await Promise.all([endpoints.health(), endpoints.channels(), endpoints.titles(), endpoints.assets(), endpoints.titleWhitelistStats().catch(() => null), endpoints.titleCandidates().catch(() => [])]);
    setHealth(h); setChannels(c); setTitles(t); setAssets(a); setWhitelistStats(stats); setTitleCandidates(candidates);
    try { setReport(await endpoints.latestReport()); } catch (_) { setReport(null); }
  }

  useEffect(() => { run(load); }, []);

  async function importChannelFile(event) {
    event.preventDefault();
    if (!channelFile) return;
    await run(async () => {
      const result = await endpoints.importChannelsExcel(channelFile);
      await endpoints.seedTitles();
      await load();
      setChannelFile(null);
      setMessage(`Kanalliste importiert: ${result.created} neu, ${result.updated} aktualisiert.`);
    });
  }

  async function runApifyMonitor() {
    await run(async () => {
      const result = await endpoints.runApifyMonitor({
        channel_ids: [],
        max_channels: Number(monitorForm.max_channels) || 5,
        results_limit_per_channel: Number(monitorForm.results_limit_per_channel) || 5,
        only_whitelist_matches: Boolean(monitorForm.only_whitelist_matches),
      });
      await load();
      setMessage(`Instagram: ${result.raw_items} Roh-Treffer, ${result.created_assets} neue Treffer.`);
    });
  }

  async function runTikTokMonitor() {
    await run(async () => {
      const username = normalizeHandle(tiktokForm.username);
      if (username) {
        await endpoints.createChannel({
          name: `TikTok @${username}`,
          platform: 'tiktok',
          url: `https://www.tiktok.com/@${username}`,
          handle: username,
          market: 'US',
          channel_type: 'Studio/Verleih',
          priority: 'A',
          active: true,
          mvp: true,
          notes: 'Automatisch angelegt.',
        }).catch(() => null);
      }
      const result = await endpoints.runTikTokMonitor({
        usernames: username ? [username] : [],
        max_channels: Number(tiktokForm.max_channels) || 1,
        results_limit_per_channel: Number(tiktokForm.results_limit_per_channel) || 5,
        only_whitelist_matches: Boolean(tiktokForm.only_whitelist_matches),
      });
      await load();
      setMessage(`TikTok: ${result.raw_items} Roh-Treffer, ${result.created_assets} neue Treffer.`);
    });
  }

  async function analyzeAssetVisual(asset) {
    await run(async () => {
      await endpoints.analyzeAssetVisual(asset.id);
      await load();
      setMessage('Bild-/Text-Prüfung aktualisiert.');
    });
  }

  async function runVisualBatch() {
    await run(async () => {
      const result = await endpoints.analyzeVisualBatch(10);
      await load();
      setBatchFeedback({ ...result, timestamp: new Date().toISOString() });
      setMessage(`Batchanalyse: ${result.analyzed} analysiert, ${result.no_source} ohne Bild, ${result.fetch_failed} Abrufprobleme, ${result.text_fallback} Text-Fallback, ${result.provider_error} KI-Probleme, ${result.failed} Fehler gesamt.`);
    });
  }

  async function reviewAsset(asset, status) {
    await run(async () => {
      await endpoints.reviewAsset(asset.id, {
        review_status: status,
        include_in_report: status === 'approved' || status === 'highlight',
        is_highlight: status === 'highlight',
        title_id: asset.title_id || null,
        curator_note: asset.curator_note || '',
      });
      await load();
      setMessage('Trefferstatus wurde aktualisiert.');
    });
  }

  async function assignTitle(asset, titleId) {
    await run(async () => {
      setRecentlyCreatedByAssetId((current) => {
        if (!current[asset.id]) return current;
        const next = { ...current };
        delete next[asset.id];
        return next;
      });
      await endpoints.reviewAsset(asset.id, {
        review_status: asset.review_status,
        include_in_report: asset.include_in_report,
        is_highlight: asset.is_highlight,
        title_id: titleId || null,
        curator_note: asset.curator_note || '',
      });
      await load();
      setMessage(titleId ? 'Filmtitel wurde zugeordnet.' : 'Filmtitel-Zuordnung wurde entfernt.');
    });
  }

  async function reportMissingTitle(asset, hint) {
    await run(async () => {
      await endpoints.createTitleCandidateFromAsset(asset.id, { suggested_title: hint?.label || null });
      await load();
      setMessage('Titelkandidat wurde zur Prüfung vorgemerkt.');
    });
  }

  async function syncTitleSources() {
    await run(async () => {
      const result = await endpoints.syncTmdbTitles({ markets: ['DE', 'US'], lookback_weeks: 8, lookahead_weeks: 24 });
      const rematch = await endpoints.rematchAssets();
      await load();
      setMessage(`Titelquellen aktualisiert: ${result.upserted_count} upserted, ${result.deduped_count} dedupliziert. Rematch: ${rematch.auto_matched} auto, ${rematch.candidates_created} Kandidaten.`);
    });
  }

  async function rematchAssets() {
    await run(async () => {
      const result = await endpoints.rematchAssets();
      await load();
      setMessage(`Rematch abgeschlossen: ${result.checked} geprüft, ${result.auto_matched} auto-zugeordnet, ${result.candidates_created} Kandidaten, ${result.still_unmatched} offen.`);
    });
  }

  async function generateReportSuggestion() {
    await run(async () => {
      const payload = {
        report_type: reportForm.report_type,
        date_range: reportForm.date_range,
        channels: reportForm.channel === "all" ? [] : [reportForm.channel],
        markets: reportForm.market === 'all' ? [] : [reportForm.market],
        limit: reportForm.limit,
      };
      const suggested = await endpoints.suggestReport(payload);
      setReportSuggestion(suggested);
      setMessage(suggested.selected > 0 ? 'Vorschlag erstellt. Bitte vorgeschlagene Assets prüfen und danach den Report erzeugen.' : 'Kein technischer Fehler. Es wurden nur keine passenden Treffer für diese Filter gefunden.');
    });
  }

  async function generateReportFromSuggestion() {
    await run(async () => {
      if (!reportSuggestion) return;
      const created = await endpoints.generateSuggestedReport({
        report_type: reportSuggestion.report_type,
        asset_ids: reportSuggestion.assets.map((a) => a.asset_id),
        date_range: reportForm.date_range,
      });
      setReport(created);
      setMessage('Report bereit. Die ausgewählten Treffer sind ausreichend geprüft und visuell dokumentiert.');
    });
  }
  async function relaxFiltersAndResuggest() {
    const nextForm = { ...reportForm, date_range: '30d', market: 'all', channel: 'all', limit: 10 };
    setReportForm(nextForm);
    await run(async () => {
      const suggested = await endpoints.suggestReport({ report_type: nextForm.report_type, date_range: nextForm.date_range, channels: [], markets: [], limit: 10 });
      setReportSuggestion(suggested);
    });
  }

  return (
    <main>
      <header className="hero">
        <div>
          <p className="eyebrow">Creative Intelligence Workspace</p>
          <h1>Creative Radar</h1>
          <p>Report-Typ wählen, Vorschlag prüfen und Report erzeugen – ohne jeden Treffer manuell freizugeben.</p>
        </div>
        <div className="hero-actions">
          <button className="primary" onClick={() => setActiveTab('Report erstellen')} disabled={busy}>Report erstellen</button>
          <button className="secondary" onClick={() => setActiveTab('Treffer prüfen')} disabled={busy}>Treffer prüfen</button>
        </div>
      </header>

      {error && <div className="error">{error}</div>}
      {message && <div className="success">{message}</div>}
      {busy && <div className="info">Arbeite gerade …</div>}
      {batchFeedback && (
        <div className="info">
          Batch-Ergebnis: {batchFeedback.analyzed} analysiert, {batchFeedback.no_source} ohne Bild, {batchFeedback.fetch_failed} Bildquelle nicht erreichbar, {batchFeedback.text_fallback} nur Textanalyse, {batchFeedback.provider_error} KI-/Provider-Fehler, {batchFeedback.failed} Fehler gesamt (geprüft: {batchFeedback.checked}, {formatDate(batchFeedback.timestamp)}).
        </div>
      )}

      <nav className="tabs">
        {NAV_ITEMS.map((tab) => (
          <button key={tab} className={activeTab === tab ? 'active' : ''} onClick={() => setActiveTab(tab)}>{tab}</button>
        ))}
      </nav>

      {activeTab === 'Report erstellen' && (
        <ReportsPanel
          report={report}
          busy={busy}
          suggestion={reportSuggestion}
          form={reportForm}
          setForm={setReportForm}
          onSuggest={generateReportSuggestion}
          onGenerateSuggestedReport={generateReportFromSuggestion}
          onRelaxFiltersAndSuggest={relaxFiltersAndResuggest}
        />
      )}
      {activeTab === 'Treffer prüfen' && (
        <ReviewPanel
          assets={assets}
          titles={sortedTitles}
          visibleAssets={visibleAssets}
          filters={filters}
          setFilters={setFilters}
          busy={busy}
          onReview={reviewAsset}
          onAnalyzeVisual={analyzeAssetVisual}
          onAssignTitle={assignTitle}
          onReportMissingTitle={reportMissingTitle}
          recentlyCreatedByAssetId={recentlyCreatedByAssetId}
        />
      )}
      {activeTab === 'Quellen' && (
        <SourcesPanel
          busy={busy}
          channelFile={channelFile}
          setChannelFile={setChannelFile}
          onImportChannelFile={importChannelFile}
          monitorForm={monitorForm}
          setMonitorForm={setMonitorForm}
          onInstagram={runApifyMonitor}
          tiktokForm={tiktokForm}
          setTiktokForm={setTiktokForm}
          onTikTok={runTikTokMonitor}
          whitelistStats={whitelistStats}
          titleCandidates={titleCandidates}
          onSyncTitleSources={syncTitleSources}
          onRematchAssets={rematchAssets}
        />
      )}

      <footer className="footer-status">
        API: {health?.status || 'offen'} · Kanäle {channels.length} · Titel {titles.length} · Treffer {assets.length}
        <button className="secondary" onClick={() => run(load)} disabled={busy}>Aktualisieren</button>
      </footer>
    </main>
  );
}

createRoot(document.getElementById('root')).render(<App />);
