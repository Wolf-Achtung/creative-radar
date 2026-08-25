import React, { useState } from 'react';
import { apiUrl } from '../api/client';
import { formatCronRunStatus, formatDate, formatDateTime, searchTitles } from '../format';
import { Section } from '../components/Section';
import { DIMENSION_LABEL, WERT_LABEL } from '../PatternsBlock';

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
  onKatalogVorschau = () => {},
  onKatalogAnwenden = () => {},
  katalogVorschau = null,
  onToggleChannelOwn,
  channels,
  titles,
  onToggleTitleOwnProject,
  startBrief = null,
  onProjektStartBrief = () => {},
  features = {},
  onFullSync,
  cronBusy,
  cronMessage,
  lastCronRun,
}) {
  // Wir-Projekte (22.08.2026): bei ~30k Titeln ist eine Checkliste
  // unbenutzbar — dieselbe Umlaut-tolerante Live-Suche wie in der
  // Pruef-Queue, Treffer als Checkboxen.
  const [projektSuche, setProjektSuche] = useState('');
  const projektTreffer = searchTitles(titles || [], projektSuche, 8);
  const markierteProjekte = (titles || []).filter((t) => t.is_own_project);
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
          {/* Katalog-Nachladen (25.08.2026): der Schritt NACH der KI-
              Prüfung. Sie endet regelmäßig mit „bewirbt X (nicht im
              Katalog)" — dieser Knopf legt genau diese Titel an, wenn
              der Post sie wörtlich nennt und TMDb sie eindeutig kennt.
              Feature-Flag-Gate (Arbeitsregel 23.08.2026): erscheint nur,
              wo /api/health -> features es anbietet — Staging zuerst. */}
          {features.katalog_nachladen && (
            <>
              <button className="secondary" onClick={onKatalogVorschau} disabled={busy}>Fehlende Titel prüfen (Vorschau)</button>
              {/* Erst sehen, dann anlegen. Der Knopf bleibt gesperrt,
                  bis eine Vorschau vorliegt, die auch wirklich etwas
                  anzulegen hätte — ein Klick ins Leere soll nicht wie
                  eine Entscheidung aussehen. */}
              <button
                className="secondary"
                onClick={onKatalogAnwenden}
                disabled={busy || !katalogVorschau || !katalogVorschau.angelegt}
                title={katalogVorschau
                  ? undefined
                  : 'Erst die Vorschau ansehen — sie zeigt, welche Titel angelegt würden.'}
              >
                {katalogVorschau && katalogVorschau.angelegt
                  ? `${katalogVorschau.angelegt} Titel wirklich anlegen`
                  : 'Titel wirklich anlegen'}
              </button>
            </>
          )}
        </div>
      </Section>
      {/* Wir-Projekte (22.08.2026): Trailerhaus betreut keine kompletten
          Kunden-Kanäle, sondern liefert pro Filmprojekt — die Wir-Einheit
          fürs Monitoring ist deshalb der TITEL. Das Kanal-Häkchen darunter
          bleibt für den Fall eines wirklich eigenen Kanals bestehen.
          Feature-Flag-Gate (Arbeitsregel 23.08.2026): erscheint nur, wo
          /api/health -> features es anbietet — Staging zuerst. */}
      {features.wir_projekte && (
      <details className="card" open={markierteProjekte.length === 0}>
        <summary>Wir-Projekte markieren ({markierteProjekte.length} markiert)</summary>
        <p className="muted small">
          Kreuze die Filme an, an denen euer Team gearbeitet hat. Das
          Monitoring zählt dann alle Posts zu diesen Titeln als „von uns“ —
          egal auf welchem Kanal sie erschienen sind. Hinweis: Das misst
          eure Filme, nicht das einzelne Asset — fremdes Material zum
          selben Titel zählt mit.
        </p>
        {markierteProjekte.length > 0 && (
          <div style={{ marginBottom: '0.5rem' }}>
            {markierteProjekte.map((title) => (
              <div key={title.id} style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', padding: '0.15rem 0' }}>
                <label style={{ flex: 1 }}>
                  <input
                    type="checkbox"
                    checked
                    onChange={() => onToggleTitleOwnProject(title)}
                  />{' '}
                  {title.title_original}
                  {title.title_local && title.title_local !== title.title_original ? (
                    <span className="muted small"> · {title.title_local}</span>
                  ) : null}
                </label>
                {/* Projekt-Start-Brief (22.08.2026): das Radar VOR der
                    Arbeit — die aktuell ueberperformenden Muster mit
                    Referenz-Posts als Moodboard fuer genau dieses Projekt.
                    Eigenes Flag: kann unabhaengig freigegeben werden. */}
                {features.projekt_start_brief && (
                  <button
                    type="button"
                    className="secondary"
                    onClick={() => onProjektStartBrief(title)}
                  >
                    Start-Brief
                  </button>
                )}
              </div>
            ))}
          </div>
        )}
        {startBrief?.laedt && <p className="muted small">Start-Brief wird berechnet …</p>}
        {startBrief?.fehler && (
          <p className="error">Start-Brief konnte nicht geladen werden: {startBrief.fehler}</p>
        )}
        {startBrief?.daten && (
          <div className="card" style={{ marginTop: '0.5rem' }}>
            <h3>Start-Brief: {startBrief.daten.title.title_original}</h3>
            <p className="muted small">
              {startBrief.daten.genre_standort ? (
                <>
                  Euer Genre <strong>{startBrief.daten.title.genre}</strong> steht aktuell auf{' '}
                  Median-Lift {startBrief.daten.genre_standort.median_lift}x
                  ({startBrief.daten.genre_standort.verdict}
                  {startBrief.daten.genre_standort.breakout_z != null
                    ? `, z=${startBrief.daten.genre_standort.breakout_z}`
                    : ''}).{' '}
                </>
              ) : startBrief.daten.title.genre ? (
                <>Für das Genre „{startBrief.daten.title.genre}“ gibt es im Fenster keine belastbare Zelle. </>
              ) : (
                <>Der Titel trägt noch kein Genre — der TMDb-Sync ergänzt es beim nächsten Lauf. </>
              )}
              Basis: {startBrief.daten.posts_im_fenster} Posts der letzten {startBrief.daten.window_days} Tage.
            </p>
            {startBrief.daten.empfehlungen.length === 0 && (
              <p className="muted">Aktuell gibt es keine MACHEN-Empfehlung im Muster-Bericht.</p>
            )}
            {startBrief.daten.empfehlungen.map((emp) => (
              <div key={`${emp.dimension}:${emp.value}`} style={{ margin: '0.5rem 0' }}>
                <p style={{ margin: 0 }}>
                  <strong>{DIMENSION_LABEL[emp.dimension] || emp.dimension}: {WERT_LABEL[emp.value] || emp.value}</strong>
                  <span className="muted small">
                    {' '}— Median-Lift {emp.median_lift}x
                    {emp.breakout_z != null ? `, z=${emp.breakout_z}` : ''}, n={emp.sample_size}
                  </span>
                </p>
                {emp.beispiele.length > 0 && (
                  <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', marginTop: '0.25rem' }}>
                    {emp.beispiele.map((bsp) => (
                      <a
                        key={bsp.post_url}
                        href={bsp.post_url}
                        target="_blank"
                        rel="noreferrer"
                        className="muted small"
                        style={{ maxWidth: '9rem' }}
                        title={bsp.caption || bsp.post_url}
                      >
                        {bsp.asset_id && (
                          <img
                            src={apiUrl(`/api/thumbnails/${bsp.asset_id}`)}
                            alt=""
                            style={{ width: '100%', borderRadius: '4px', display: 'block' }}
                          />
                        )}
                        {bsp.handle ? `@${bsp.handle}` : 'Post'} · {bsp.lift}x
                      </a>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
        <input
          type="text"
          value={projektSuche}
          onChange={(event) => setProjektSuche(event.target.value)}
          placeholder="Filmtitel tippen — findet auch Umlaute (lugen → Lügen)"
          aria-label="Titelsuche für Wir-Projekte"
        />
        {projektTreffer.length > 0 && (
          <div style={{ maxHeight: '12rem', overflowY: 'auto' }}>
            {projektTreffer.map((title) => (
              <label key={title.id} style={{ display: 'block', padding: '0.15rem 0' }}>
                <input
                  type="checkbox"
                  checked={Boolean(title.is_own_project)}
                  onChange={() => onToggleTitleOwnProject(title)}
                />{' '}
                {title.title_original}
                {title.franchise ? <span className="muted small"> · {title.franchise}</span> : null}
              </label>
            ))}
          </div>
        )}
        {projektSuche.trim().length >= 2 && projektTreffer.length === 0 && (
          <p className="muted small">
            Kein Titel gefunden — fehlt er im Katalog, lässt er sich in der
            Prüf-Queue („Treffer prüfen“) anlegen.
          </p>
        )}
      </details>
      )}
      {/* Wir-Segment Schritt 1 (21.08.2026): eigene Kanäle markieren —
          Grundlage der „empfohlen → gemacht → gewirkt"-Auswertung im
          Monitoring. Eingeklappt, weil das eine einmalige Pflege ist. */}
      <details className="card">
        <summary>Wir-Kanäle markieren ({(channels || []).filter((c) => c.is_own).length} markiert)</summary>
        <p className="muted small">
          Kreuze die Kanäle an, die euer Team selbst betreut. Das Monitoring
          zeigt dann je Empfehlung, wie oft ihr das Muster schon spielt und
          wie eure Posts dabei laufen.
        </p>
        <div style={{ maxHeight: '16rem', overflowY: 'auto' }}>
          {(channels || []).map((channel) => (
            <label key={channel.id} style={{ display: 'block', padding: '0.15rem 0' }}>
              <input
                type="checkbox"
                checked={Boolean(channel.is_own)}
                onChange={() => onToggleChannelOwn(channel)}
              />{' '}
              {channel.name} <span className="muted small">({channel.platform}{channel.handle ? ` · @${channel.handle}` : ''})</span>
            </label>
          ))}
        </div>
      </details>
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
