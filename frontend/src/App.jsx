import React, { useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { endpoints } from './api/client';
import './styles.css';

const STATUS_OPTIONS = ['all', 'new', 'needs_review', 'approved', 'highlight', 'rejected'];
const NAV_ITEMS = ['Heute', 'Treffer prüfen', 'Vergleich', 'Weekly Report', 'Quellen'];

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

function ImagePreview({ src }) {
  const [failed, setFailed] = useState(false);
  if (!src || failed) return <div className="preview-placeholder">Kein Preview</div>;
  return <img src={src} alt="Creative Preview" onError={() => setFailed(true)} />;
}

function MetricStrip({ asset }) {
  const metrics = [
    ['Views', asset.visible_views],
    ['Likes', asset.visible_likes],
    ['Shares', asset.visible_shares],
    ['Comments', asset.visible_comments],
  ];
  return (
    <div className="metric-strip">
      {metrics.map(([label, value]) => (
        <span key={label}><b>{formatNumber(value)}</b><small>{label}</small></span>
      ))}
    </div>
  );
}

function TodoCard({ openReview, missingTitles, reportCandidates, approved, highlights, onGoReview, onGoReport }) {
  const reportGap = [];
  if (approved === 0) reportGap.push('mindestens 1 freigegebener Treffer');
  if (highlights === 0) reportGap.push('mindestens 1 Highlight');
  if (missingTitles > 0) reportGap.push('Filmtitel-Zuordnung bei offenen Treffern');

  return (
    <Section title="Heute zu tun" kicker="Geführter Workflow" className="todo-card">
      <div className="todo-grid">
        <div className="todo-item"><strong>{openReview}</strong><span>neue Treffer warten auf Prüfung</span></div>
        <div className="todo-item"><strong>{missingTitles}</strong><span>Treffer brauchen Filmtitel-Zuordnung</span></div>
        <div className="todo-item"><strong>{reportCandidates}</strong><span>Treffer sind für den Report geeignet</span></div>
      </div>
      <div className="todo-actions">
        <button className="primary" onClick={onGoReview}>Jetzt Treffer prüfen</button>
        <button className="secondary" onClick={onGoReport}>Weekly Report öffnen</button>
      </div>
      <p className="muted small">{reportGap.length ? `Für einen belastbaren Report fehlen noch: ${reportGap.join(', ')}.` : 'Report kann jetzt sinnvoll erstellt oder aktualisiert werden.'}</p>
    </Section>
  );
}

function ImportantFinds({ assets }) {
  const weekly = [...assets]
    .filter((asset) => asset.review_status === 'highlight' || asset.is_highlight || asset.review_status === 'approved')
    .sort((a, b) => new Date(b.published_at || b.created_at || 0) - new Date(a.published_at || a.created_at || 0))
    .slice(0, 6);

  return (
    <Section title="Wichtige Funde dieser Woche" kicker="Kuratiert">
      {weekly.length === 0 ? <p className="muted">Noch keine kuratierten Funde vorhanden.</p> : (
        <div className="find-grid">
          {weekly.map((asset) => (
            <article key={asset.id} className="find-card">
              <ImagePreview src={asset.thumbnail_url || asset.screenshot_url} />
              <div>
                <p className="find-title">{asset.title_name || asset.placement_title_text || 'Neuer Treffer – Filmtitel noch offen'}</p>
                <p className="muted small">{asset.channel_name || 'Unbekannter Kanal'} · {asset.channel_market || 'UNKNOWN'} · {formatDate(asset.published_at || asset.created_at)}</p>
                <p className="small">{clip(asset.ai_summary_de || asset.caption || 'Noch keine Zusammenfassung verfügbar.', 120)}</p>
              </div>
            </article>
          ))}
        </div>
      )}
    </Section>
  );
}

function ReportStatus({ approved, highlights, openReview, missingTitles }) {
  const ready = approved > 0 && highlights > 0;
  return (
    <Section title="Report-Status" kicker="Was noch fehlt">
      <div className="status-pills">
        <span className={`pill ${ready ? 'good' : 'warn'}`}>{ready ? 'Bereit für Management-Report' : 'Noch nicht vollständig'}</span>
        <span className="pill">{approved} freigegeben</span>
        <span className="pill">{highlights} Highlights</span>
        <span className="pill">{openReview} noch zu prüfen</span>
        <span className="pill">{missingTitles} mit offener Filmtitel-Zuordnung</span>
      </div>
    </Section>
  );
}

function AssetCard({ asset, titles, busy, onReview, onAnalyzeVisual, onAssignTitle }) {
  const preview = asset.thumbnail_url || asset.screenshot_url;
  const platform = asset.platform || asset.channel_platform || asset.media_type || 'instagram';
  const hasTitle = Boolean(asset.title_name || asset.placement_title_text);
  const displayStatus = {
    new: 'Noch zu prüfen',
    needs_review: 'Noch zu prüfen',
    approved: 'Freigegeben',
    highlight: 'Als Highlight markiert',
    rejected: 'Nicht relevant',
  }[asset.review_status] || asset.review_status;

  return (
    <article className="asset-card">
      <div className="asset-preview"><ImagePreview src={preview} /></div>
      <div className="asset-content">
        <div className="asset-topline">
          <span className="asset-title">{asset.title_name || asset.placement_title_text || 'Neuer Treffer – Filmtitel noch offen'}</span>
          <span className="pill">{platform}</span>
          <span className="pill">{displayStatus}</span>
          {asset.has_title_placement && <span className="pill">Titel-/Claim-Platzierung</span>}
          {asset.has_kinetic && <span className="pill">Bewegter Text</span>}
        </div>
        <p className="asset-meta">{asset.channel_name || 'Unbekannter Kanal'} · {asset.channel_market || 'UNKNOWN'} · {formatDate(asset.published_at || asset.detected_at || asset.created_at)}</p>
        <MetricStrip asset={asset} />
        <label className="title-select">
          Filmtitel-Zuordnung
          <select value={asset.title_id || ''} onChange={(e) => onAssignTitle(asset, e.target.value)} disabled={busy}>
            <option value="">Noch nicht zugeordnet</option>
            {titles.map((title) => <option key={title.id} value={title.id}>{title.title_original}</option>)}
          </select>
        </label>
        {!hasTitle && <p className="missing-hint">Hinweis: Dieser Treffer braucht noch eine Filmtitel-Zuordnung.</p>}
        <p className="asset-summary">{clip(asset.ai_summary_de || 'Noch keine KI-Zusammenfassung vorhanden.', 300)}</p>
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
        <button onClick={() => onReview(asset, 'approved')} disabled={busy}>Freigeben</button>
        <button onClick={() => onReview(asset, 'highlight')} disabled={busy}>Als Highlight markieren</button>
        <button onClick={() => onReview(asset, 'needs_review')} disabled={busy}>Später prüfen</button>
        <button onClick={() => onReview(asset, 'rejected')} disabled={busy}>Nicht relevant</button>
        <button className="secondary" onClick={() => onAnalyzeVisual(asset)} disabled={busy}>Visual prüfen</button>
      </div>
    </article>
  );
}

function HomePanel({ assets, openReview, missingTitles, reportCandidates, approved, highlights, setActiveTab }) {
  return (
    <>
      <TodoCard
        openReview={openReview}
        missingTitles={missingTitles}
        reportCandidates={reportCandidates}
        approved={approved.length}
        highlights={highlights.length}
        onGoReview={() => setActiveTab('Treffer prüfen')}
        onGoReport={() => setActiveTab('Weekly Report')}
      />
      <ImportantFinds assets={assets} />
      <ReportStatus approved={approved.length} highlights={highlights.length} openReview={openReview} missingTitles={missingTitles} />
    </>
  );
}

function ReviewPanel({ assets, titles, visibleAssets, filters, setFilters, busy, onReview, onAnalyzeVisual, onAssignTitle }) {
  return (
    <Section title="Treffer prüfen" kicker={`${visibleAssets.length} von ${assets.length} Treffern sichtbar`}>
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
        />
      ))}
    </Section>
  );
}

