import React from 'react';
import { apiUrl, proxyImageUrl } from '../api/client';
import { ImagePreview } from '../components/ImagePreview';
import { Section } from '../components/Section';

export function ReportsPanel({ report, busy, suggestion, form, setForm, onSuggest, onGenerateSuggestedReport, onRelaxFiltersAndSuggest }) {
  const reportTypes = [
    { key: 'weekly_overview', label: 'Wochenüberblick', desc: 'Die wichtigsten Creative-Funde der letzten Tage. Für Geschäftsführung, Marketing und schnelle Wochenupdates.' },
    { key: 'de_us_comparison', label: 'DE/US Vergleich', desc: 'Vergleicht deutsche und internationale Creatives: Titel, Claims, CTAs, Kinetics und visuelle Platzierung.' },
    { key: 'visual_kinetics', label: 'Bild/Text & Kinetics', desc: 'Dokumentiert sichtbaren Text, Claims, Overlays, CTAs, Kinetics und Screenshots.' },
  ];
  const selectedType = reportTypes.find((t) => t.key === form.report_type);
  const hasSuggestedAssets = Boolean(suggestion?.assets?.length);
  const step = !suggestion ? 2 : !hasSuggestedAssets ? 3 : !report ? 4 : 5;
  return (
    <Section title="Welchen Report möchtest du erstellen?" kicker="Report-Zentrale" helpKey="adminReports">
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
        <label>Kanäle<select value={form.channel || "all"} onChange={(e) => setForm({ ...form, channel: e.target.value })}><option value="all">alle</option><option value="tiktok">TikTok</option><option value="instagram">Instagram</option><option value="youtube">YouTube</option></select></label>
        <label>Märkte<select value={form.market} onChange={(e) => setForm({ ...form, market: e.target.value })}><option value="all">alle</option><option value="DE">DE</option><option value="US">US</option><option value="INT">INT</option><option value="UNKNOWN">UNKNOWN</option></select></label>
        <label>Max. Assets<select value={form.limit} onChange={(e) => setForm({ ...form, limit: Number(e.target.value) })}><option value={5}>5</option><option value={10}>10</option><option value={20}>20</option><option value={50}>50</option><option value={100}>100</option></select></label>
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
            <p className="muted small">
              Geprüft: {suggestion.checked} · Geeignet: {suggestion.eligible} · Vorgeschlagen: {suggestion.selected} · Ohne Bildquelle: {suggestion.excluded?.missing_visual || 0} · Ohne gesichertes Evidence-Bild: {suggestion.excluded?.missing_secure_evidence || 0} · Mit externer/ungesicherter Bildquelle: {(suggestion.excluded?.external_visual_only || 0) + (suggestion.excluded?.source_only_visual || 0)} · Ausgeschlossen wegen fehlendem Titel: {suggestion.excluded?.missing_title || 0}
            </p>
            <div className="find-grid">{suggestion.assets.map((asset) => {
              const tags = asset.tags || [];
              const warnings = asset.warnings || [];
              const beleg = tags.length > 0 ? tags.join(' · ') : 'keine starken Belege';
              const hinweis = warnings.length > 0 ? warnings.join(' · ') : 'keine kritischen Hinweise';
              return (
                <article key={asset.asset_id} className="find-card">
                  <ImagePreview
                    sources={((asset.display_image_candidates && asset.display_image_candidates.length > 0)
                      ? asset.display_image_candidates
                      : (asset.display_image_url ? [asset.display_image_url] : [])
                    ).map(proxyImageUrl)}
                    evidenceQuality={asset.evidence_quality}
                  />
                  <div>
                    <p className="find-title">{asset.title || (asset.caption ? (asset.caption.length > 60 ? asset.caption.slice(0, 60).trim() + '…' : asset.caption) : 'Ohne Titel')}</p>
                    <p className="muted small">{asset.channel} · {asset.market} · Eignung: {asset.suitability}</p>
                    <p className="small">{asset.reason}</p>
                    <p className="small muted">Belege: {beleg}</p>
                    <p className="small muted">Hinweise: {hinweis}</p>
                    <p className="small muted">Quelle: {asset.evidence_label || 'unbekannt'}</p>
                  </div>
                </article>
              );
            })}</div>
          </>
        )}
        <div className="section-actions"><button className="primary" onClick={onGenerateSuggestedReport} disabled={busy || suggestion.assets.length===0}>Report erzeugen</button></div>
        {!hasSuggestedAssets && <p className="small muted">Report kann erst erzeugt werden, wenn ein Vorschlag Assets enthält.</p>}
      </>)}
      {report ? <details><summary>Aktuellen Report anzeigen</summary><p className="muted small">Report wurde erstellt.</p><div className="section-actions"><a className="secondary" href={apiUrl('/api/reports/latest/download.html')} download>HTML herunterladen</a><a className="secondary" href={apiUrl('/api/reports/latest/download.md')} download>Markdown herunterladen</a></div><iframe title="report" srcDoc={report.html_content || ''} /></details> : <p className="muted">Noch kein Report erzeugt.</p>}
    </Section>
  );
}
