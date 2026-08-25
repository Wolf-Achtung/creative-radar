// Post-Check (Roadmap-Ausbau, 25.08.2026) — Flag-Gate und Ablauf.
// Zusagen: Production ohne Flag sieht NICHTS; "Entwurf prüfen" schickt
// die Eingaben an den Endpoint; die Antwort erscheint als Zeilen mit
// Befund und Tipp; ein Fehler wird benannt statt verschluckt.
import React from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('./api/client', () => ({
  apiUrl: (path) => `https://api.test${path}`,
  endpoints: {
    health: vi.fn(),
    postCheck: vi.fn(),
  },
}));

import { endpoints } from './api/client';
import PostCheckBlock from './PostCheckBlock';

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const ERGEBNIS = {
  checks: [
    { dimension: 'caption_frage', wert: 'mit_frage', befund: 'gut', satz: 'Caption mit Frage: funktioniert gerade.', tipp: null },
    { dimension: 'caption_laenge', wert: 'lang', befund: 'achtung', satz: 'Lange Caption (200+): funktioniert gerade nicht.', tipp: 'Gerade besser: Kurze Caption (≤80 Zeichen).' },
  ],
  zusammenfassung: { gut: 1, achtung: 1, neutral: 0, kein_befund: 0 },
  window_days: 90,
  basis: { posts: 5804, kanaele: 156 },
};

describe('PostCheckBlock — Flag-Gate und Ablauf', () => {
  it('rendert NICHTS, wenn health das Feature nicht meldet (Produktion)', async () => {
    endpoints.health.mockResolvedValue({ features: { post_check: false } });
    const { container } = render(<PostCheckBlock />);
    await waitFor(() => expect(endpoints.health).toHaveBeenCalled());
    expect(container.innerHTML).toBe('');
  });

  it('prueft den Entwurf und zeigt Befunde mit Tipp', async () => {
    endpoints.health.mockResolvedValue({ features: { post_check: true } });
    endpoints.postCheck.mockResolvedValue(ERGEBNIS);
    render(<PostCheckBlock />);
    await screen.findByText('Entwurf prüfen, bevor er rausgeht');

    fireEvent.change(screen.getByLabelText('Caption-Entwurf'), {
      target: { value: 'Traust du dich?' },
    });
    fireEvent.click(screen.getByText('Entwurf prüfen'));

    expect(await screen.findByText(/1 Punkt spricht gerade dagegen/)).toBeTruthy();
    expect(endpoints.postCheck).toHaveBeenCalledWith({
      caption: 'Traust du dich?',
      duration_seconds: null,
      titel_im_bild: null,
      format: null,
      tonfall: null,
    });
    expect(screen.getByText('Caption mit Frage: funktioniert gerade.')).toBeTruthy();
    expect(screen.getByText('Gerade besser: Kurze Caption (≤80 Zeichen).')).toBeTruthy();
    expect(screen.getByText(/5804 Posts aus 156 Kanälen/)).toBeTruthy();
  });

  it('meldet einen Fehler, statt ihn zu verschlucken', async () => {
    endpoints.health.mockResolvedValue({ features: { post_check: true } });
    endpoints.postCheck.mockRejectedValue(new Error('API error 503'));
    render(<PostCheckBlock />);
    await screen.findByText('Entwurf prüfen, bevor er rausgeht');
    fireEvent.change(screen.getByLabelText('Caption-Entwurf'), { target: { value: 'x' } });
    fireEvent.click(screen.getByText('Entwurf prüfen'));
    expect(await screen.findByText(/API error 503/)).toBeTruthy();
  });

  it('ohne Caption bleibt der Knopf gesperrt', async () => {
    endpoints.health.mockResolvedValue({ features: { post_check: true } });
    render(<PostCheckBlock />);
    const knopf = await screen.findByText('Entwurf prüfen');
    expect(knopf.disabled).toBe(true);
  });
});
