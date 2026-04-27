import React, { useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { endpoints } from './api/client';
import './styles.css';

const ASSET_TYPES = [
  'Trailer', 'Trailer Drop', 'Teaser', 'Poster', 'Poster / Key Art', 'Story', 'Kinetic',
  'Character Card', 'Character / Cast Post', 'Quote / Review', 'CTA Post', 'Ticket CTA',
  'Release Reminder', 'Behind the Scenes', 'Event / Festival', 'Series Episode Push',
  'Franchise / Brand Post', 'Discovery', 'Unknown'
];

const STATUS_OPTIONS = ['all', 'new', 'approved', 'highlight', 'rejected', 'needs_review'];

function Section({ title, kicker, children, className = '' }) {
  return <section className={`card ${className}`}>{kicker && <p className="section-kicker">{kicker}</p>}<h2>{title}</h2>{children}</section>;
}

function ImagePreview({ src }) {
  const [failed, setFailed] = useState(false);
  if (!src || failed) return <div className="preview-placeholder">Kein Preview verfügbar</div>;
  return <img src={src} alt="Asset Preview" onError={() => setFailed(true)} />;
}

function formatDate(value) {
  if (!value) return 'Datum offen';
  try { return new Date(value).toLocaleDateString('de-DE'); } catch (_) { return 'Datum offen'; }
}

function clip(text, max = 340) {
  if (!text) return '';
  return text.length > max ? `${text.slice(0, max).trim()} …` : text;
}

function AssetCard({ asset, busy, onReview }) {
  const preview = asset.thumbnail_url || asset.screenshot_url;
  const channel = asset.channel_name || 'Unbekannter Channel';
  const market = asset.channel_market || 'UNKNOWN';
  const title = asset.title_name || 'Discovery · kein Whitelist-Match';
  const isDiscovery = asset.is_discovery || !asset.title_name;
  const date = formatDate(asset.published_at || asset.detected_at || asset.created_at);

  return (
    <article className="asset-card">
      <div className="asset-preview"><ImagePreview src={preview} /></div>
      <div className="asset-content">
        <div className="asset-topline">
          <span className="asset-title">{title}</span>
          <span className="pill">{asset.asset_type || 'Unknown'}</span>
          <span className={`pill status-${asset.review_status}`}>{asset.review_status}</span>
          {isDiscovery && <span className="pill discovery">Discovery</span>}
        </div>
        <div className="asset-meta">
          {channel} · {market} · {asset.media_type || 'Instagram'} · {date}
          {asset.confidence_score !== null && asset.confidence_score !== undefined ? ` · Confidence ${Math.round(asset.confidence_score * 100)}%` : ''}
        </div>
        <p className="asset-summary">{clip(asset.ai_summary_de || 'Keine KI-Zusammenfassung vorhanden.', 420)}</p>
        {asset.ai_trend_notes && <p className="asset-trend">{clip(asset.ai_trend_notes, 300)}</p>}
        <div className="asset-links">
          {asset.post_url && <a href={asset.post_url} target="_blank" rel="noreferrer">Original öffnen</a>}
          {asset.caption && <details><summary>Caption / Details</summary><p>{asset.caption}</p>{asset.ai_summary_en && <p>{asset.ai_summary_en}</p>}</details>}
        </div>
      </div>
      <div className="asset-actions">
        <button onClick={() => onReview(asset, 'approved')} disabled={busy}>Approve</button>
        <button onClick={() => onReview(asset, 'highlight')} disabled={busy}>Highlight</button>
        <button onClick={() => onReview(asset, 'needs_review')} disabled={busy}>Später prüfen</button>
        <button onClick={() => onReview(asset, 'rejected')} disabled={busy}>Reject</button>
      </div>
    </article>
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
  const [channelFile, setChannelFile] = useState(null);
  const [monitorForm, setMonitorForm] = useState({ max_channels: 5, results_limit_per_channel: 5, only_whitelist_matches: true });
  const [filters, setFilters] = useState({ status: 'all', market: 'all', assetType: 'all', discovery: 'all', query: '' });
  const [quickForm, setQuickForm] = useState({ post_url: '', channel_id: '', title_id: '', caption_hint: '', asset_type_hint: 'Unknown' });
  const [form, setForm] = useState({ channel_id: '', title_id: '', post_url: '', caption: '', media_type: 'reel', asset_type: 'Unknown', screenshot_url: '', ocr_text: '' });

  const sortedChannels = useMemo(() => [...channels].sort((a, b) => `${a.market}-${a.name}`.localeCompare(`${b.market}-${b.name}`)), [channels]);
  const sortedTitles = useMemo(() => [...titles].sort((a, b) => a.title_original.localeCompare(b.title_original)), [titles]);
  const highlights = assets.filter((asset) => asset.is_highlight || asset.review_status === 'highlight');
  const approved = assets.filter((asset) => asset.include_in_report || asset.review_status === 'approved' || asset.review_status === 'highlight');
  const discoveryCount = assets.filter((asset) => asset.is_discovery || !asset.title_name).length;

  const visibleAssets = useMemo(() => {
    const query = filters.query.trim().toLowerCase();
    return assets.filter((asset) => {
      if (filters.status !== 'all' && asset.review_status !== filters.status) return false;
      if (filters.market !== 'all' && asset.channel_market !== filters.market) return false;
      if (filters.assetType !== 'all' && asset.asset_type !== filters.assetType) return false;
      const isDiscovery = asset.is_discovery || !asset.title_name;
      if (filters.discovery === 'discovery' && !isDiscovery) return false;
      if (filters.discovery === 'whitelist' && isDiscovery) return false;
      if (!query) return true;
      return [asset.title_name, asset.channel_name, asset.asset_type, asset.ai_summary_de, asset.ai_trend_notes, asset.caption]
        .filter(Boolean).join(' ').toLowerCase().includes(query);
    });
  }, [assets, filters]);

  async function run(fn) {
    setBusy(true); setError(''); setMessage('');
    try { await fn(); } catch (err) { setError(err.message || String(err)); } finally { setBusy(false); }
  }

  async function load() {
    const [h, c, t, a] = await Promise.all([endpoints.health(), endpoints.channels(), endpoints.titles(), endpoints.assets()]);
    setHealth(h); setChannels(c); setTitles(t); setAssets(a);
    try { setReport(await endpoints.latestReport()); } catch (_) { setReport(null); }
  }

  useEffect(() => { run(load); }, []);

  async function seed() {
    await run(async () => {
      await endpoints.seedChannels(); await endpoints.seedTitles(); await load();
      setMessage('Basisdaten wurden angelegt. Für die vollständige Kanalliste bitte die Excel-Datei importieren.');
    });
  }

  async function importChannelFile(event) {
    event.preventDefault();
    if (!channelFile) return;
    await run(async () => {
      const result = await endpoints.importChannelsExcel(channelFile);
      await endpoints.seedTitles(); await load(); setChannelFile(null);
      setMessage(`Kanalliste importiert: ${result.created} neu, ${result.updated} aktualisiert, ${result.skipped} übersprungen.`);
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
      setMessage(`Apify-Monitoring abgeschlossen: ${result.channels_checked} Channels geprüft, ${result.raw_items} Roh-Treffer, ${result.created_assets} neue Assets, ${result.skipped_no_whitelist_match} ohne Whitelist-Treffer übersprungen.`);
    });
  }

  async function analyzeLink(event) {
    event.preventDefault();
    await run(async () => {
      const result = await endpoints.analyzeInstagramLink({ ...quickForm, channel_id: quickForm.channel_id || null, title_id: quickForm.title_id || null, caption_hint: quickForm.caption_hint || null });
      await load(); setQuickForm({ post_url: '', channel_id: '', title_id: '', caption_hint: '', asset_type_hint: 'Unknown' });
      setMessage(result.already_exists ? 'Dieser Link war bereits vorhanden. Asset unten prüfen.' : 'Instagram-Link analysiert. Asset unten prüfen, freigeben oder highlighten.');
    });
  }

  async function importPost(event) {
    event.preventDefault();
    await run(async () => {
      await endpoints.manualImport({ ...form, title_id: form.title_id || null, screenshot_url: form.screenshot_url || null, ocr_text: form.ocr_text || null, caption: form.caption || null, media_type: form.media_type || null });
      setForm({ ...form, post_url: '', caption: '', screenshot_url: '', ocr_text: '', asset_type: 'Unknown' }); await load();
      setMessage('Treffer importiert. Unten im Asset Review prüfen und freigeben.');
    });
  }

  async function reviewAsset(asset, status) {
    await run(async () => {
      await endpoints.reviewAsset(asset.id, { review_status: status, include_in_report: status === 'approved' || status === 'highlight', is_highlight: status === 'highlight', curator_note: asset.curator_note || '' });
      await load(); setMessage(`Asset wurde als ${status} gespeichert.`);
    });
  }

  async function generateReport() {
    await run(async () => {
      const today = new Date();
      const end = today.toISOString().slice(0, 10);
      const startDate = new Date(today.getTime() - 7 * 86400000).toISOString().slice(0, 10);
      const created = await endpoints.generateReport({ week_start: startDate, week_end: end, include_only_reviewed: true });
      setReport(created); setMessage('Report-Entwurf wurde aus freigegebenen Assets erzeugt.');
    });
  }

  return (
    <main>
      <header className="hero">
        <div><p className="eyebrow">Creative Radar · Online MVP</p><h1>Creative Radar v1</h1><p>Kanalliste importieren, Channels automatisch prüfen, Assets kuratieren, Report erzeugen.</p></div>
        <div className="hero-actions"><button onClick={seed} disabled={busy}>Basisdaten anlegen</button><button onClick={() => run(load)} disabled={busy}>Aktualisieren</button></div>
      </header>

      {error && <div className="error">{error}</div>}
      {message && <div className="success">{message}</div>}
      {busy && <div className="info">Arbeite gerade …</div>}

      <div className="grid">
        <Section title="Systemstatus"><p>Status: <strong>{health?.status || 'nicht verbunden'}</strong></p><p>Channels: {channels.length}</p><p>Whitelist-Titel: {titles.length}</p><p>Assets: {assets.length}</p><p>Discovery: {discoveryCount}</p><p>Freigegeben: {approved.length}</p><p>Highlights: {highlights.length}</p></Section>
        <Section title="Report-Zentrale"><p>{approved.length} Assets sind reportfähig.</p><button onClick={generateReport} disabled={busy || approved.length === 0}>Report-Entwurf erzeugen</button>{approved.length === 0 && <p className="muted">Erst mindestens ein Asset approven oder highlighten.</p>}</Section>
      </div>

      <Section title="Kanalliste importieren" kicker="Einmaliger Setup-Schritt"><form className="form-grid" onSubmit={importChannelFile}><label className="wide">Excel-Datei mit FILMVERLEIH / INSTAGRAM<input type="file" accept=".xlsx,.xlsm" onChange={(event) => setChannelFile(event.target.files?.[0] || null)} /></label><div className="wide"><button type="submit" disabled={busy || !channelFile}>Excel-Kanalliste importieren</button></div></form><p className="muted small">Die Excel-Datei muss nur erneut importiert werden, wenn sich die Kanalliste geändert hat.</p></Section>

      <Section title="Kanäle automatisch prüfen" kicker="Apify Public Monitor"><div className="form-grid"><label>Max. Channels pro Lauf<input type="number" min="1" max="25" value={monitorForm.max_channels} onChange={e => setMonitorForm({ ...monitorForm, max_channels: e.target.value })} /></label><label>Posts/Reels pro Channel<input type="number" min="1" max="20" value={monitorForm.results_limit_per_channel} onChange={e => setMonitorForm({ ...monitorForm, results_limit_per_channel: e.target.value })} /></label><label className="wide checkbox-row"><input type="checkbox" checked={monitorForm.only_whitelist_matches} onChange={e => setMonitorForm({ ...monitorForm, only_whitelist_matches: e.target.checked })} /> Nur Treffer zur Whitelist übernehmen</label><div className="wide"><button onClick={runApifyMonitor} disabled={busy || channels.length === 0}>Kanäle automatisch prüfen</button></div></div><p className="muted small">Discovery-Test: Haken rausnehmen. Routine-Report: Haken aktivieren.</p></Section>

      <Section title="Instagram-Link analysieren" kicker="Einzel-Link-Fallback"><form className="form-grid" onSubmit={analyzeLink}><label className="wide">Instagram-Post- oder Reel-Link<input value={quickForm.post_url} onChange={e => setQuickForm({ ...quickForm, post_url: e.target.value })} placeholder="https://www.instagram.com/p/... oder /reel/..." required /></label><label>Channel optional<select value={quickForm.channel_id} onChange={e => setQuickForm({ ...quickForm, channel_id: e.target.value })}><option value="">Automatisch / Auto Import</option>{sortedChannels.map(channel => <option key={channel.id} value={channel.id}>{channel.market} · {channel.name}</option>)}</select></label><label>Titel / Franchise optional<select value={quickForm.title_id} onChange={e => setQuickForm({ ...quickForm, title_id: e.target.value })}><option value="">Automatisch über Text matchen</option>{sortedTitles.map(title => <option key={title.id} value={title.id}>{title.title_original}</option>)}</select></label><label>Vermuteter Asset-Typ optional<select value={quickForm.asset_type_hint} onChange={e => setQuickForm({ ...quickForm, asset_type_hint: e.target.value })}>{ASSET_TYPES.map(type => <option key={type} value={type}>{type}</option>)}</select></label><label>Hinweistext optional<input value={quickForm.caption_hint} onChange={e => setQuickForm({ ...quickForm, caption_hint: e.target.value })} placeholder="z. B. Titel, Claim oder kurze Notiz" /></label><div className="wide"><button type="submit" disabled={busy || !quickForm.post_url}>Instagram-Link analysieren</button></div></form></Section>

      <Section title="Manueller Treffer-Import" kicker="Fallback für Sonderfälle"><form className="form-grid" onSubmit={importPost}><label>Channel<select value={form.channel_id} onChange={e => setForm({ ...form, channel_id: e.target.value })} required><option value="">Bitte wählen</option>{sortedChannels.map(channel => <option key={channel.id} value={channel.id}>{channel.market} · {channel.name}</option>)}</select></label><label>Titel / Franchise<select value={form.title_id} onChange={e => setForm({ ...form, title_id: e.target.value })}><option value="">Automatisch über Caption matchen</option>{sortedTitles.map(title => <option key={title.id} value={title.id}>{title.title_original}</option>)}</select></label><label className="wide">Instagram-Post-Link<input value={form.post_url} onChange={e => setForm({ ...form, post_url: e.target.value })} placeholder="https://www.instagram.com/p/..." required /></label><label>Asset-Typ<select value={form.asset_type} onChange={e => setForm({ ...form, asset_type: e.target.value })}>{ASSET_TYPES.map(type => <option key={type} value={type}>{type}</option>)}</select></label><label>Media Type<input value={form.media_type} onChange={e => setForm({ ...form, media_type: e.target.value })} placeholder="reel / post / story" /></label><label className="wide">Screenshot-URL optional<input value={form.screenshot_url} onChange={e => setForm({ ...form, screenshot_url: e.target.value })} placeholder="Interner Screenshot-Link oder leer lassen" /></label><label className="wide">Caption<textarea value={form.caption} onChange={e => setForm({ ...form, caption: e.target.value })} placeholder="Caption oder kurzer Text aus dem Post" rows="4" /></label><label className="wide">Sichtbarer Text / OCR optional<textarea value={form.ocr_text} onChange={e => setForm({ ...form, ocr_text: e.target.value })} placeholder="Sichtbarer Titel, Claim, CTA etc." rows="3" /></label><div className="wide"><button type="submit" disabled={busy || !form.channel_id || !form.post_url}>Treffer importieren</button></div></form></Section>

      <Section title="Asset Review" kicker={`${visibleAssets.length} von ${assets.length} sichtbar`} className="review-section">
        <div className="filterbar"><select value={filters.status} onChange={e => setFilters({ ...filters, status: e.target.value })}>{STATUS_OPTIONS.map(status => <option key={status} value={status}>{status === 'all' ? 'Alle Status' : status}</option>)}</select><select value={filters.market} onChange={e => setFilters({ ...filters, market: e.target.value })}><option value="all">Alle Märkte</option><option value="DE">DE</option><option value="US">US</option><option value="INT">INT</option><option value="UNKNOWN">UNKNOWN</option></select><select value={filters.assetType} onChange={e => setFilters({ ...filters, assetType: e.target.value })}><option value="all">Alle Asset-Typen</option>{ASSET_TYPES.map(type => <option key={type} value={type}>{type}</option>)}</select><select value={filters.discovery} onChange={e => setFilters({ ...filters, discovery: e.target.value })}><option value="all">Whitelist + Discovery</option><option value="discovery">Nur Discovery</option><option value="whitelist">Nur Whitelist</option></select><input value={filters.query} onChange={e => setFilters({ ...filters, query: e.target.value })} placeholder="Suche nach Titel, Channel, Pattern …" /></div>
        {assets.length === 0 && <p>Noch keine Assets. Erst Kanalliste importieren und Kanäle automatisch prüfen.</p>}
        {visibleAssets.map(asset => <AssetCard key={asset.id} asset={asset} busy={busy} onReview={reviewAsset} />)}
      </Section>

      <Section title="Weekly Report">{report && <div><p>Status: {report.status}</p><p>{report.executive_summary_de}</p><details><summary>HTML anzeigen</summary><iframe title="report" srcDoc={report.html_content || ''} /></details></div>}</Section>
    </main>
  );
}

createRoot(document.getElementById('root')).render(<App />);
