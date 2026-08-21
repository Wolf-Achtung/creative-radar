import React, { useState } from 'react';
import { proxyImageUrl } from '../api/client';
import { clip, formatDate, formatNumber, getAssetDisplayTitle } from '../format';
import { ImagePreview } from './ImagePreview';

// Wolfs UX-Befund 21.08.2026: die Vorschlags-Queue zeigte jede Karte in
// voller Review-Ansicht (Report-Buttons, Workflow-Pills, Analyse-Box,
// Dropdown) — die eine anstehende Entscheidung ging darin unter, und
// verfallene Instagram-CDN-Bilder standen als kaputtes Icon da. Diese
// Karte zeigt pro offenem Vorschlag genau EINE Entscheidung mit den
// 2–3 Aktionen, die der jeweilige Fall wirklich hat; alles andere
// steht hinter „Mehr". Die Bildflaeche verlinkt immer auf den
// Original-Post — auch wenn kein Bild mehr ladbar ist.

// Die KI-Notiz nennt den echten Filmtitel meist in Anfuehrungszeichen
// („KI unsicher: Der Post bewirbt 'Beware Boiúna', einen Titel, der
// nicht …"). Der erste zitierte Abschnitt ist der beste Vorschlag fuer
// die Titel-Anlage — praeziser als der oft verstuemmelte
// suggested_title (z. B. „beware").
export function extractTitleFromNote(note) {
  if (!note) return '';
  const treffer = note.match(/„([^“]{2,80})“|'([^']{2,80})'|"([^"]{2,80})"|‚([^‘]{2,80})‘/);
  if (!treffer) return '';
  return (treffer[1] || treffer[2] || treffer[3] || treffer[4] || '').trim();
}

