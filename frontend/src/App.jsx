import React, { useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { endpoints } from './api/client';
import './styles.css';

const ASSET_TYPES = ['Trailer', 'Teaser', 'Poster', 'Story', 'Kinetic', 'Character Card', 'Review Quote', 'CTA Post', 'Unknown'];

function Section({ title, children }) {
  return <section className="card"><h2>{title}</h2>{children}</section>;
}

function App() {
  const [health, setHealth] = useState(null);
  const [channels, setChannels] = useState([]);
  const [titles, setTitles] = useState([]);
  const [assets, setAssets] = useState([]);
  const [report, setReport] = useState(null);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [form, setForm] = useState({
    channel_id: '',
    title_id: '',
    post_url: '',
    caption: '',
    media_type: 'reel',
    asset_type: 'Unknown',
    screenshot_url: '',
    ocr_text: '',
  });

  const sortedChannels = useMemo(() => [...channels].sort((a, b) => `${a.market}-${a.name}`.localeCompare(`${b.market}-${b.name}`)), [channels]);
  const sortedTitles = useMemo(() => [...titles].sort((a, b) => a.title_original.localeCompare(b.title_original)), [titles]);

  async function load() {
    setError('');
    try {
      const [h, c, t, a] = await Promise.all([
        endpoints.health(), endpoints.channels(), endpoints.titles(), endpoints.assets()
      ]);
      setHealth(h); setChannels(c); setTitles(t); setAssets(a);
      try { setReport(await endpoints.latestReport()); } catch (_) { setReport(null); }
    } catch (err) {
      setError(err.message);
    }
  }

  useEffect(() => { load(); }, []);

  async function seed() {
    setError(''); setMessage('');
    try {
      await endpoints.seedChannels();
      await endpoints.seedTitles();
      await load();
      setMessage('MVP-Daten wurden angelegt oder waren bereits vorhanden.');
    } catch (err) { setError(err.message); }
  }

  async function importPost(event) {
    event.preventDefault();
    setError(''); setMessage('');
    try {
      const payload = {
        ...form,
        title_id: form.title_id || null,
        screenshot_url: form.screenshot_url || null,
        ocr_text: form.ocr_text || null,
        caption: form.caption || null,
        media_type: form.media_type || null,
      };
      await endpoints.manualImport(payload);
      setForm({ ...form, post_url: '', caption: '', screenshot_url: '', ocr_text: '', asset_type: 'Unknown' });
      await load();
      setMessage('Treffer importiert. Unten im Asset Review prüfen und freigeben.');
    } catch (err) { setError(err.message); }
  }

  async function reviewAsset(asset, status) {
    setError(''); setMessage('');
    try {
      await endpoints.reviewAsset(asset.id, {
        review_status: status,
        include_in_report: status === 'approved' || status === 'highlight',
        is_highlight: status === 'highlight',
        curator_note: asset.curator_note || ''
      });
      await load();
      setMessage(`Asset wurde als ${status} gespeichert.`);
    } catch (err) { setError(err.message); }
  }

  async function generateReport() {
    setError(''); setMessage('');
    try {
      const today = new Date();
      const end = today.toISOString().slice(0, 10);
      const startDate = new Date(today.getTime() - 7 * 86400000).toISOString().slice(0, 10);
      const created = await endpoints.generateReport({ week_start: startDate, week_end: end, include_only_reviewed: true });
      setReport(created);
      setMessage('Report-Entwurf wurde erzeugt.');
    } catch (err) { setError(err.message); }
  }

  return (
    <main>
      <header className="hero">
        <div>
          <p className="eyebrow">Online-first Monorepo · Railway + Netlify</p>
          <h1>Creative Radar v1</h1>
          <p>Interner MVP für kuratiertes Creative Monitoring.</p>
        </div>
        <div className="hero-actions">
          <button onClick={seed}>MVP-Daten anlegen</button>
          <button onClick={load}>Aktualisieren</button>
        </div>
      </header>

      {error && <div className="error">{error}</div>}
      {message && <div className="success">{message}</div>}

      <div className="grid">
        <Section title="Systemstatus">
          <p>Status: <strong>{health?.status || 'nicht verbunden'}</strong></p>
          <p>Channels: {channels.length}</p>
          <p>Whitelist-Titel: {titles.length}</p>
          <p>Assets: {assets.length}</p>
        </Section>

        <Section title="Workflow v1">
          <ol>
            <li>MVP-Daten anlegen</li>
            <li>Post-Link manuell importieren</li>
            <li>Asset prüfen: Approve / Highlight / Reject</li>
            <li>Report-Entwurf erzeugen</li>
          </ol>
        </Section>
      </div>

      <Section title="Manueller Treffer-Import">
        <form className="form-grid" onSubmit={importPost}>
          <label>
            Channel
            <select value={form.channel_id} onChange={e => setForm({ ...form, channel_id: e.target.value })} required>
              <option value="">Bitte wählen</option>
              {sortedChannels.map(channel => <option key={channel.id} value={channel.id}>{channel.market} · {channel.name}</option>)}
            </select>
          </label>
          <label>
            Titel / Franchise
            <select value={form.title_id} onChange={e => setForm({ ...form, title_id: e.target.value })}>
              <option value="">Automatisch über Caption matchen</option>
              {sortedTitles.map(title => <option key={title.id} value={title.id}>{title.title_original}</option>)}
            </select>
          </label>
          <label className="wide">
            Instagram-Post-Link
            <input value={form.post_url} onChange={e => setForm({ ...form, post_url: e.target.value })} placeholder="https://www.instagram.com/p/..." required />
          </label>
          <label>
            Asset-Typ
            <select value={form.asset_type} onChange={e => setForm({ ...form, asset_type: e.target.value })}>
              {ASSET_TYPES.map(type => <option key={type} value={type}>{type}</option>)}
            </select>
          </label>
          <label>
            Media Type
            <input value={form.media_type} onChange={e => setForm({ ...form, media_type: e.target.value })} placeholder="reel / post / story" />
          </label>
          <label className="wide">
            Screenshot-URL optional
            <input value={form.screenshot_url} onChange={e => setForm({ ...form, screenshot_url: e.target.value })} placeholder="Interner Screenshot-Link oder leer lassen" />
          </label>
          <label className="wide">
            Caption
            <textarea value={form.caption} onChange={e => setForm({ ...form, caption: e.target.value })} placeholder="Caption oder kurzer Text aus dem Post" rows="4" />
          </label>
          <label className="wide">
            Sichtbarer Text / OCR optional
            <textarea value={form.ocr_text} onChange={e => setForm({ ...form, ocr_text: e.target.value })} placeholder="Sichtbarer Titel, Claim, CTA etc." rows="3" />
          </label>
          <div className="wide">
            <button type="submit" disabled={!form.channel_id || !form.post_url}>Treffer importieren</button>
          </div>
        </form>
      </Section>

      <Section title="Asset Review">
        {assets.length === 0 && <p>Noch keine Assets. Erst MVP-Daten anlegen und einen Post importieren.</p>}
        {assets.map(asset => (
          <article key={asset.id} className="asset">
            {asset.screenshot_url && <img src={asset.screenshot_url} alt="Asset Screenshot" />}
            <div className="asset-body">
              <strong>{asset.asset_type}</strong> · Status: <span className="badge">{asset.review_status}</span>
              <p>{asset.ai_summary_de || 'Keine KI-Zusammenfassung vorhanden.'}</p>
              <p className="muted">{asset.ai_trend_notes}</p>
              {asset.ocr_text && <p><strong>OCR:</strong> {asset.ocr_text}</p>}
            </div>
            <div className="actions">
              <button onClick={() => reviewAsset(asset, 'approved')}>Approve</button>
              <button onClick={() => reviewAsset(asset, 'highlight')}>Highlight</button>
              <button onClick={() => reviewAsset(asset, 'rejected')}>Reject</button>
            </div>
          </article>
        ))}
      </Section>

      <Section title="Weekly Report">
        <button onClick={generateReport}>Report-Entwurf erzeugen</button>
        {report && (
          <div>
            <p>Status: {report.status}</p>
            <p>{report.executive_summary_de}</p>
            <details>
              <summary>HTML anzeigen</summary>
              <iframe title="report" srcDoc={report.html_content || ''} />
            </details>
          </div>
        )}
      </Section>
    </main>
  );
}

createRoot(document.getElementById('root')).render(<App />);
