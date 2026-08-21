import React from 'react';
import { findExactTitleForSuggestion } from '../format';
import { ASSET_PAGE_SIZE } from '../constants';
import { AssetCard } from '../components/AssetCard';
import { CandidateDecisionCard } from '../components/CandidateDecisionCard';
import { ReviewGuide } from '../components/ReviewGuide';
import { Section } from '../components/Section';

// Sprint 28.05.2026 (Admin-Sektion): NAV_ITEMS ohne "DE/US Vergleich".
// Die Alt-Last-Komponente ComparisonPanel + ihre Daten waren ein
// Vorgaenger des Pair-Brief-"Drei Maerkte, ein Film"-Blocks (#191) und
// inhaltlich ueberholt — entfernt. Die anderen drei Werkzeuge wandern
// in die geschuetzte Admin-Sektion (siehe AdminPage.jsx).
export const STATUS_OPTIONS = ['all', 'new', 'needs_review', 'approved', 'highlight', 'rejected'];

export function ReviewPanel({
  assets,
  titles,
  visibleAssets,
  filters,
  setFilters,
  busy,
  onReview,
  onAnalyzeVisual,
  onAssignTitle,
  onReportMissingTitle,
  recentlyCreatedByAssetId,
  openCandidatesByAssetId = {},
  onConfirmCandidate = () => {},
  onDismissCandidate = () => {},
  onCreateTitleFromCandidate = () => {},
  assetMode = 'candidates',
  assetsHasMore = false,
  onSwitchAssetMode = () => {},
  onLoadMoreAssets = () => {},
}) {
  const modeKicker = assetMode === 'candidates'
    ? `${visibleAssets.length} Vorschläge zur Prüfung`
    : `${visibleAssets.length} von ${assets.length} geladen${assetsHasMore ? ' (weitere verfügbar)' : ''}`;
  return (
    <Section title="Treffer prüfen" kicker={modeKicker} helpKey="adminReview"><p className="muted small">Standard: nur Assets mit offenem Titel-Vorschlag. „Alle Treffer“ lädt den vollen Bestand seitenweise.</p>
      {/* UX-Audit Befund 11 (2026-07-14): der aktive Modus war vorher per
          ``disabled`` markiert — sah "kaputt" statt "ausgewählt" aus.
          Jetzt aria-pressed + .active-Stil; der Klick auf den bereits
          aktiven Modus ist ein No-Op im Handler statt einer gesperrten
          Schaltfläche. */}
      <div className="review-mode-toggle">
        <button
          type="button"
          className={assetMode === 'candidates' ? 'active' : ''}
          aria-pressed={assetMode === 'candidates'}
          onClick={() => assetMode !== 'candidates' && onSwitchAssetMode('candidates')}
          disabled={busy}
        >
          Nur Titel-Vorschläge
        </button>
        <button
          type="button"
          className={assetMode === 'all' ? 'active' : ''}
          aria-pressed={assetMode === 'all'}
          onClick={() => assetMode !== 'all' && onSwitchAssetMode('all')}
          disabled={busy}
        >
          Alle Treffer (seitenweise)
        </button>
      </div>
      {/* Entscheidungs-Queue: die lange Report-Anleitung gehoert zum
          „Alle Treffer"-Modus — im Vorschlags-Modus reicht ein Satz. */}
      {assetMode === 'candidates' ? (
        <p className="muted small">
          Jede Karte ist genau eine Entscheidung: Titel <strong>zuordnen</strong> (steht in der Liste),
          <strong> anlegen</strong> (fehlt in der Liste) oder den Vorschlag <strong>verwerfen</strong>.
          Report-Aktionen und KI-Analyse stehen je Karte hinter „Mehr“.
        </p>
      ) : (
        <ReviewGuide />
      )}
      <div className="filterbar">
        <select value={filters.status} onChange={(e) => setFilters({ ...filters, status: e.target.value })}>
          {STATUS_OPTIONS.map((status) => <option key={status} value={status}>{status === 'all' ? 'Alle Status' : status}</option>)}
        </select>
        <select value={filters.platform} onChange={(e) => setFilters({ ...filters, platform: e.target.value })}>
          <option value="all">Alle Kanäle</option>
          <option value="instagram">Instagram</option>
          <option value="tiktok">TikTok</option>
          <option value="youtube">YouTube</option>
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
      {visibleAssets.map((asset) => {
        const openCandidate = openCandidatesByAssetId[asset.id] || null;
        const candidateMatchedTitle = openCandidate
          ? findExactTitleForSuggestion(openCandidate.suggested_title, titles)
          : null;
        // Entscheidungs-Queue (21.08.2026): im Vorschlags-Modus zeigt jede
        // Karte genau die eine anstehende Entscheidung — die volle
        // Review-Karte bleibt dem „Alle Treffer"-Modus vorbehalten.
        if (assetMode === 'candidates' && openCandidate) {
          return (
            <CandidateDecisionCard
              key={asset.id}
              asset={asset}
              titles={titles}
              busy={busy}
              openCandidate={openCandidate}
              candidateMatchedTitle={candidateMatchedTitle}
              onConfirmCandidate={onConfirmCandidate}
              onDismissCandidate={onDismissCandidate}
              onCreateTitleFromCandidate={onCreateTitleFromCandidate}
              onReview={onReview}
            />
          );
        }
        return (
          <AssetCard
            key={asset.id}
            asset={asset}
            titles={titles}
            busy={busy}
            onReview={onReview}
            onAnalyzeVisual={onAnalyzeVisual}
            onAssignTitle={onAssignTitle}
            onReportMissingTitle={onReportMissingTitle}
            recentlyCreatedTitleName={recentlyCreatedByAssetId[asset.id] || ''}
            openCandidate={openCandidate}
            candidateMatchedTitle={candidateMatchedTitle}
            onConfirmCandidate={onConfirmCandidate}
            onDismissCandidate={onDismissCandidate}
          />
        );
      })}
      {assetMode === 'candidates' && assets.length === 0 && (
        <p className="muted small">Keine offenen Titel-Vorschläge. Über „Alle Treffer“ den vollen Bestand prüfen.</p>
      )}
      {assetMode === 'all' && assetsHasMore && (
        <div className="section-actions">
          <button type="button" className="secondary" onClick={onLoadMoreAssets} disabled={busy}>
            Weitere {ASSET_PAGE_SIZE} laden
          </button>
        </div>
      )}
    </Section>
  );
}