function ComparisonPanel({ assets }) {
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

function ReportsPanel({ report, approved, highlights, openReview, busy, onGenerateReport }) {
  return (
    <Section title="Weekly Report" kicker="Ergebnisraum">
      <div className="report-head">
        <p><strong>Zeitraum:</strong> letzte 7 Tage</p>
        <p><strong>Status:</strong> {report?.status || 'Noch nicht erstellt'}</p>
      </div>
      <div className="status-pills">
        <span className="pill">Management Summary</span>
        <span className="pill">Trend Snapshot</span>
        <span className="pill">Highlight Creatives</span>
        <span className="pill">DE/US Vergleich</span>
        <span className="pill">Appendix</span>
      </div>
      <p className="muted small">Freigegeben: {approved.length} · Highlights: {highlights.length} · Noch zu prüfen: {openReview}</p>
      <div className="section-actions">
        <button className="primary" onClick={onGenerateReport} disabled={busy || approved.length === 0}>Report aktualisieren</button>
      </div>
      {report ? (
        <details>
          <summary>Report-Vorschau öffnen</summary>
          <iframe title="report" srcDoc={report.html_content || ''} />
        </details>
      ) : <p className="muted">Noch kein Weekly Report erzeugt.</p>}
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
}) {
  return (
    <>
      <Section title="Quellen prüfen" kicker="Kanäle und Monitoring">
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
          <div className="source-card">
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
  const [activeTab, setActiveTab] = useState('Heute');
  const [channelFile, setChannelFile] = useState(null);
  const [monitorForm, setMonitorForm] = useState({ max_channels: 5, results_limit_per_channel: 5, only_whitelist_matches: true });
  const [tiktokForm, setTiktokForm] = useState({ username: 'warnerbros', max_channels: 1, results_limit_per_channel: 5, only_whitelist_matches: false });
  const [filters, setFilters] = useState({ status: 'all', platform: 'all', market: 'all', query: '' });

  const sortedTitles = useMemo(() => [...titles].sort((a, b) => a.title_original.localeCompare(b.title_original)), [titles]);
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
    const [h, c, t, a] = await Promise.all([endpoints.health(), endpoints.channels(), endpoints.titles(), endpoints.assets()]);
    setHealth(h); setChannels(c); setTitles(t); setAssets(a);
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

  async function reviewAsset(asset, status) {
    await run(async () => {
      await endpoints.reviewAsset(asset.id, {
        review_status: status,
        include_in_report: status === 'approved' || status === 'highlight',
        is_highlight: status === 'highlight',
        curator_note: asset.curator_note || '',
      });
      await load();
      setMessage('Trefferstatus wurde aktualisiert.');
    });
  }

  async function assignTitle(asset, titleId) {
    await run(async () => {
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

  async function generateReport() {
    await run(async () => {
      const today = new Date();
      const end = today.toISOString().slice(0, 10);
      const startDate = new Date(today.getTime() - 7 * 86400000).toISOString().slice(0, 10);
      const created = await endpoints.generateReport({ week_start: startDate, week_end: end, include_only_reviewed: true });
      setReport(created);
      setMessage('Weekly Report wurde aktualisiert.');
    });
  }

  return (
    <main>
      <header className="hero">
        <div>
          <p className="eyebrow">Creative Intelligence Workspace</p>
          <h1>Creative Radar</h1>
          <p>Ausgewählte Kanäle prüfen, relevante Creatives erkennen, DE/US-Muster vergleichen und einen Weekly Report erstellen.</p>
        </div>
        <div className="hero-actions">
          <button className="primary" onClick={() => setActiveTab('Treffer prüfen')} disabled={busy}>Neue Treffer prüfen</button>
          <button className="secondary" onClick={() => setActiveTab('Weekly Report')} disabled={busy}>Weekly Report öffnen</button>
        </div>
      </header>

      {error && <div className="error">{error}</div>}
      {message && <div className="success">{message}</div>}
      {busy && <div className="info">Arbeite gerade …</div>}

      <nav className="tabs">
        {NAV_ITEMS.map((tab) => (
          <button key={tab} className={activeTab === tab ? 'active' : ''} onClick={() => setActiveTab(tab)}>{tab}</button>
        ))}
      </nav>

      {activeTab === 'Heute' && (
        <HomePanel
          assets={assets}
          openReview={openReview}
          missingTitles={missingTitles}
          reportCandidates={reportCandidates}
          approved={approved}
          highlights={highlights}
          setActiveTab={setActiveTab}
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
        />
      )}
      {activeTab === 'Vergleich' && <ComparisonPanel assets={assets} />}
      {activeTab === 'Weekly Report' && (
        <ReportsPanel
          report={report}
          approved={approved}
          highlights={highlights}
          openReview={openReview}
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