export function CandidateDecisionCard({
  asset,
  titles,
  busy,
  openCandidate,
  candidateMatchedTitle = null,
  onConfirmCandidate = () => {},
  onDismissCandidate = () => {},
  onAssignTitle = () => {},
  onCreateTitleFromCandidate = () => {},
  onReview = () => {},
}) {
  const vorschlag = openCandidate?.suggested_title || '';
  const vorbefuellt = extractTitleFromNote(openCandidate?.llm_note) || vorschlag;
  const [neuerTitel, setNeuerTitel] = useState(vorbefuellt);
  const [umzugScharf, setUmzugScharf] = useState(false);
  // Kandidat gewechselt (nach Resolve laedt die Liste neu) → Eingabe und
  // Umzugs-Schutz zuruecksetzen. Anpassung waehrend des Renders, gleiches
  // Muster wie ImagePreview (kein Effect noetig).
  const [vorigerKandidat, setVorigerKandidat] = useState(openCandidate?.id);
  if (openCandidate?.id !== vorigerKandidat) {
    setVorigerKandidat(openCandidate?.id);
    setNeuerTitel(vorbefuellt);
    setUmzugScharf(false);
  }

  const bereitsZugeordnet = Boolean(asset.title_id);
  const metaTeile = [
    asset.channel_name || 'Unbekannter Kanal',
    asset.channel_market || null,
    formatDate(asset.published_at || asset.detected_at || asset.created_at),
    asset.visible_views != null ? `${formatNumber(asset.visible_views)} Views` : null,
    asset.visible_likes != null ? `${formatNumber(asset.visible_likes)} Likes` : null,
  ].filter(Boolean);

  return (
    <article className="decision-card">
      <a
        className="decision-preview"
        href={asset.post_url || undefined}
        target="_blank"
        rel="noreferrer"
        title="Original-Post öffnen"
      >
        <ImagePreview sources={[asset.visual_evidence_url, asset.screenshot_url, asset.thumbnail_url, asset.visual_source_url].map(proxyImageUrl)} />
        <span className="decision-open">Original öffnen ↗</span>
      </a>
      <div className="decision-content">
        <p className="decision-meta">{metaTeile.join(' · ')}</p>
        <p className="decision-head">
          Vorschlag: <strong>{vorschlag}</strong>
          {openCandidate?.confidence != null && (
            <span className="candidate-conf"> · conf {Math.round(openCandidate.confidence * 100)}%</span>
          )}
        </p>
        {openCandidate?.llm_note && <p className="decision-llm">{openCandidate.llm_note}</p>}
        {asset.caption && <p className="decision-caption muted small">{clip(asset.caption, 160)}</p>}

        {bereitsZugeordnet ? (
          <div className="decision-verdict">
            <p>Dieser Treffer ist bereits <strong>„{getAssetDisplayTitle(asset, titles)}“</strong> zugeordnet.</p>
            <div className="decision-actions">
              <button
                type="button"
                className="primary"
                disabled={busy}
                onClick={() => onDismissCandidate(openCandidate)}
              >
                Zuordnung behalten — Vorschlag schließen
              </button>
              {candidateMatchedTitle && candidateMatchedTitle.id !== asset.title_id && (
                umzugScharf ? (
                  <button
                    type="button"
                    className="secondary"
                    disabled={busy}
                    onClick={() => onConfirmCandidate(asset, openCandidate, candidateMatchedTitle)}
                  >
                    Wirklich zu „{candidateMatchedTitle.title_original}“ umziehen
                  </button>
                ) : (
                  <button
                    type="button"
                    className="secondary ghost"
                    disabled={busy}
                    onClick={() => setUmzugScharf(true)}
                  >
                    Stattdessen „{candidateMatchedTitle.title_original}“ zuordnen …
                  </button>
                )
              )}
            </div>
          </div>
        ) : candidateMatchedTitle ? (
          <div className="decision-verdict">
            <p><strong>„{candidateMatchedTitle.title_original}“</strong> steht in der Titelliste — ein Klick ordnet zu.</p>
            <div className="decision-actions">
              <button
                type="button"
                className="primary"
                disabled={busy}
                onClick={() => onConfirmCandidate(asset, openCandidate, candidateMatchedTitle)}
              >
                „{candidateMatchedTitle.title_original}“ zuordnen
              </button>
              <button type="button" className="secondary ghost" disabled={busy} onClick={() => onDismissCandidate(openCandidate)}>
                Verwerfen
              </button>
            </div>
          </div>
        ) : (
          <div className="decision-verdict">
            <p>Dieser Titel steht <strong>nicht in der Titelliste</strong>. Anlegen ordnet ihn sofort zu; Name vorher prüfen.</p>
            <div className="decision-actions decision-actions--create">
              <input
                type="text"
                value={neuerTitel}
                onChange={(e) => setNeuerTitel(e.target.value)}
                disabled={busy}
                aria-label="Titelname für die Anlage"
              />
              <button
                type="button"
                className="primary"
                disabled={busy || !neuerTitel.trim()}
                onClick={() => onCreateTitleFromCandidate(asset, openCandidate, neuerTitel.trim())}
              >
                Titel anlegen + zuordnen
              </button>
              <button type="button" className="secondary ghost" disabled={busy} onClick={() => onDismissCandidate(openCandidate)}>
                Verwerfen
              </button>
            </div>
          </div>
        )}

        <details className="decision-more">
          <summary>Mehr: andere Zuordnung, Report-Aktionen, KI-Analyse</summary>
          <label className="title-select">
            Filmtitel-Zuordnung (ganze Liste)
            <select value={asset.title_id || ''} onChange={(e) => onAssignTitle(asset, e.target.value)} disabled={busy}>
              <option value="">Bitte Filmtitel auswählen</option>
              {titles.map((title) => <option key={title.id} value={title.id}>{title.title_original}</option>)}
            </select>
          </label>
          <div className="decision-actions">
            <button type="button" disabled={busy} onClick={() => onReview(asset, 'approved')}>Für Report freigeben</button>
            <button type="button" disabled={busy} onClick={() => onReview(asset, 'rejected')}>Aussortieren</button>
          </div>
          {(asset.visual_notes || asset.ai_summary_de) && (
            <p className="muted small">{clip(asset.visual_notes || asset.ai_summary_de, 300)}</p>
          )}
          {asset.caption && <p className="muted small"><strong>Caption:</strong> {clip(asset.caption, 400)}</p>}
        </details>
      </div>
    </article>
  );
}
