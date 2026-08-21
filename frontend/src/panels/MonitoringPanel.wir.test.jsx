// Wir-Segment-Sektion (21.08.2026) — empfohlen → gemacht → gewirkt im
// Monitoring. Kritisch: der Leer-Zustand ohne markierte Kanäle muss in
// Klartext zum Quellen-Tab führen, und die Zeilen müssen zwischen
// „gemacht" und „noch nicht gespielt" unterscheiden.
import React from 'react';
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api/client', () => ({
  endpoints: { adminWirSegment: vi.fn() },
}));

import { endpoints } from '../api/client';
import { WirSegmentSection } from './MonitoringPanel';

afterEach(() => { cleanup(); vi.clearAllMocks(); });

describe('WirSegmentSection', () => {
  it('erklaert den Leer-Zustand mit dem Weg zur Markierung', async () => {
    endpoints.adminWirSegment.mockResolvedValue({
      own_channels: 0, eigene_posts_im_fenster: 0, window_days: 90,
      zeilen: [], note: 'Noch kein Kanal als „Wir“ markiert — in Quellen → Wir-Kanäle die eigenen Kanäle ankreuzen.',
    });

    render(<WirSegmentSection />);

    await screen.findByText(/Noch kein Kanal als „Wir“ markiert/);
  });

  it('zeigt gemachte Muster mit eigenem Median-Lift und Vergleich', async () => {
    endpoints.adminWirSegment.mockResolvedValue({
      own_channels: 2, eigene_posts_im_fenster: 34, window_days: 90,
      note: null,
      zeilen: [{
        dimension: 'genre', value: 'Science Fiction',
        empfohlen_median_lift: 1.182, empfohlen_breakout_z: 4.69,
        gemacht: 5, gewirkt_median_lift: 1.4,
      }],
    });

    render(<WirSegmentSection />);

    await screen.findByText(/von uns 5× gemacht, Median-Lift 1.4x/);
    expect(screen.getByText(/Gesamtbestand: 1.182x, z=4.69/)).toBeTruthy();
  });

  it('nennt nicht gespielte Empfehlungen als Luecke statt mit leerer Zahl', async () => {
    endpoints.adminWirSegment.mockResolvedValue({
      own_channels: 1, eigene_posts_im_fenster: 3, window_days: 90,
      note: null,
      zeilen: [{
        dimension: 'cover_kinetik', value: 'title_card',
        empfohlen_median_lift: 1.3, empfohlen_breakout_z: 4.3,
        gemacht: 0, gewirkt_median_lift: null,
      }],
    });

    render(<WirSegmentSection />);

    await screen.findByText(/von uns noch nicht gespielt/);
  });
});
