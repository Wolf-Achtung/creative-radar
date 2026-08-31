import React, { useState } from 'react';
import { apiUrl, proxyImageUrl } from '../api/client';
import { clip, formatDate, formatNumber, getAssetDisplayTitle, searchTitles } from '../format';
import { ImagePreview } from './ImagePreview';

// Wolfs UX-Befund 21.08.2026: die Vorschlags-Queue zeigte jede Karte in
// voller Review-Ansicht — die eine anstehende Entscheidung ging darin
// unter. Diese Karte zeigt pro offenem Vorschlag genau EINE Entscheidung
// mit den 2–3 Aktionen des jeweiligen Falls.
//
// Nachschlag (gleicher Abend): das Zuordnungs-Dropdown mit zehntausenden
// Eintraegen war unbenutzbar, und der Fall „bereits zugeordnet" hatte
// keinen Weg, einen fehlenden Titel anzulegen. Jetzt traegt jede Karte
// EIN Eingabefeld, das beides ist: Umlaut-tolerante Live-Suche ueber die
// Titelliste (Treffer als Klick-Buttons, Zuordnen loest den Vorschlag)
// und zugleich der Name fuer „Titel anlegen + zuordnen", wenn die Suche
// nichts findet. Hintergrund: der Titel-Sync holt nur die Slates der 6
// Studio-Paare — Filme anderer Verleiher (Tobis, DCM, …) fehlen im
// Katalog SYSTEMATISCH, die Anlage per Klick ist ihr vorgesehener Weg.

// Die KI-Notiz nennt den echten Filmtitel meist in Anfuehrungszeichen
// („KI unsicher: Der Post bewirbt 'Beware Boiúna', einen Titel, der
// nicht …"). Der erste zitierte Abschnitt ist der beste Vorschlag fuer
// die Titel-Anlage — praeziser als der oft verstuemmelte
// suggested_title (z. B. „beware").
export function extractTitleFromNote(note) {
  if (!note) return '';
  // 22.08.: die deutsche Low-9-Variante schliesst je nach Autor mit ‘
  // ODER ’ („‚H wie Habicht’") — beide Schlusszeichen akzeptieren,
  // sonst faellt die Vorbefuellung auf den verstuemmelten
  // suggested_title zurueck (Wolfs „adaptation"-Fall vom 21.08.).
  const treffer = note.match(/„([^“]{2,80})“|'([^']{2,80})'|"([^"]{2,80})"|‚([^‘’]{2,80})[‘’]/);
  if (!treffer) return '';
  return (treffer[1] || treffer[2] || treffer[3] || treffer[4] || '').trim();
}


// Wolfs Queue-Befund 31.08.2026: der Zuordnen-Button zeigte „트리거“ —
// den Original-Titel der koreanischen Serie, deren LOKALTITEL
// „Trigger“ den Vorschlag ueberhaupt erst gematcht hatte. Die Anzeige
// bevorzugt den Lokaltitel und haelt das Original als Klammer-Zusatz,
// damit die Zuordnung nachvollziehbar bleibt.
export function titelAnzeigeName(title) {
  if (!title) return '';
  const original = title.title_original || '';
  const lokal = (title.title_local || '').trim();
  if (lokal && lokal.toLowerCase() !== original.trim().toLowerCase()) {
    return `${lokal} (${original})`;
  }
  return original;
}

