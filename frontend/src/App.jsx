import React, { useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { endpoints } from './api/client';
import './styles.css';

const ASSET_TYPES = ['Trailer', 'Teaser', 'Poster', 'Story', 'Kinetic', 'Character Card', 'Review Quote', 'CTA Post', 'Unknown'];

function Section({ title, kicker, children }) {
  return <section className="card">{kicker && <p className="section-kicker">{kicker}</p>}<h2>{title}</h2>{children}</section>;
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
  const [quickForm, setQuickForm] = useState({ post_url: '', channel_id: '', title_id: '', caption_hint: '', asset_type_hint: 'Unknown' });
  const [form, setForm] = useState({ channel_id: '', title_id: '', post_url: '', caption: '', media_type: 'reel', asset_type: 'Unknown', screenshot_url: '', ocr_text: '' });

  const sortedChannels = useMemo(() => [...channels].sort((a, b) => `${a.market}-${a.name}`.localeCompare(`${b.market}-${b.name}`)), [channels]);
  const sortedTitles = useMemo(() => [...titles].sort((a, b) => a.title_original.localeCompare(b.title_original)), [titles]);
  const highlights = assets.filter((asset) => asset.is_highlight || asset.review_status === 'highlight');
  const approved = assets.filter((asset) => asset.include_in_report || asset.review_status === 'approved' || asset.review_status === 'highlight');

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
        <Section title="Systemstatus"><p>Status: <strong>{health?.status || 'nicht verbunden'}</strong></p><p>Channels: {channels.length}</p><p>Whitelist-Titel: {titles.length}</p><p>Assets: {assets.length}</p><p>Freigegeben: {approved.length}</p><p>Highlights: {highlights.length}</p></Section>
        <Section title="Empfohlener Ablauf"><ol><li>Excel-Kanalliste importieren.</li><li>„Kanäle automatisch prüfen“ starten.</li><li>Assets als Approve, Highlight oder Reject markieren.</li><li>Report-Entwurf erzeugen.</li></ol></Section>
      </div>

      <Section title="Kanalliste importieren" kicker="Einmaliger Setup-Schritt">
        <form className="form-grid" onSubmit={importChannelFile}><label className="wide">Excel-Datei mit FILMVERLEIH / INSTAGRAM<input type="file" accept=".xlsx,.xlsm" onChange={(event) => setChannelFile(event.target.files?.[0] || null)} /></label><div className="wide"><button type="submit" disabled={busy || !channelFile}>Excel-Kanalliste importieren</button></div></form>
        <p className="muted small">Nutze hier „Filmverleih Instagram.xlsx“. Die URLs werden automatisch als Channels angelegt oder aktualisiert.</p>
      </Section>

      <Section title="Kanäle automatisch prüfen" kicker="Apify Public Monitor">
        <div className="form-grid">
          <label>Max. Channels pro Lauf<input type="number" min="1" max="25" value={monitorForm.max_channels} onChange={e => setMonitorForm({ ...monitorForm, max_channels: e.target.value })} /></label>
          <label>Posts/Reels pro Channel<input type="number" min="1" max="20" value={monitorForm.results_limit_per_channel} onChange={e => setMonitorForm({ ...monitorForm, results_limit_per_channel: e.target.value })} /></label>
          <label className="wide checkbox-row"><input type="checkbox" checked={monitorForm.only_whitelist_matches} onChange={e => setMonitorForm({ ...monitorForm, only_whitelist_matches: e.target.checked })} /> Nur Treffer zur Whitelist übernehmen</label>
          <div className="wide"><button onClick={runApifyMonitor} disabled={busy || channels.length === 0}>Kanäle automatisch prüfen</button></div>
        </div>
        <p className="muted small">Start klein: 5 Channels × 5 Posts. Das spart Apify-Laufzeit und zeigt schnell, ob die Whitelist gut greift.</p>
      </Section>

      <Section title="Instagram-Link analysieren" kicker="Einzel-Link-Fallback">
        <form className="form-grid" onSubmit={analyzeLink}>
          <label className="wide">Instagram-Post- oder Reel-Link<input value={quickForm.post_url} onChange={e => setQuickForm({ ...quickForm, post_url: e.target.value })} placeholder="https://www.instagram.com/p/... oder /reel/..." required /></label>
          <label>Channel optional<select value={quickForm.channel_id} onChange={e => setQuickForm({ ...quickForm, channel_id: e.target.value })}><option value="">Automatisch / Auto Import</option>{sortedChannels.map(channel => <option key={channel.id} value={channel.id}>{channel.market} · {channel.name}</option>)}</select></label>
          <label>Titel / Franchise optional<select value={quickForm.title_id} onChange={e => setQuickForm({ ...quickForm, title_id: e.target.value })}><option value="">Automatisch über Text matchen</option>{sortedTitles.map(title => <option key={title.id} value={title.id}>{title.title_original}</option>)}</select></label>
          <label>Vermuteter Asset-Typ optional<select value={quickForm.asset_type_hint} onChange={e => setQuickForm({ ...quickForm, asset_type_hint: e.target.value })}>{ASSET_TYPES.map(type => <option key={type} value={type}>{type}</option>)}</select></label>
          <label>Hinweistext optional<input value={quickForm.caption_hint} onChange={e => setQuickForm({ ...quickForm, caption_hint: e.target.value })} placeholder="z. B. Titel, Claim oder kurze Notiz" /></label>
          <div className="wide"><button type="submit" disabled={busy || !quickForm.post_url}>Instagram-Link analysieren</button></div>
        </form>
      </Section>

      <Section title="Manueller Treffer-Import" kicker="Fallback für Sonderfälle">
        <form className="form-grid" onSubmit={importPost}>
          <label>Channel<select value={form.channel_id} onChange={e => setForm({ ...form, channel_id: e.target.value })} required><option value="">Bitte wählen</option>{sortedChannels.map(channel => <option key={channel.id} value={channel.id}>{channel.market} · {channel.name}</option>)}</select></label>
          <label>Titel / Franchise<select value={form.title_id} onChange={e => setForm({ ...form, title_id: e.target.value })}><option value="">Automatisch über Caption matchen</option>{sortedTitles.map(title => <option key={title.id} value={title.id}>{title.title_original}</option>)}</select></label>
          <label className="wide">Instagram-Post-Link<input value={form.post_url} onChange={e => setForm({ ...form, post_url: e.target.value })} placeholder="https://www.instagram.com/p/..." required /></label>
          <label>Asset-Typ<select value={form.asset_type} onChange={e => setForm({ ...form, asset_type: e.target.value })}>{ASSET_TYPES.map(type => <option key={type} value={type}>{type}</option>)}</select></label>
          <label>Media Type<input value={form.media_type} onChange={e => setForm({ ...form, media_type: e.target.value })} placeholder="reel / post / story" /></label>
          <label className="wide">Screenshot-URL optional<input value={form.screenshot_url} onChange={e => setForm({ ...form, screenshot_url: e.target.value })} placeholder="Interner Screenshot-Link oder leer lassen" /></label>
          <label className="wide">Caption<textarea value={form.caption} onChange={e => setForm({ ...form, caption: e.target.value })} placeholder="Caption oder kurzer Text aus dem Post" rows="4" /></label>
          <label className="wide">Sichtbarer Text / OCR optional<textarea value={form.ocr_text} onChange={e => setForm({ ...form, ocr_text: e.target.value })} placeholder="Sichtbarer Titel, Claim, CTA etc." rows="3" /></label>
          <div className="wide"><button type="submit" disabled={busy || !form.channel_id || !form.post_url}>Treffer importieren</button></div>
        </form>
      </Section>

      <Section title="Asset Review">
        {assets.length === 0 && <p>Noch keine Assets. Erst Kanalliste importieren und Kanäle automatisch prüfen.</p>}
        {assets.map(asset => <article key={asset.id} className="asset">{asset.screenshot_url && <img src={asset.screenshot_url} alt="Asset Screenshot" />}<div className="asset-body"><strong>{asset.asset_type}</strong> · Status: <span className="badge">{asset.review_status}</span><p>{asset.ai_summary_de || 'Keine KI-Zusammenfassung vorhanden.'}</p><p className="muted">{asset.ai_trend_notes}</p>{asset.ocr_text && <p><strong>OCR:</strong> {asset.ocr_text}</p>}</div><div className="actions"><button onClick={() => reviewAsset(asset, 'approved')} disabled={busy}>Approve</button><button onClick={() => reviewAsset(asset, 'highlight')} disabled={busy}>Highlight</button><button onClick={() => reviewAsset(asset, 'rejected')} disabled={busy}>Reject</button></div></article>)}
      </Section>

      <Section title="Weekly Report"><button onClick={generateReport} disabled={busy || approved.length === 0}>Report-Entwurf erzeugen</button>{approved.length === 0 && <p className="muted">Erst mindestens ein Asset approven oder highlighten.</p>}{report && <div><p>Status: {report.status}</p><p>{report.executive_summary_de}</p><details><summary>HTML anzeigen</summary><iframe title="report" srcDoc={report.html_content || ''} /></details></div>}</Section>
    </main>
  );
}

createRoot(document.getElementById('root')).render(<App />);
