// Trailer-Intelligence Stufe 1 (20.08.2026) — das Flag-Verhalten des
// Muster-Blocks. Die zentrale Zusage: derselbe main-Build zeigt auf
// Produktion NICHTS von Stufe 1 (health meldet das Feature nicht),
// auf Staging den Bericht. Der Block entscheidet das zur Laufzeit
// ueber /api/health — nicht ueber eine Build-Variable.
//
// Die Daten-Fixtures folgen dem ECHTEN Berichts-Format von
// services/trailer_patterns.py (report.to_dict) — derselbe Shape wie
// GET /api/admin/patterns.
//
// React wird ausdruecklich importiert: klassische JSX-Transformation.
import React from 'react';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('./api/client', () => ({
  endpoints: {
    health: vi.fn(),
    insightPatterns: vi.fn(),
    insightPatternBriefing: vi.fn(),
  },
}));

import { endpoints } from './api/client';
import PatternsBlock from './PatternsBlock';

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

// Default: noch kein Briefing persistiert (404) — der NORMALE Zustand,
// solange der Montags-Cron das erste Mal noch nicht gelaufen ist.
beforeEach(() => {
  endpoints.insightPatternBriefing.mockRejectedValue(new Error('404'));
});

const ZELLE_OK = {
  value: 'Romance', sample_size: 42, channel_count: 6,
  median_lift: 1.12, median_activation: 0.021, median_views: 120000,
  verdict: 'neutral', breakout_rate: 0.19, expected_breakout_rate: 0.11,
  breakout_z: 2.4, breakout_verdict: 'over', p90_lift: 2.6,
  platform_mix: { instagram: 30, tiktok: 12 },
};
const ZELLE_DUENN = {
  value: 'Western', sample_size: 3, channel_count: 1,
  median_lift: 4.0, median_activation: 0.09, median_views: 500,
  verdict: 'insufficient', breakout_rate: 0.0, expected_breakout_rate: 0.0,
  breakout_z: null, breakout_verdict: 'insufficient', p90_lift: 4.0,
  platform_mix: { tiktok: 3 }, reason: 'nur 3 Posts (Minimum 5)',
};
const DATEN = {
  window_days: 90, market: null, format_class: null,
  posts_in_window: 900, posts_with_baseline: 700, channels_covered: 14,
  analysis_coverage: 0.4, baseline_breakout_rate: 0.1,
  platform_breakout_rates: { instagram: 0.08, tiktok: 0.14 },
  dimensions: { genre: [ZELLE_OK, ZELLE_DUENN], format: [] },
  notes: ['Genre-Abdeckung liegt bei 12 % — Genres kommen aus TMDb.'],
};