export function CandidateDecisionCard({
  asset,
  titles,
  busy,
  openCandidate,
  candidateMatchedTitle = null,
  onConfirmCandidate = () => {},
  onDismissCandidate = () => {},
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
  const suchTreffer = searchTitles(titles, neuerTitel).filter((t) => t.id !== asset.title_id);
  const metaTeile = [
    asset.channel_name || 'Unbekannter Kanal',
    asset.channel_market || null,
    formatDate(asset.published_at || asset.detected_at || asset.created_at),
    asset.visible_views != null ? `${formatNumber(asset.visible_views)} Views` : null,
    asset.visible_likes != null ? `${formatNumber(asset.visible_likes)} Likes` : null,
  ].filter(Boolean);

  // EIN Feld fuer Suchen UND Anlegen: die Live-Treffer zeigen sofort, ob
  // der getippte Titel schon im Katalog steht (Klick ordnet zu und
  // schliesst den Vorschlag) — erst wenn nichts passt, ist Anlegen dran.
  const sucheUndAnlage = (
    <div className="decision-search">
      <input
        type="text"
        value={neuerTitel}
        onChange={(e) => setNeuerTitel(e.target.value)}
        disabled={busy}
        placeholder="Filmtitel tippen — findet auch Umlaute (lugen → Lügen)"
        aria-label="Titelname für Suche oder Anlage"
      />
      {suchTreffer.length > 0 && (
        <div className="decision-search-treffer">
          {suchTreffer.map((title) => (
            <button
              key={title.id}
              type="button"
              className="secondary"
              disabled={busy}
              onClick={() => onConfirmCandidate(asset, openCandidate, title)}
            >
              {bereitsZugeordnet ? `Stattdessen „${titelAnzeigeName(title)}“ zuordnen` : `„${titelAnzeigeName(title)}“ zuordnen`}
            </button>
          ))}
        </div>
      )}
      <div className="decision-actions">
        <button
          type="button"
          className={suchTreffer.length > 0 ? 'secondary' : 'primary'}
          disabled={busy || !neuerTitel.trim()}
          onClick={() => onCreateTitleFromCandidate(asset, openCandidate, neuerTitel.trim())}
        >
          {bereitsZugeordnet ? 'Titel neu anlegen + stattdessen zuordnen' : 'Titel anlegen + zuordnen'}
        </button>
        <button type="button" className="secondary ghost" disabled={busy} onClick={() => onDismissCandidate(openCandidate)}>
          Verwerfen
        </button>
      </div>
    </div>
  );

  return (
    <article className="decision-card">
      <a
        className="decision-preview"
        href={asset.post_url || undefined}
        target="_blank"
        rel="noreferrer"
        title="Original-Post öffnen"
      >
        {/* Bild-Fix 22.08.2026: PRIMAER der /api/thumbnails-Proxy — er
            liefert die beim Scrape GESPEICHERTE Evidence (302 auf
            signierte URL) und faellt server-seitig mit korrektem
            Referer auf die CDN zurueck. Die rohen Felder scheiterten
            doppelt: der nackte Storage-Key ("evidence/…") ging als
            relative URL kaputt, und /api/img blockt Instagram per
            Hotlink-Schutz (leerer Referer → 403). Muster: Dashboard-
            Referenz-Posts (InsightWeekly/PatternsBlock). */}
        <ImagePreview sources={[apiUrl(`/api/thumbnails/${asset.id}`), proxyImageUrl(asset.thumbnail_url)]} />
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
                    Wirklich zu „{titelAnzeigeName(candidateMatchedTitle)}“ umziehen
                  </button>
                ) : (
                  <button
                    type="button"
                    className="secondary ghost"
                    disabled={busy}
                    onClick={() => setUmzugScharf(true)}
                  >
                    Stattdessen „{titelAnzeigeName(candidateMatchedTitle)}“ zuordnen …
                  </button>
                )
              )}
            </div>
            <p className="decision-alt">Richtiger Titel ist ein anderer oder fehlt im Katalog? Suchen oder anlegen:</p>
            {sucheUndAnlage}
          </div>
        ) : candidateMatchedTitle ? (
          <div className="decision-verdict">
            <p><strong>„{titelAnzeigeName(candidateMatchedTitle)}“</strong> steht in der Titelliste — ein Klick ordnet zu.</p>
            <div className="decision-actions">
              <button
                type="button"
                className="primary"
                disabled={busy}
                onClick={() => onConfirmCandidate(asset, openCandidate, candidateMatchedTitle)}
              >
                „{titelAnzeigeName(candidateMatchedTitle)}“ zuordnen
              </button>
              <button type="button" className="secondary ghost" disabled={busy} onClick={() => onDismissCandidate(openCandidate)}>
                Verwerfen
              </button>
            </div>
          </div>
        ) : (
          <div className="decision-verdict">
            <p>Dieser Titel steht <strong>nicht in der Titelliste</strong> — tippen zeigt sofort, ob es ihn doch schon gibt; sonst anlegen (ordnet sofort zu).</p>
            {sucheUndAnlage}
          </div>
        )}

        <details className="decision-more">
          <summary>Mehr: {candidateMatchedTitle && !bereitsZugeordnet ? 'anderen Titel suchen/anlegen, ' : ''}Report-Aktionen, KI-Analyse</summary>
          {candidateMatchedTitle && !bereitsZugeordnet && (
            <>
              <p className="decision-alt">Der Vorschlag passt nicht? Anderen Titel suchen oder anlegen:</p>
              {sucheUndAnlage}
            </>
          )}
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
