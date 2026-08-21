import React from 'react';
import { formatCronRunStatus, formatDate, formatDateTime } from '../format';
import { Section } from '../components/Section';

export function SourcesPanel({
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
  onCandidateAutopilot,
  onCandidateLlmAssist,
  onFullSync,
  cronBusy,
  cronMessage,
  lastCronRun,
}) {
  return (
    <>
      {/* UX-Audit Befund 2 (2026-07-14): Copy sagte "laufende Woche", der
          Lauf verarbeitet aber seit dem Incident-Fix 2026-07-13
          (target_week='completed', siehe client.js) die zuletzt
          ABGESCHLOSSENE Woche — Text an das echte Systemverhalten
          angeglichen. */}
      <Section title="Komplett aktualisieren" kicker="Voller Lauf für die zuletzt abgeschlossene Woche">
        <p className="muted small">
          Löst den vollen Lauf aus: Posts scrapen, Bilder auswerten, Titel
          synchronisieren, Treffer neu zuordnen sowie Briefs und Roundups für die
          <strong> zuletzt abgeschlossene Woche</strong> neu erzeugen (überschreibt
          bestehende). Läuft im Hintergrund und dauert einige Minuten.
        </p>
        {/* Read-only Status des letzten Laufs (Diagnose-Folge 2026-07-06) —
            unabhaengig vom Trigger-Button, kein Seiteneffekt beim Ansehen. */}
        <div className="status-pills">
          <span className="pill">Letzter Lauf: {formatDateTime(lastCronRun?.started_at)}</span>
          <span className={`pill ${lastCronRun?.status === 'completed' ? 'good' : lastCronRun?.status && lastCronRun.status !== 'running' ? 'warn' : ''}`}>
            Status: {formatCronRunStatus(lastCronRun?.status)}
          </span>
          <span className="pill">
            Dauer: {lastCronRun?.duration_seconds != null ? `${Math.round(lastCronRun.duration_seconds / 60)} Min.` : '—'}
          </span>
          {lastCronRun?.error_message && (
            <span className="pill warn">Fehler: {lastCronRun.error_message}</span>
          )}
        </div>
        <div className="section-actions">
          <button className="primary" onClick={onFullSync} disabled={busy || cronBusy}>
            {cronBusy ? 'Läuft … (Scrape → Briefs, dauert einige Minuten)' : 'Jetzt komplett aktualisieren'}
          </button>
        </div>
        {cronMessage && <p className="muted small cron-status">{cronMessage}</p>}
      </Section>

      <Section title="Quellen prüfen" kicker="Kanäle und Monitoring" helpKey="adminSources">
        <p className="muted small source-intro">Kanal hier hinzufügen oder pflegen — danach in „Treffer prüfen“ zuordnen.</p>
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
          {/* Kandidaten-Autopilot (2026-07-20): bestätigt offene Titel-
              Vorschläge mit eindeutigem Exakt-Treffer automatisch und
              schließt alte schwache Vorschläge — läuft auch wöchentlich
              im Cron; der Button ist für den Backlog-Abbau on demand. */}
          <button className="secondary" onClick={onCandidateAutopilot} disabled={busy}>Offene Vorschläge automatisch bestätigen</button>
          {/* Kandidaten-LLM-Assist (21.08.2026): der Rest OHNE Exakt-
              Treffer („beware" statt „Beware Boiúna") — KI liest die
              Caption und ordnet nur sichere Fälle zu, 12 je Klick. */}
          <button className="secondary" onClick={onCandidateLlmAssist} disabled={busy}>Rest-Vorschläge mit KI prüfen</button>
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
