// Entscheidungs-Queue (21.08.2026) — Verdrahtung im ReviewPanel: der
// Vorschlags-Modus muss die kompakte Entscheidungs-Karte rendern, der
// „Alle Treffer"-Modus die volle Review-Karte. Faellt dieser Test, ist
// der Umbau still zurueckgerollt und die Queue wieder ueberladen.
import React from 'react';
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api/client', () => ({
  proxyImageUrl: (url) => url,
  endpoints: {},
}));

import { ReviewPanel } from './ReviewPanel';

afterEach(() => { cleanup(); vi.clearAllMocks(); });

const asset = {
  id: 'a1',
  title_id: null,
  review_status: 'new',
  post_url: 'https://x.test/p/1',
  channel_name: 'Lionsgate Horror',
  channel_market: 'US',
  detected_at: '2026-08-13T10:00:00Z',
};

const kandidat = {
  id: 'c1',
  asset_id: 'a1',
  status: 'open',
  suggested_title: 'beware',
  llm_note: "KI unsicher: Der Post bewirbt 'Beware Boiúna', einen Titel, der nicht in der Kandidatenliste enthalten ist.",
};

function renderPanel(assetMode) {
  return render(
    <ReviewPanel
      assets={[asset]}
      titles={[]}
      visibleAssets={[asset]}
      filters={{ status: 'all', platform: 'all', market: 'all', query: '' }}
      setFilters={() => {}}
      busy={false}
      onReview={() => {}}
      onAnalyzeVisual={() => {}}
      onAssignTitle={() => {}}
      onReportMissingTitle={() => {}}
      recentlyCreatedByAssetId={{}}
      openCandidatesByAssetId={{ a1: kandidat }}
      assetMode={assetMode}
    />
  );
}

describe('ReviewPanel Entscheidungs-Queue', () => {
  it('rendert im Vorschlags-Modus die Entscheidungs-Karte mit Titel-Anlage', () => {
    renderPanel('candidates');

    expect(screen.getByRole('button', { name: 'Titel anlegen + zuordnen' })).toBeTruthy();
    const eingabe = screen.getByLabelText('Titelname für Suche oder Anlage');
    expect(eingabe.value).toBe('Beware Boiúna');
    // Die Report-Buttons der vollen Karte stehen NICHT offen auf der Karte.
    expect(screen.queryByRole('button', { name: 'Als Top-Fund markieren' })).toBeNull();
  });

  it('rendert im Alle-Treffer-Modus weiterhin die volle Review-Karte', () => {
    renderPanel('all');

    expect(screen.getByRole('button', { name: 'Als Top-Fund markieren' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Titel anlegen + zuordnen' })).toBeNull();
  });
});