describe('PatternsBlock — Flag-Gate und Bericht', () => {
  it('rendert NICHTS, wenn health das Feature nicht meldet (Produktion)', async () => {
    endpoints.health.mockResolvedValue({ features: { trailer_intelligence: false } });
    const { container } = render(<PatternsBlock />);
    await waitFor(() => expect(endpoints.health).toHaveBeenCalled());
    expect(container.innerHTML).toBe('');
    expect(endpoints.insightPatterns).not.toHaveBeenCalled();
  });

  it('rendert auch bei kaputtem health nichts — Produktion sieht nie einen Fehlerkasten', async () => {
    endpoints.health.mockRejectedValue(new Error('boom'));
    const { container } = render(<PatternsBlock />);
    await waitFor(() => expect(endpoints.health).toHaveBeenCalled());
    expect(container.innerHTML).toBe('');
  });

  it('zeigt mit Flag die Zellen mit Befund — insufficient nur als Zaehler', async () => {
    endpoints.health.mockResolvedValue({ features: { trailer_intelligence: true } });
    endpoints.insightPatterns.mockResolvedValue(DATEN);
    render(<PatternsBlock />);

    await screen.findByText('Romance');
    expect(screen.getByText('läuft über Schnitt')).toBeTruthy();
    // Die duenne Zelle steht NICHT in der Tabelle — auch nicht mit
    // ihrem Traum-Lift von 4x. Sie erscheint nur im Zaehler.
    expect(screen.queryByText('Western')).toBeNull();
    expect(screen.getByText(/1 weitere unter der Stichproben-Schwelle/)).toBeTruthy();
  });

  it('zeigt die notes des Berichts ungekuerzt — Abdeckungs-Warnungen sind Teil des Ergebnisses', async () => {
    endpoints.health.mockResolvedValue({ features: { trailer_intelligence: true } });
    endpoints.insightPatterns.mockResolvedValue(DATEN);
    render(<PatternsBlock />);

    await screen.findByText(/Genre-Abdeckung liegt bei 12 %/);
    expect(screen.getByText(/700 Posts mit Kanal-Baseline/)).toBeTruthy();
  });

  it('sagt es ehrlich, wenn kein Muster belastbar ist', async () => {
    endpoints.health.mockResolvedValue({ features: { trailer_intelligence: true } });
    endpoints.insightPatterns.mockResolvedValue({
      ...DATEN, dimensions: { genre: [ZELLE_DUENN] }, notes: [],
    });
    render(<PatternsBlock />);

    await screen.findByText(/Noch kein Muster mit ausreichender Stichprobe/);
  });

  it('zeigt die Text-Bausteine mit Hooks, Captions und Belegen', async () => {
    endpoints.health.mockResolvedValue({ features: { trailer_intelligence: true } });
    endpoints.insightPatterns.mockResolvedValue(DATEN);
    endpoints.insightPatternBriefing.mockResolvedValue({
      mode: 'genre', iso_year: 2026, iso_week: 34, window_days: 90,
      generated_at: '2026-08-24T06:30:00+00:00', model_used: true,
      citation_dropped: 1,
      llm_output: {
        bausteine: [{
          muster: 'Romance auf TikTok',
          begruendung: '19,0 % Breakout-Quote bei erwarteten 11,0 %.',
          hooks_de: ['Noch 3 Tage.'],
          hooks_en: ['3 days left.'],
          captions_de: ['[TITEL] — ab Donnerstag im Kino.'],
          captions_en: ['[TITLE] — in theaters Thursday.'],
          hashtags: ['kino'],
          cited_post_ids: ['https://x.test/p/1', 'https://x.test/p/2'],
        }],
        data_caveats: ['Western zu dünn belegt — kein Baustein geliefert.'],
      },
    });
    render(<PatternsBlock />);

    await screen.findByText('Text-Bausteine aus den Mustern');
    expect(screen.getByText('Romance auf TikTok')).toBeTruthy();
    expect(screen.getByText('Noch 3 Tage.')).toBeTruthy();
    expect(screen.getByText('[TITLE] — in theaters Thursday.')).toBeTruthy();
    // Belege sind klickbare Links auf die Original-Posts.
    expect(screen.getByText('Post 1').getAttribute('href')).toBe('https://x.test/p/1');
    // Caveats und der Citation-Drop-Hinweis bleiben sichtbar —
    // Transparenz ist Teil des Ergebnisses, wie bei den notes.
    expect(screen.getByText(/Western zu dünn belegt/)).toBeTruthy();
    expect(screen.getByText(/1 Baustein\(e\) wurden verworfen/)).toBeTruthy();
  });

  it('blendet die Baustein-Sektion ohne Briefing still aus (404 ist kein Fehler)', async () => {
    endpoints.health.mockResolvedValue({ features: { trailer_intelligence: true } });
    endpoints.insightPatterns.mockResolvedValue(DATEN);
    // beforeEach: insightPatternBriefing rejected — wie vor dem ersten
    // Montags-Cron-Lauf.
    render(<PatternsBlock />);

    await screen.findByText('Romance');
    expect(screen.queryByText('Text-Bausteine aus den Mustern')).toBeNull();
    // Und vor allem: KEIN Fehlerkasten — die Muster-Tabellen stehen.
    expect(screen.queryByText(/Muster momentan nicht verfügbar/)).toBeNull();
  });
});
