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
    { dimension: 'caption_laenge', wert: 'lang', befund: 'achtung', satz: 'Lange Caption: funktioniert gerade nicht.', tipp: 'Gerade besser: Kurze Caption.' },
    { dimension: 'caption_hashtags', wert: '1-3', befund: 'neutral', satz: '1-3 Hashtags: unauffällig — weder stark noch schwach.', tipp: null },
    { dimension: 'format_class', wert: 'kurzform', befund: 'neutral', satz: 'Kurzform: unauffällig — weder stark noch schwach.', tipp: null },
    { dimension: 'cover_titel', wert: 'mit_titel', befund: 'kein_befund', satz: 'Titel im Bild: dazu gibt es gerade keinen belastbaren Befund.', tipp: null },
  ],
  chancen: [
    { dimension: 'cover_kinetik', wert: 'title_card', satz: 'Title-Card — funktioniert gerade. Im Entwurf nicht angegeben.' },
  ],
  zusammenfassung: { gut: 1, achtung: 1, neutral: 2, kein_befund: 1 },
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
    // Chancen: was den Post staerker machen koennte, steht als eigener Block.
    expect(screen.getByText('Was den Post stärker machen könnte:')).toBeTruthy();
    expect(screen.getByText(/Title-Card — funktioniert gerade/)).toBeTruthy();
    // Unauffaelliges ist EINE Sammelzeile, keine Einzelzeilen (Wolfs
    // Feedback 25.08.: sieben Mal "unauffaellig" ist keine Hilfe).
    expect(screen.getByText(/Unauffällig: 1–3 Hashtags, Kurzform\./)).toBeTruthy();
    expect(screen.queryByText(/unauffällig — weder stark noch schwach/)).toBeNull();
    expect(screen.getByText(/Kein belastbarer Befund zu: Titel im Bild\./)).toBeTruthy();
    expect(endpoints.postCheck).toHaveBeenCalledWith({
      caption: 'Traust du dich?',
      duration_seconds: null,
      titel_im_bild: null,
      format: null,
      tonfall: null,
    });
    expect(screen.getByText('Caption mit Frage: funktioniert gerade.')).toBeTruthy();
    expect(screen.getByText('Gerade besser: Kurze Caption.')).toBeTruthy();
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

  it('das Beispiel fuellt das Formular — der leere Zustand erklaert sich selbst', async () => {
    endpoints.health.mockResolvedValue({ features: { post_check: true } });
    render(<PostCheckBlock />);
    await screen.findByText('Entwurf prüfen, bevor er rausgeht');
    fireEvent.click(screen.getByText('Mit einem Beispiel ausprobieren'));
    expect(screen.getByLabelText('Caption-Entwurf').value).toMatch(/DIE NACHT/);
    expect(screen.getByText('Entwurf prüfen').disabled).toBe(false);
  });
});
