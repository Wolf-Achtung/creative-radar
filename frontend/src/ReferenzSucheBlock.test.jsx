// Referenz-Suche (Roadmap Schritt 1, 25.08.2026) — Flag-Gate und
// Facetten-Verhalten des Werkbank-Blocks. Zentrale Zusagen:
//
// 1. Derselbe main-Build zeigt auf Produktion NICHTS (health meldet
//    das Feature nicht), auf Staging die Suche — Laufzeit-Entscheidung
//    ueber /api/health, keine Build-Variable.
// 2. Ein Facetten-Klick loest eine NEUE Suche mit dem Filter aus —
//    die Treffer sind serverseitig gefiltert, nicht clientseitig.
// 3. Die Trefferkarte verlinkt den Post und zeigt das Bild ueber den
//    Thumbnail-Proxy (asset_id), nicht ueber rohe CDN-URLs.
import React from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('./api/client', () => ({
  apiUrl: (path) => `https://api.test${path}`,
  endpoints: {
    health: vi.fn(),
    referenzSuche: vi.fn(),
  },
}));

import { endpoints } from './api/client';
import ReferenzSucheBlock from './ReferenzSucheBlock';

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const TREFFER = {
  post_url: 'https://tiktok.test/p/1',
  asset_id: 'asset-123',
  platform: 'tiktok',
  channel_handle: 'netflixde',
  market: 'DE',
  lift: 2.3,
  views: 120000,
  caption: 'Traust du dich?',
  detected_at: '2026-08-20',
  titel: 'Steckerlfisch Fiasko',
  genre: 'Horror',
};

const DATEN = {
  window_days: 90,
  gesamt: 7,
  treffer: [TREFFER],
  facetten_zaehlung: {
    genre: [{ wert: 'Horror', anzahl: 5 }, { wert: 'Romance', anzahl: 2 }],
    tone: [{ wert: 'humorous', anzahl: 3 }],
  },
};

describe('ReferenzSucheBlock — Flag-Gate und Suche', () => {
  it('rendert NICHTS, wenn health das Feature nicht meldet (Produktion)', async () => {
    endpoints.health.mockResolvedValue({ features: { referenz_suche: false } });
    const { container } = render(<ReferenzSucheBlock />);
    await waitFor(() => expect(endpoints.health).toHaveBeenCalled());
    expect(container.innerHTML).toBe('');
    expect(endpoints.referenzSuche).not.toHaveBeenCalled();
  });

  it('rendert auch bei kaputtem health nichts', async () => {
    endpoints.health.mockRejectedValue(new Error('boom'));
    const { container } = render(<ReferenzSucheBlock />);
    await waitFor(() => expect(endpoints.health).toHaveBeenCalled());
    expect(container.innerHTML).toBe('');
  });

  it('laedt mit Flag die Treffer und zeigt Karte mit Proxy-Bild und Link', async () => {
    endpoints.health.mockResolvedValue({ features: { referenz_suche: true } });
    endpoints.referenzSuche.mockResolvedValue(DATEN);
    render(<ReferenzSucheBlock />);
    expect(await screen.findByText(/7 Treffer/)).toBeTruthy();
    expect(screen.getByText('@netflixde')).toBeTruthy();
    expect(screen.getByText('2,3x')).toBeTruthy();
    const link = screen.getByRole('link');
    expect(link.getAttribute('href')).toBe('https://tiktok.test/p/1');
    const bild = link.querySelector('img');
    expect(bild.getAttribute('src')).toBe('https://api.test/api/thumbnails/asset-123');
  });

  it('ein Facetten-Klick loest eine neue Suche mit dem Filter aus', async () => {
    endpoints.health.mockResolvedValue({ features: { referenz_suche: true } });
    endpoints.referenzSuche.mockResolvedValue(DATEN);
    render(<ReferenzSucheBlock />);
    await screen.findByText(/7 Treffer/);

    const genreSelect = screen.getByLabelText('Genre');
    fireEvent.change(genreSelect, { target: { value: 'Horror' } });
    await waitFor(() => expect(endpoints.referenzSuche).toHaveBeenCalledTimes(2));
    expect(endpoints.referenzSuche).toHaveBeenLastCalledWith(
      expect.objectContaining({ facetten: { genre: 'Horror' } }),
    );
  });

  it('Filter zuruecksetzen leert die Facetten und sucht neu', async () => {
    endpoints.health.mockResolvedValue({ features: { referenz_suche: true } });
    endpoints.referenzSuche.mockResolvedValue(DATEN);
    render(<ReferenzSucheBlock />);
    await screen.findByText(/7 Treffer/);
    fireEvent.change(screen.getByLabelText('Genre'), { target: { value: 'Horror' } });
    await waitFor(() => expect(endpoints.referenzSuche).toHaveBeenCalledTimes(2));

    fireEvent.click(screen.getByText('Filter zurücksetzen'));
    await waitFor(() => expect(endpoints.referenzSuche).toHaveBeenCalledTimes(3));
    expect(endpoints.referenzSuche).toHaveBeenLastCalledWith(
      expect.objectContaining({ facetten: {} }),
    );
  });

  it('zeigt bei leerer Treffermenge eine klare Meldung', async () => {
    endpoints.health.mockResolvedValue({ features: { referenz_suche: true } });
    endpoints.referenzSuche.mockResolvedValue({
      ...DATEN, gesamt: 0, treffer: [],
    });
    render(<ReferenzSucheBlock />);
    expect(await screen.findByText('Keine Treffer mit diesen Filtern.')).toBeTruthy();
  });
});
