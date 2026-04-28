import React, { useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { endpoints } from './api/client';
import './styles.css';

const STATUS_OPTIONS = ['all', 'new', 'approved', 'highlight', 'rejected', 'needs_review'];
const TABS = ['Radar', 'Review', 'Reports', 'Quellen'];

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
  try {
    return new Intl.NumberFormat('de-DE').format(Number(value));
  } catch (_) {
    return String(value);
  }
}

function formatDate(value) {
  if (!value) return 'Datum offen';
  try {
    return new Date(value).toLocaleDateString('de-DE');
  } catch (_) {
    return 'Datum offen';
  }
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

function ImagePreview({ src }) {
  const [failed, setFailed] = useState(false);
  if (!src || failed) return <div className="preview-placeholder compact">Kein Preview</div>;
  return <img src={src} alt="Asset Preview" onError={() => setFailed(true)} />;
}

function Metric({ label, value, tone = '' }) {
  return (
    <div className={`radar-metric ${tone}`}>
      <strong>{formatNumber(value)}</strong>
      <span>{label}</span>
    </div>
  );
}

function MetricStrip({ asset }) {
  const metrics = [
    ['Views', asset.visible_views],
    ['Likes', asset.visible_likes],
    ['Shares', asset.visible_shares],
    ['Comments', asset.visible_comments],
    ['Saves', asset.visible_bookmarks],
  ];
  return (
    <div className="metric-strip">
      {metrics.map(([label, value]) => (
        <span key={label}>
          <b>{formatNumber(value)}</b>
          <small>{label}</small>
        </span>
      ))}
      {asset.duration_seconds ? (
        <span>
          <b>{asset.duration_seconds}s</b>
          <small>Dauer</small>
        </span>
      ) : null}
    </div>
  );
}

function MiniTable({ title, rows, columns, emptyText = 'Noch keine Daten.' }) {
  return (
    <div className="mini-table-wrap compact-table">
      <h3>{title}</h3>
      {(!rows || rows.length === 0) ? (
        <p className="muted">{emptyText}</p>
      ) : (
        <table className="mini-table">
          <thead>
            <tr>{columns.map((col) => <th key={col.key}>{col.label}</th>)}</tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={`${title}-${index}`}>
                {columns.map((col) => (
                  <td key={col.key}>{col.render ? col.render(row, index) : row[col.key]}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function ReportReadiness({ approved, highlights, openReview, missingPreviews, missingVisual }) {
  const ready = approved > 0;
  return (
    <div className="readiness-card">
      <div>
        <strong>{ready ? 'Report bereit' : 'Report noch nicht bereit'}</strong>
        <span>{approved} reportfähige Assets · {highlights} Highlights · {openReview} offen</span>
      </div>
      <div className="quality-pills">
        <span className={missingPreviews ? 'check warn' : 'check good'}>
          {missingPreviews ? `${missingPreviews} ohne Preview` : 'Previews ok'}
        </span>
        <span className={missingVisual ? 'check warn' : 'check good'}>
          {missingVisual ? `${missingVisual} ohne Visual/OCR` : 'Visual/OCR ok'}
        </span>
      </div>
    </div>
  );
}

function AssetCard({ asset, busy, onReview, onAnalyzeVisual }) {
  const preview = asset.thumbnail_url || asset.screenshot_url;
  const platform = asset.platform || asset.channel_platform || asset.media_type || 'instagram';
  const hasTitle = Boolean(asset.title_name || asset.placement_title_text);
  const title = hasTitle ? (asset.title_name || asset.placement_title_text) : 'Neuer Fund – Titel noch offen';
  const subtitle = `${asset.channel_name || 'Unbekannter Channel'} · ${asset.channel_market || 'UNKNOWN'} · ${platform} · ${formatDate(asset.published_at || asset.detected_at || asset.created_at)}`;

  return (
    <article className="asset-card simplified">
      <div className="asset-preview"><ImagePreview src={preview} /></div>
      <div className="asset-content">
        <div className="asset-topline">
          <span className="asset-title">{title}</span>
          <span className={`pill platform-${platform}`}>{platform}</span>
          <span className="pill">{asset.asset_type || 'Unknown'}</span>
          <span className={`pill status-${asset.review_status}`}>{asset.review_status}</span>
          {!hasTitle && <span className="pill discovery">Discovery</span>}
          {asset.has_title_placement && <span className="pill placement">Titel-Placement</span>}
          {asset.has_kinetic && <span className="pill kinetic">Kinetic</span>}
        </div>
        <div className="asset-meta">{subtitle}</div>
        <MetricStrip asset={asset} />
        <p className="asset-summary">{clip(asset.ai_summary_de || 'Noch keine KI-Zusammenfassung vorhanden.', 300)}</p>
        <div className="asset-links">
          {asset.post_url && <a href={asset.post_url} target="_blank" rel="noreferrer">Original öffnen</a>}
          <details>
            <summary>Details</summary>
            {asset.caption && <p><strong>Caption:</strong> {asset.caption}</p>}
            {asset.ai_trend_notes && <p><strong>Pattern:</strong> {asset.ai_trend_notes}</p>}
            {asset.ai_summary_en && <p><strong>EN:</strong> {asset.ai_summary_en}</p>}
            {(asset.ocr_text || asset.visual_notes || asset.kinetic_text) && (
              <p><strong>Visual/OCR:</strong> {[asset.ocr_text, asset.visual_notes, asset.kinetic_text].filter(Boolean).join(' · ')}</p>
            )}
          </details>
        </div>
      </div>
      <div className="asset-actions simplified-actions">
        <button className="primary" onClick={() => onReview(asset, 'highlight')} disabled={busy}>Highlight</button>
        <button onClick={() => onReview(asset, 'approved')} disabled={busy}>Freigeben</button>
        <button onClick={() => onReview(asset, 'rejected')} disabled={busy}>Ablehnen</button>
        <button className="secondary" onClick={() => onAnalyzeVisual(asset)} disabled={busy}>Visual/OCR</button>
      </div>
    </article>
  );
}

function RadarPanel({ insights, assets, approved, highlights, openReview, missingPreviews, missingVisual, onAnalyzeVisualBatch, busy }) {
  const topAssets = insights?.top_assets_total || [];
  const channels = insights?.channel_rankings || [];
  const placements = insights?.placement_comparison || [];
  const tiktokCount = assets.filter((a) => (a.platform || a.channel_platform) === 'tiktok').length;
  const instagramCount = assets.filter((a) => (a.platform || a.channel_platform) === 'instagram').length;

  return (
    <>
      <Section title="Radar Heute" kicker="Management-Übersicht" className="hero-panel">
        <div className="radar-metrics">
          <Metric label="Assets dokumentiert" value={assets.length} />
          <Metric label="Review offen" value={openReview} tone={openReview ? 'warn' : 'good'} />
          <Metric label="Reportfähig" value={approved.length} tone={approved.length ? 'good' : 'warn'} />
          <Metric label="Highlights" value={highlights.length} />
          <Metric label="TikTok" value={tiktokCount} />
          <Metric label="Instagram" value={instagramCount} />
        </div>
        <ReportReadiness
          approved={approved.length}
          highlights={highlights.length}
          openReview={openReview}
          missingPreviews={missingPreviews}
          missingVisual={missingVisual}
        />
        <div className="section-actions">
          <button onClick={onAnalyzeVisualBatch} disabled={busy || assets.length === 0}>Alle neuen Assets visuell prüfen</button>
        </div>
      </Section>

      <div className="grid two-col">
        <MiniTable title="Top Assets gesamt" rows={topAssets.slice(0, 6)} columns={[
          { key: 'rank', label: '#', render: (_row, i) => i + 1 },
          { key: 'title', label: 'Titel' },
          { key: 'channel', label: 'Channel' },
          { key: 'score', label: 'Score', render: (row) => formatNumber(row.score) },
        ]} />
        <MiniTable title="DE/US Placement-Vergleich" rows={placements.slice(0, 6)} columns={[
          { key: 'match_key', label: 'Titel-Key' },
          { key: 'de_count', label: 'DE' },
          { key: 'us_count', label: 'US' },
          { key: 'gap', label: 'Gap' },
        ]} emptyText="Noch kein belastbarer DE/US-Vergleich. Erst Discovery-Funde Titeln zuordnen und Visual/OCR prüfen." />
        <MiniTable title="Channel-Ranking" rows={channels.slice(0, 8)} columns={[
          { key: 'channel', label: 'Channel' },
          { key: 'count', label: 'Assets' },
          { key: 'top_assets', label: 'Top-Treffer', render: (row) => row.top_assets?.[0]?.title || '—' },
        ]} />
        <div className="mini-table-wrap compact-table">
          <h3>Datenqualität</h3>
          <ul className="recommendations">
            <li>{assets.filter((a) => a.is_discovery || !a.title_name).length} Discovery-Funde brauchen Titelzuordnung für DE/US-Vergleich.</li>
            <li>{missingPreviews} Assets ohne Preview/Screenshot.</li>
            <li>{missingVisual} Assets ohne Visual/OCR-Prüfung.</li>
          </ul>
        </div>
      </div>
    </>
  );
}

function ReviewPanel({ assets, visibleAssets, filters, setFilters, busy, onReview, onAnalyzeVisual }) {
  return (
    <Section title="Review Queue" kicker={`${visibleAssets.length} von ${assets.length} sichtbar`} className="review-section">
      <div className="filterbar simplified-filterbar">
        <select value={filters.status} onChange={(e) => setFilters({ ...filters, status: e.target.value })}>
          {STATUS_OPTIONS.map((status) => <option key={status} value={status}>{status === 'all' ? 'Alle Status' : status}</option>)}
        </select>
        <select value={filters.platform} onChange={(e) => setFilters({ ...filters, platform: e.target.value })}>
          <option value="all">Alle Plattformen</option>
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
        <input value={filters.query} onChange={(e) => setFilters({ ...filters, query: e.target.value })} placeholder="Suche nach Titel, Channel, Pattern …" />
      </div>
      {assets.length === 0 && <p>Noch keine Assets. Erst Kanäle prüfen.</p>}
      {visibleAssets.map((asset) => <AssetCard key={asset.id} asset={asset} busy={busy} onReview={onReview} onAnalyzeVisual={onAnalyzeVisual} />)}
    </Section>
  );
}

function ReportsPanel({ report, approved, highlights, openReview, missingPreviews, missingVisual, busy, onGenerateReport }) {
  return (
    <Section title="Weekly Report" kicker="Donnerstags-Output">
      <ReportReadiness
        approved={approved.length}
        highlights={highlights.length}
        openReview={openReview}
        missingPreviews={missingPreviews}
        missingVisual={missingVisual}
      />
      <div className="section-actions">
        <button onClick={onGenerateReport} disabled={busy || approved.length === 0}>Weekly Report erzeugen</button>
      </div>
      {report ? (
        <div className="report-summary">
          <p><strong>Status:</strong> {report.status}</p>
          <p>{report.executive_summary_de}</p>
          <details><summary>Report-Vorschau öffnen</summary><iframe title="report" srcDoc={report.html_content || ''} /></details>
        </div>
      ) : <p className="muted">Noch kein Report erzeugt.</p>}
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
  quickForm,
  setQuickForm,
  onAnalyzeLink,
  form,
  setForm,
  onImportPost,
  sortedChannels,
  sortedTitles,
}) {
  return (
    <>
      <Section title="Kanäle prüfen" kicker="Datenquellen">
        <div className="source-grid">
          <div className="source-card">
            <h3>TikTok</h3>
            <label>Username oder Profil-URL
              <input value={tiktokForm.username} onChange={(e) => setTiktokForm({ ...tiktokForm, username: e.target.value })} placeholder="warnerbros" />
            </label>
            <label>Videos pro Profil
              <input type="number" min="1" max="20" value={tiktokForm.results_limit_per_channel} onChange={(e) => setTiktokForm({ ...tiktokForm, results_limit_per_channel: e.target.value })} />
            </label>
            <label className="checkbox-row">
              <input type="checkbox" checked={tiktokForm.only_whitelist_matches} onChange={(e) => setTiktokForm({ ...tiktokForm, only_whitelist_matches: e.target.checked })} /> Nur Whitelist-Treffer
            </label>
            <button onClick={onTikTok} disabled={busy || !tiktokForm.username}>TikTok prüfen</button>
          </div>
          <div className="source-card">
            <h3>Instagram</h3>
            <label>Max. Channels
              <input type="number" min="1" max="25" value={monitorForm.max_channels} onChange={(e) => setMonitorForm({ ...monitorForm, max_channels: e.target.value })} />
            </label>
            <label>Posts/Reels pro Channel
              <input type="number" min="1" max="20" value={monitorForm.results_limit_per_channel} onChange={(e) => setMonitorForm({ ...monitorForm, results_limit_per_channel: e.target.value })} />
            </label>
            <label className="checkbox-row">
              <input type="checkbox" checked={monitorForm.only_whitelist_matches} onChange={(e) => setMonitorForm({ ...monitorForm, only_whitelist_matches: e.target.checked })} /> Nur Whitelist-Treffer
            </label>
            <button onClick={onInstagram} disabled={busy}>Instagram prüfen</button>
          </div>
        </div>
      </Section>
      <details className="card setup-details">
        <summary>Setup & Sonderfälle</summary>
        <form className="form-grid" onSubmit={onImportChannelFile}>
          <label className="wide">Excel-Kanalliste
            <input type="file" accept=".xlsx,.xlsm" onChange={(event) => setChannelFile(event.target.files?.[0] || null)} />
          </label>
          <div className="wide"><button type="submit" disabled={busy || !channelFile}>Kanalliste importieren</button></div>
        </form>
        <hr />
        <form className="form-grid" onSubmit={onAnalyzeLink}>
          <label className="wide">Instagram-Link
            <input value={quickForm.post_url} onChange={(e) => setQuickForm({ ...quickForm, post_url: e.target.value })} placeholder="https://www.instagram.com/p/..." />
          </label>
          <label>Channel
            <select value={quickForm.channel_id} onChange={(e) => setQuickForm({ ...quickForm, channel_id: e.target.value })}>
              <option value="">Auto</option>
              {sortedChannels.map((channel) => <option key={channel.id} value={channel.id}>{channel.platform} · {channel.market} · {channel.name}</option>)}
            </select>
          </label>
          <label>Titel
            <select value={quickForm.title_id} onChange={(e) => setQuickForm({ ...quickForm, title_id: e.target.value })}>
              <option value="">Auto</option>
              {sortedTitles.map((title) => <option key={title.id} value={title.id}>{title.title_original}</option>)}
            </select>
          </label>
          <div className="wide"><button type="submit" disabled={busy || !quickForm.post_url}>Einzel-Link analysieren</button></div>
        </form>
        <hr />
        <form className="form-grid" onSubmit={onImportPost}>
          <label>Channel
            <select value={form.channel_id} onChange={(e) => setForm({ ...form, channel_id: e.target.value })} required>
              <option value="">Bitte wählen</option>
              {sortedChannels.map((channel) => <option key={channel.id} value={channel.id}>{channel.platform} · {channel.market} · {channel.name}</option>)}
            </select>
          </label>
          <label>Titel
            <select value={form.title_id} onChange={(e) => setForm({ ...form, title_id: e.target.value })}>
              <option value="">Auto</option>
              {sortedTitles.map((title) => <option key={title.id} value={title.id}>{title.title_original}</option>)}
            </select>
          </label>
          <label className="wide">Post-Link
            <input value={form.post_url} onChange={(e) => setForm({ ...form, post_url: e.target.value })} />
          </label>
          <label className="wide">Caption
            <textarea value={form.caption} onChange={(e) => setForm({ ...form, caption: e.target.value })} rows="3" />
          </label>
          <div className="wide"><button type="submit" disabled={busy || !form.channel_id || !form.post_url}>Manuell importieren</button></div>
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
  const [insights, setInsights] = useState(null);
  const [report, setReport] = useState(null);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [activeTab, setActiveTab] = useState('Radar');
  const [channelFile, setChannelFile] = useState(null);
  const [monitorForm, setMonitorForm] = useState({ max_channels: 5, results_limit_per_channel: 5, only_whitelist_matches: true });
  const [tiktokForm, setTiktokForm] = useState({ username: 'warnerbros', max_channels: 1, results_limit_per_channel: 5, only_whitelist_matches: false });
  const [filters, setFilters] = useState({ status: 'all', platform: 'all', market: 'all', assetType: 'all', discovery: 'all', query: '' });
  const [quickForm, setQuickForm] = useState({ post_url: '', channel_id: '', title_id: '', caption_hint: '', asset_type_hint: 'Unknown' });
  const [form, setForm] = useState({ channel_id: '', title_id: '', post_url: '', caption: '', media_type: 'reel', asset_type: 'Unknown', screenshot_url: '', ocr_text: '' });

  const sortedChannels = useMemo(() => [...channels].sort((a, b) => `${a.platform}-${a.market}-${a.name}`.localeCompare(`${b.platform}-${b.market}-${b.name}`)), [channels]);
  const sortedTitles = useMemo(() => [...titles].sort((a, b) => a.title_original.localeCompare(b.title_original)), [titles]);
  const approved = assets.filter((asset) => asset.include_in_report || asset.review_status === 'approved' || asset.review_status === 'highlight');
  const highlights = assets.filter((asset) => asset.is_highlight || asset.review_status === 'highlight');
  const openReview = assets.filter((asset) => asset.review_status === 'new' || asset.review_status === 'needs_review').length;
  const missingPreviews = assets.filter((asset) => !(asset.thumbnail_url || asset.screenshot_url)).length;
  const missingVisual = assets.filter((asset) => !asset.visual_analysis_status || ['pending', 'error'].includes(asset.visual_analysis_status)).length;

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
    const [h, c, t, a, overview] = await Promise.all([endpoints.health(), endpoints.channels(), endpoints.titles(), endpoints.assets(), endpoints.insightsOverview()]);
    setHealth(h); setChannels(c); setTitles(t); setAssets(a); setInsights(overview);
    try { setReport(await endpoints.latestReport()); } catch (_) { setReport(null); }
  }

  useEffect(() => { run(load); }, []);

  async function seed() {
    await run(async () => { await endpoints.seedChannels(); await endpoints.seedTitles(); await load(); setMessage('Basisdaten wurden angelegt.'); });
  }

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
      setMessage(`Instagram: ${result.raw_items} Roh-Treffer, ${result.created_assets} neue Assets.`);
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
          notes: 'Automatisch aus TikTok-Testlauf angelegt.',
        }).catch(() => null);
      }
      const result = await endpoints.runTikTokMonitor({
        usernames: username ? [username] : [],
        max_channels: Number(tiktokForm.max_channels) || 1,
        results_limit_per_channel: Number(tiktokForm.results_limit_per_channel) || 5,
        only_whitelist_matches: Boolean(tiktokForm.only_whitelist_matches),
      });
      await load();
      setMessage(`TikTok: ${result.raw_items} Roh-Treffer, ${result.created_assets} neue Assets, ${result.skipped_existing} bereits vorhanden.`);
    });
  }

  async function analyzeVisualBatch() {
    await run(async () => {
      const result = await endpoints.analyzeVisualBatch(10);
      await load();
      setMessage(`Visual/OCR: ${result.updated} Assets aktualisiert.`);
    });
  }

  async function analyzeAssetVisual(asset) {
    await run(async () => {
      await endpoints.analyzeAssetVisual(asset.id);
      await load();
      setMessage('Visual/OCR aktualisiert.');
    });
  }

  async function analyzeLink(event) {
    event.preventDefault();
    await run(async () => {
      await endpoints.analyzeInstagramLink({ ...quickForm, channel_id: quickForm.channel_id || null, title_id: quickForm.title_id || null, caption_hint: quickForm.caption_hint || null });
      await load();
      setQuickForm({ post_url: '', channel_id: '', title_id: '', caption_hint: '', asset_type_hint: 'Unknown' });
      setMessage('Einzel-Link analysiert.');
    });
  }

  async function importPost(event) {
    event.preventDefault();
    await run(async () => {
      await endpoints.manualImport({ ...form, title_id: form.title_id || null, screenshot_url: form.screenshot_url || null, ocr_text: form.ocr_text || null, caption: form.caption || null, media_type: form.media_type || null });
      setForm({ ...form, post_url: '', caption: '', screenshot_url: '', ocr_text: '', asset_type: 'Unknown' });
      await load();
      setMessage('Treffer importiert.');
    });
  }

  async function reviewAsset(asset, status) {
    await run(async () => {
      await endpoints.reviewAsset(asset.id, {
        review_status: status,
        include_in_report: status === 'approved' || status === 'highlight',
        is_highlight: status === 'highlight',
        curator_note: asset.curator_note || '',
      });
      await load();
      setMessage(`Asset wurde als ${status} gespeichert.`);
    });
  }

  async function generateReport() {
    await run(async () => {
      const today = new Date();
      const end = today.toISOString().slice(0, 10);
      const startDate = new Date(today.getTime() - 7 * 86400000).toISOString().slice(0, 10);
      const created = await endpoints.generateReport({ week_start: startDate, week_end: end, include_only_reviewed: true });
      setReport(created);
      setMessage('Report-Entwurf wurde erzeugt.');
    });
  }

  return (
    <main>
      <header className="hero simplified-hero">
        <div>
          <p className="eyebrow">Creative Radar · Weekly Creative Monitoring</p>
          <h1>Creative Radar</h1>
          <p>Kanäle prüfen, relevante Assets kuratieren, DE/US-Patterns erkennen und Weekly Report erstellen.</p>
        </div>
        <div className="hero-actions">
          <button onClick={() => setActiveTab('Quellen')} disabled={busy}>Kanäle prüfen</button>
          <button onClick={() => setActiveTab('Review')} disabled={busy}>Assets reviewen</button>
          <button onClick={generateReport} disabled={busy || approved.length === 0}>Weekly Report</button>
          <button className="secondary" onClick={() => run(load)} disabled={busy}>Aktualisieren</button>
        </div>
      </header>
      {error && <div className="error">{error}</div>}
      {message && <div className="success">{message}</div>}
      {busy && <div className="info">Arbeite gerade …</div>}
      <nav className="tabs">
        {TABS.map((tab) => <button key={tab} className={activeTab === tab ? 'active' : ''} onClick={() => setActiveTab(tab)}>{tab}</button>)}
      </nav>

      {activeTab === 'Radar' && (
        <RadarPanel
          insights={insights}
          assets={assets}
          approved={approved}
          highlights={highlights}
          openReview={openReview}
          missingPreviews={missingPreviews}
          missingVisual={missingVisual}
          onAnalyzeVisualBatch={analyzeVisualBatch}
          busy={busy}
        />
      )}
      {activeTab === 'Review' && (
        <ReviewPanel
          assets={assets}
          visibleAssets={visibleAssets}
          filters={filters}
          setFilters={setFilters}
          busy={busy}
          onReview={reviewAsset}
          onAnalyzeVisual={analyzeAssetVisual}
        />
      )}
      {activeTab === 'Reports' && (
        <ReportsPanel
          report={report}
          approved={approved}
          highlights={highlights}
          openReview={openReview}
          missingPreviews={missingPreviews}
          missingVisual={missingVisual}
          busy={busy}
          onGenerateReport={generateReport}
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
          quickForm={quickForm}
          setQuickForm={setQuickForm}
          onAnalyzeLink={analyzeLink}
          form={form}
          setForm={setForm}
          onImportPost={importPost}
          sortedChannels={sortedChannels}
          sortedTitles={sortedTitles}
        />
      )}

      <footer className="footer-status">
        API: {health?.status || 'offen'} · Channels {channels.length} · Whitelist {titles.length} · Assets {assets.length}
        <button onClick={seed} disabled={busy}>Basisdaten</button>
      </footer>
    </main>
  );
}

createRoot(document.getElementById('root')).render(<App />);
