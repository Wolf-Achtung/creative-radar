import React, { useState } from 'react';
import { proxyImageUrl } from '../api/client';
import { clip, formatDate, getAssetDisplayTitle, inferTitleHint } from '../format';
import { ImagePreview } from './ImagePreview';
import { MetricStrip } from './MetricStrip';

export function AssetCard({ asset, titles, busy, onReview, onAnalyzeVisual, onAssignTitle, onReportMissingTitle, recentlyCreatedTitleName, openCandidate = null, candidateMatchedTitle = null, onConfirmCandidate = () => {}, onDismissCandidate = () => {} }) {
  const platform = asset.platform || asset.channel_platform || asset.media_type || 'instagram';
  // Candidate-Review: bei bereits zugeordnetem Asset (asset.title_id) ist
  // "Bestätigen" gesperrt, bis der Admin den Re-Assign bewusst quittiert.
  const [confirmAck, setConfirmAck] = useState(false);
  const hasTitle = Boolean(asset.title_name || asset.placement_title_text || asset.title_id);
  const visualChecked = asset.visual_analysis_status === "done";
  const workflowSteps = [
    { done: hasTitle, doneLabel: 'Filmtitel bestätigt', openLabel: '1 Filmtitel bestätigen' },
    { done: visualChecked, doneLabel: 'Visual geprüft', openLabel: '2 Visual prüfen' },
    { done: false, doneLabel: 'Entscheidung getroffen', openLabel: '3 Entscheidung treffen' },
  ];
  const hint = inferTitleHint(asset, titles);
  // Label-Fix (Wolf-Freigabe 12.06.2026): „Noch zu prüfen" hat nicht
  // zwischen „Titel ist zugeordnet, nur nie reviewt" und „wirklich offen"
  // getrennt — automatisch zugeordnete Assets suggerierten Handarbeit.
  // Bedingung STRIKT über asset.title_id (echte Whitelist-Zuordnung) —
  // NICHT das hasTitle oben, das placement_title_text (bloß erkannten
  // Bildtext ohne Zuordnung) mitzählt. Bewusst „Zugeordnet" statt
  // „Automatisch zugeordnet": title_id sagt nur, DASS zugeordnet ist,
  // nicht von wem (Auto-Matcher und manuelles Dropdown schreiben
  // denselben Datenzustand). ``needs_review`` bleibt unabhängig von
  // title_id „Noch zu prüfen" — das hat ein Mensch explizit per
  // „Später prüfen" markiert. Titellose new-Assets behalten die
  // neutrale Pill (kein warn-Orange): sie fließen voll in die
  // Auswertung ein, meist ohne Handlungsbedarf — Alarm-Optik wäre das
  // falsche Signal. Reines Anzeige-Label, kein API-/Status-Eingriff.
  const statusDisplay = (() => {
    if (asset.review_status === 'new') {
      return asset.title_id
        ? { label: 'Zugeordnet', pillClass: 'pill good' }
        : { label: 'Noch zu prüfen', pillClass: 'pill' };
    }
    const label = {
      needs_review: 'Noch zu prüfen',
      approved: 'Freigegeben',
      highlight: 'Als Highlight markiert',
      rejected: 'Nicht relevant',
    }[asset.review_status] || asset.review_status;
    return { label, pillClass: 'pill' };
  })();
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
      <div className="asset-preview"><ImagePreview sources={[asset.visual_evidence_url, asset.screenshot_url, asset.thumbnail_url, asset.visual_source_url].map(proxyImageUrl)} /></div>
      <div className="asset-content">
        <div className="asset-topline">
          <span className="asset-title">{getAssetDisplayTitle(asset, titles)}</span>
          <span className="pill">{platform}</span>
          <span className={statusDisplay.pillClass}>{statusDisplay.label}</span>
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
        {openCandidate && (
          <div className={`candidate-suggestion ${asset.title_id ? 'has-warn' : ''}`}>
            <p className="candidate-head">
              Offener Titel-Vorschlag: <strong>{openCandidate.suggested_title}</strong>
              {openCandidate.confidence != null && (
                <span className="candidate-conf"> · conf {Math.round(openCandidate.confidence * 100)}%</span>
              )}
            </p>
            {/* KI-Assist (21.08.2026): die Begruendung des Modells als
                Hinweis fuer die Hand-Pruefung — auch bei "unsicher". */}
            {openCandidate.llm_note && (
              <p className="candidate-llm small muted">{openCandidate.llm_note}</p>
            )}
            {asset.title_id ? (
              <p className="candidate-warn" role="alert">
                ⚠ Asset ist bereits zugeordnet zu „{getAssetDisplayTitle(asset, titles)}“ — vor dem Zuordnen prüfen.
              </p>
            ) : candidateMatchedTitle ? (
              <p className="candidate-match small muted">
                Exakter Titel in der Liste: „{candidateMatchedTitle.title_original}“ — wird beim Bestätigen zugeordnet.
              </p>
            ) : (
              <p className="candidate-nomatch small muted">
                Kein exakter Titel in der Liste — bitte unten manuell zuordnen oder den Vorschlag verwerfen.
              </p>
            )}
            <div className="candidate-actions">
              {asset.title_id && (
                <label className="candidate-ack small">
                  <input
                    type="checkbox"
                    checked={confirmAck}
                    onChange={(e) => setConfirmAck(e.target.checked)}
                    disabled={busy}
                  />
                  Re-Zuordnung bewusst bestätigen
                </label>
              )}
              <button
                type="button"
                className="primary"
                disabled={busy || !candidateMatchedTitle || (Boolean(asset.title_id) && !confirmAck)}
                title={!candidateMatchedTitle ? 'Kein exakter Titel in der Liste — bitte manuell zuordnen' : 'Titel zuordnen und Kandidat schließen'}
                onClick={() => onConfirmCandidate(asset, openCandidate, candidateMatchedTitle)}
              >
                Vorschlag bestätigen
              </button>
              <button
                type="button"
                className="secondary ghost"
                disabled={busy}
                onClick={() => onDismissCandidate(openCandidate)}
              >
                Verwerfen
              </button>
            </div>
          </div>
        )}
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
