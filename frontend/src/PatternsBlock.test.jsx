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
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('./api/client', () => ({
  apiUrl: (path) => `https://api.test${path}`,
  endpoints: {
    health: vi.fn(),
    insightPatterns: vi.fn(),
    insightPatternBriefing: vi.fn(),
    insightPatternExamples: vi.fn(),
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
  endpoints.insightPatternExamples.mockResolvedValue({ examples: [] });
});

// Seit dem Empfehlungs-Tab (20.08.2026) ist die Zahlen-Ansicht der
// ZWEITE Tab — Tests, die Tabellen oder Mess-Karten pruefen, wechseln
// zuerst dorthin.
async function zuZahlen() {
  fireEvent.click(await screen.findByText('Zahlen & Methode'));
}

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
    await zuZahlen();

    await screen.findAllByText('Romance');
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
    await zuZahlen();

    await screen.findByText(/Genre-Abdeckung liegt bei 12 %/);
    expect(screen.getByText(/700 Posts mit Kanal-Baseline/)).toBeTruthy();
  });

  it('sagt es ehrlich, wenn kein Muster belastbar ist', async () => {
    endpoints.health.mockResolvedValue({ features: { trailer_intelligence: true } });
    endpoints.insightPatterns.mockResolvedValue({
      ...DATEN, dimensions: { genre: [ZELLE_DUENN] }, notes: [],
    });
    render(<PatternsBlock />);
    await zuZahlen();

    await screen.findByText(/Noch kein Muster mit ausreichender Stichprobe/);
  });

  it('faerbt die Breakout-Quote nach Befund — nicht pauschal rot', async () => {
    // Regression 20.08.2026: die Quote-Spalte trug die Immer-Rot-Klasse
    // des Breakouts-Blocks — 20 % UEBER Schnitt sah aus wie eine
    // Warnung. Gruen fuer over, Rot nur fuer under.
    endpoints.health.mockResolvedValue({ features: { trailer_intelligence: true } });
    endpoints.insightPatterns.mockResolvedValue({
      ...DATEN,
      dimensions: {
        genre: [
          ZELLE_OK,
          { ...ZELLE_OK, value: 'Horror', breakout_rate: 0.05, breakout_verdict: 'under' },
        ],
      },
    });
    render(<PatternsBlock />);
    await zuZahlen();

    const ueber = await screen.findByText('19.0 %');
    expect(ueber.style.color).toBe('rgb(31, 122, 69)');
    expect(ueber.className).not.toContain('breakouts-multiplier');
    const unter = screen.getByText('5.0 %');
    expect(unter.style.color).toBe('rgb(176, 61, 46)');
  });

  it('zeigt die staerksten Befunde als Klartext-Karten — nach Signalstaerke, ohne unauffaellige', async () => {
    // Aufwertung A (20.08.2026): ueber den Tabellen stehen Karten mit
    // Handlung + Zahlen. Nur over/under, |z|-sortiert ueber ALLE
    // Dimensionen — die staerkste Karte zuerst, unauffaellige Zellen
    // erzeugen KEINE Karte.
    endpoints.health.mockResolvedValue({ features: { trailer_intelligence: true } });
    endpoints.insightPatterns.mockResolvedValue({
      ...DATEN,
      dimensions: {
        genre: [
          { ...ZELLE_OK, value: 'Romance', breakout_z: 2.4, breakout_verdict: 'over' },
          { ...ZELLE_OK, value: 'Drama', breakout_z: 0.4, breakout_verdict: 'neutral' },
        ],
        lifecycle_stage: [
          { ...ZELLE_OK, value: 'evergreen', breakout_rate: 0.05, breakout_z: -3.4, breakout_verdict: 'under' },
        ],
      },
    });
    render(<PatternsBlock />);
    await zuZahlen();

    await screen.findByText('Das Wichtigste zuerst');
    // evergreen (|z|=3.4) schlaegt Romance (2.4) — und traegt den
    // Anzeige-Namen samt Sparsam-Empfehlung.
    const karten = screen.getAllByText(/Mehr davon testen|Sparsam einsetzen/);
    expect(karten).toHaveLength(2);
    expect(karten[0].textContent).toBe('Sparsam einsetzen');
    expect(screen.getByText('Evergreen')).toBeTruthy();
    expect(screen.getByText(/1\.7× so oft Ausreißer/)).toBeTruthy();
    expect(screen.getByText(/kein Beweis für Ursache und Wirkung/)).toBeTruthy();
    // Drama (neutral) bekommt keine Karte — der Wert steht nur in der
    // Tabelle (dort mit Aufklapp-Pfeil davor).
    expect(screen.getAllByText(/Drama/)).toHaveLength(1);
  });

  it('klappt eine Zeile auf und laedt die Beispiel-Posts dahinter', async () => {
    // Aufwertung B (20.08.2026): Klick auf eine Muster-Zeile zeigt die
    // staerksten Posts der Zelle — Referenzmaterial statt nur Statistik.
    // Geladen wird erst beim Aufklappen, mit Dimension + Auspraegung.
    endpoints.health.mockResolvedValue({ features: { trailer_intelligence: true } });
    endpoints.insightPatterns.mockResolvedValue(DATEN);
    endpoints.insightPatternExamples.mockResolvedValue({
      dimension: 'genre', value: 'Romance', window_days: 90, cell_size: 42,
      examples: [{
        post_url: 'https://x.test/p/1', platform: 'tiktok', channel_handle: 'disneyde',
        lift: 3.4, views: 120000, caption: 'Der Moment, wenn …', detected_at: '2026-08-01',
      }],
    });
    render(<PatternsBlock />);
    await zuZahlen();

    await screen.findAllByText(/Romance/);
    // Die Empfehlungs-Karten des ersten Tabs laden ihre Referenz-Posts
    // mit limit 3 — ein ZEILEN-Fetch (limit 5) darf vor dem Klick nicht
    // passiert sein.
    expect(
      endpoints.insightPatternExamples.mock.calls.filter(([a]) => a.limit !== 3),
    ).toHaveLength(0);
    fireEvent.click(screen.getByText(/▸ Romance/));

    await screen.findByText('Post öffnen');
    // Beispiel OHNE asset_id: kein <img>, keine kaputte Platzhalter-URL.
    expect(document.querySelector('img')).toBeNull();
    expect(endpoints.insightPatternExamples).toHaveBeenCalledWith({
      dimension: 'genre', value: 'Romance',
    });
    expect(screen.getByText('3.4x')).toBeTruthy();
    expect(screen.getByText(/Der Moment, wenn/)).toBeTruthy();
    expect(screen.getByText('Post öffnen').getAttribute('href')).toBe('https://x.test/p/1');

    // Zweiter Klick klappt zu — ohne neuen Request.
    fireEvent.click(screen.getByText(/▾ Romance/));
    expect(screen.queryByText('Post öffnen')).toBeNull();
    // Genau EIN Zeilen-Fetch (limit 5) — zu- und wieder aufklappen
    // laedt nicht erneut. Die Karten-Fetches (limit 3) zaehlen nicht.
    expect(
      endpoints.insightPatternExamples.mock.calls.filter(([a]) => a.limit !== 3),
    ).toHaveLength(1);
  });

  it('zeigt in der aufgeklappten Zeile eine Leer-Meldung, wenn Beispiele fehlschlagen', async () => {
    endpoints.health.mockResolvedValue({ features: { trailer_intelligence: true } });
    endpoints.insightPatterns.mockResolvedValue(DATEN);
    endpoints.insightPatternExamples.mockRejectedValue(new Error('boom'));
    render(<PatternsBlock />);
    await zuZahlen();

    await screen.findAllByText(/Romance/);
    fireEvent.click(screen.getByText(/▸ Romance/));
    await screen.findByText('Keine Beispiel-Posts verfügbar.');
  });

  it('zeigt den Wochen-Trend in der Befund-Spalte', async () => {
    // Aufwertung C (20.08.2026): 'neu' = Vorwoche fehlte oder war
    // duenn; 'gewechselt' nennt das Vorwochen-Verdikt. Zellen ohne
    // trend-Feld (aeltere Backends) rendern unveraendert.
    endpoints.health.mockResolvedValue({ features: { trailer_intelligence: true } });
    endpoints.insightPatterns.mockResolvedValue({
      ...DATEN,
      dimensions: {
        genre: [
          { ...ZELLE_OK, value: 'Romance', trend: 'neu', vorwoche: null },
          {
            ...ZELLE_OK, value: 'Horror', breakout_rate: 0.05,
            trend: 'gewechselt',
            vorwoche: { breakout_rate: 0.12, breakout_verdict: 'neutral' },
          },
        ],
      },
    });
    render(<PatternsBlock />);
    await zuZahlen();

    await screen.findAllByText(/neu belastbar/);
    expect(screen.getByText(/Vorwoche: unauffällig/)).toBeTruthy();
  });

  it('zeigt Bewegung diese Woche: Verdikt-Wechsel vor Neuzugaengen, stabil und duenn nie', async () => {
    // Radar (20.08.2026): nur echte Bewegung ist eine Nachricht. Ein
    // Wechsel steht vor einem Neuzugang, auch wenn der Neuzugang das
    // staerkere |z| hat; "stabil", "neu + unauffaellig" und
    // insufficient-Zellen erscheinen nicht.
    endpoints.health.mockResolvedValue({ features: { trailer_intelligence: true } });
    endpoints.insightPatterns.mockResolvedValue({
      ...DATEN,
      dimensions: {
        format: [
          { ...ZELLE_OK, value: 'behind_the_scenes', breakout_z: 2.6, breakout_verdict: 'over',
            trend: 'gewechselt', vorwoche: { breakout_rate: 0.149, breakout_verdict: 'neutral' } },
          { ...ZELLE_OK, value: 'clip', breakout_z: -9.9, breakout_verdict: 'under',
            trend: 'neu', vorwoche: null },
          { ...ZELLE_OK, value: 'promo', breakout_z: 0.2, breakout_verdict: 'neutral',
            trend: 'neu', vorwoche: null },
          { ...ZELLE_OK, value: 'trailer', breakout_z: 2.2, breakout_verdict: 'over',
            trend: 'stabil', vorwoche: { breakout_rate: 0.18, breakout_verdict: 'over' } },
        ],
      },
    });
    render(<PatternsBlock />);
    await zuZahlen();

    await screen.findByText('Bewegung diese Woche');
    const chips = screen.getAllByText(/Verdikt gewechselt|Neu belastbar/);
    expect(chips).toHaveLength(2);
    // Wechsel (z=2.6) VOR Neuzugang (z=-9.9) — Sortierung nach Art, nicht |z|.
    expect(chips[0].textContent).toBe('Verdikt gewechselt');
    expect(screen.getByText(/unauffällig → läuft über Schnitt \(Quote 19\.0 % nach 14\.9 %\)/)).toBeTruthy();
    expect(screen.getByText(/neu belastbar — läuft unter Schnitt/)).toBeTruthy();
  });

  it('Fokus-Ansicht: Dimensionen ohne Befund klappen zu einer Zeile zusammen', async () => {
    // UX-Runde 20.08.2026: elf Tabellen voller "unauffaellig" sind
    // Rauschen. Ohne Befund -> eine Zeile; Klick klappt die Tabelle
    // auf. Die Befund-Tabelle (genre) bleibt immer offen.
    endpoints.health.mockResolvedValue({ features: { trailer_intelligence: true } });
    endpoints.insightPatterns.mockResolvedValue({
      ...DATEN,
      dimensions: {
        genre: [ZELLE_OK],
        tone: [
          { ...ZELLE_OK, value: 'emotional', breakout_verdict: 'neutral', breakout_z: 1.1 },
          { ...ZELLE_OK, value: 'edgy', breakout_verdict: 'neutral', breakout_z: -1.0 },
        ],
      },
    });
    render(<PatternsBlock />);
    await zuZahlen();

    await screen.findAllByText('Romance');
    expect(screen.queryByText(/emotional/)).toBeNull();
    const zeile = screen.getByText(/Tonalität — 2 Ausprägungen, alle unauffällig/);
    fireEvent.click(zeile);
    expect(screen.getByText(/emotional/)).toBeTruthy();
  });

  it('Alle Zahlen zeigen stellt die Vollansicht her — und laesst sich zuruecknehmen', async () => {
    endpoints.health.mockResolvedValue({ features: { trailer_intelligence: true } });
    endpoints.insightPatterns.mockResolvedValue({
      ...DATEN,
      dimensions: {
        genre: [ZELLE_OK],
        tone: [{ ...ZELLE_OK, value: 'emotional', breakout_verdict: 'neutral', breakout_z: 1.1 }],
      },
    });
    render(<PatternsBlock />);
    await zuZahlen();

    await screen.findAllByText('Romance');
    fireEvent.click(screen.getByText('Alle Zahlen zeigen (1 weitere Dimensionen)'));
    expect(screen.getByText(/emotional/)).toBeTruthy();
    fireEvent.click(screen.getByText('Nur Befunde zeigen'));
    expect(screen.queryByText(/emotional/)).toBeNull();
  });

  it('Median-Lift steht nicht mehr in der User-Tabelle', async () => {
    // Die Spalte zeigt konstruktionsbedingt fast ueberall 1,00x (Lift
    // ist am Kanal-Median normiert) — fuer User nur Rauschen. Der
    // Admin-Endpoint behaelt den Wert.
    endpoints.health.mockResolvedValue({ features: { trailer_intelligence: true } });
    endpoints.insightPatterns.mockResolvedValue(DATEN);
    render(<PatternsBlock />);
    await zuZahlen();

    await screen.findAllByText('Romance');
    expect(screen.queryByText('Median-Lift')).toBeNull();
    expect(screen.queryByText('1.12x')).toBeNull();
  });

  it('erklaert die Lesart hinter einem "Wie lese ich das?"-Aufklapper', async () => {
    endpoints.health.mockResolvedValue({ features: { trailer_intelligence: true } });
    endpoints.insightPatterns.mockResolvedValue(DATEN);
    render(<PatternsBlock />);
    await zuZahlen();

    await screen.findAllByText('Romance');
    expect(screen.getByText('Wie lese ich das?')).toBeTruthy();
    expect(screen.getByText(/der Strich die\s+erwartete/)).toBeTruthy();
  });

  it('zeichnet je Zeile einen Delta-Balken: Fuellung = Quote, Strich = erwartet', async () => {
    endpoints.health.mockResolvedValue({ features: { trailer_intelligence: true } });
    endpoints.insightPatterns.mockResolvedValue(DATEN);
    render(<PatternsBlock />);
    await zuZahlen();

    await screen.findAllByText('Romance');
    const balken = screen.getByTitle('19.0 % gegen 11.0 % erwartet');
    const [fuellung, marker] = balken.children;
    // Skala der Genre-Tabelle: max(0.19, 0.11) * 1.15 = 0.2185 →
    // Fuellung 19/21.85 = 87.0 %, Marker bei 11/21.85 = 50.3 %.
    expect(fuellung.style.width).toBe('87%');
    expect(fuellung.style.background).toBe('rgb(31, 122, 69)');
    expect(marker.style.left).toBe('50.3%');
  });


  it('Empfehlungen sind der Standard-Tab: Werkstatt-Sprache statt Mess-Sprache, Referenz-Posts direkt sichtbar', async () => {
    // Praxis-Modus (20.08.2026): der erste Blick beantwortet "was mache
    // ich anders?" — Titel aus der Werkstatt-Vorlage, keine Tabellen,
    // die drei staerksten Referenz-Posts ohne weiteren Klick.
    endpoints.health.mockResolvedValue({ features: { trailer_intelligence: true } });
    endpoints.insightPatterns.mockResolvedValue({
      ...DATEN,
      dimensions: {
        cover_kinetik: [{
          ...ZELLE_OK, value: 'title_card',
          breakout_rate: 0.232, expected_breakout_rate: 0.141,
          breakout_z: 4.3, breakout_verdict: 'over',
          sample_size: 272, channel_count: 118,
        }],
      },
    });
    endpoints.insightPatternExamples.mockResolvedValue({
      examples: [{
        post_url: 'https://x.test/p/9', platform: 'tiktok',
        channel_handle: 'disneyde', lift: 3.1, views: 90000,
        caption: 'x', detected_at: '2026-08-10',
        asset_id: 'aaaa1111-2222-3333-4444-555566667777',
      }],
    });
    render(<PatternsBlock />);

    await screen.findByText('Cover mit Titel-Tafel bauen');
    // Themen-Chip in Alltagssprache — nicht der Dimensions-Name
    // "Cover: Bildtext & Kinetik".
    expect(screen.getByText('· Cover')).toBeTruthy();
    expect(screen.queryByText(/Bildtext & Kinetik/)).toBeNull();
    expect(screen.getByText(/1\.6-mal öfter weit über dem Kanal-Schnitt/)).toBeTruthy();
    expect(screen.getByText(/Basis: 272 Posts von 118 Kanälen/)).toBeTruthy();
    // Referenz-Posts: direkt geladen (limit 3) und sichtbar. Der
    // Warte-Punkt 'Post ansehen' garantiert, dass der Effect-Fetch
    // gelaufen ist — die Assertion davor war in CI flaky (der Karten-
    // Titel erscheint einen Tick, bevor der Kind-Effect feuert).
    await screen.findByText('Post ansehen');
    expect(endpoints.insightPatternExamples).toHaveBeenCalledWith(
      { dimension: 'cover_kinetik', value: 'title_card', limit: 3 },
    );
    expect(screen.getByText('3.1x')).toBeTruthy();
    // Thumbnail ueber den auth-whitelisted Proxy — und bei Ladefehler
    // verschwindet das Bild still (kein kaputtes Icon).
    const bild = document.querySelector('img');
    expect(bild.src).toBe('https://api.test/api/thumbnails/aaaa1111-2222-3333-4444-555566667777');
    fireEvent.error(bild);
    expect(bild.style.display).toBe('none');
    // Keine Tabellen, keine Mess-Karten im Standard-Tab.
    expect(screen.queryByText('Breakout-Quote')).toBeNull();
    expect(screen.queryByText('Das Wichtigste zuerst')).toBeNull();
  });

  it('Werkstatt-Fallback: unbekannte Befunde bekommen einen generischen, ehrlichen Satz', async () => {
    endpoints.health.mockResolvedValue({ features: { trailer_intelligence: true } });
    endpoints.insightPatterns.mockResolvedValue(DATEN);
    render(<PatternsBlock />);

    // Romance (over) hat keine Werkstatt-Vorlage — Titel aus dem
    // Fallback, mit Faktor aus den Zahlen (0.19/0.11 = 1.7).
    await screen.findByText('Romance: öfter testen');
    expect(screen.getByText(/1\.7-mal öfter weit über dem Kanal-Schnitt/)).toBeTruthy();
  });

  it('Copy-Button kopiert einen Baustein-Hook in die Zwischenablage', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText }, configurable: true,
    });
    endpoints.health.mockResolvedValue({ features: { trailer_intelligence: true } });
    endpoints.insightPatterns.mockResolvedValue(DATEN);
    endpoints.insightPatternBriefing.mockImplementation(({ mode } = {}) => (
      mode === 'genre'
        ? Promise.resolve({
          mode: 'genre', iso_year: 2026, iso_week: 34, window_days: 90,
          citation_dropped: 0,
          llm_output: {
            bausteine: [{
              muster: 'Romance auf TikTok', begruendung: 'x',
              hooks_de: ['Noch 3 Tage.'], hooks_en: [],
              captions_de: [], captions_en: [], hashtags: [],
              cited_post_ids: [],
            }],
            data_caveats: [],
          },
        })
        : Promise.reject(new Error('404'))
    ));
    render(<PatternsBlock />);

    await screen.findByText('Noch 3 Tage.');
    fireEvent.click(screen.getAllByText('kopieren')[0]);
    expect(writeText).toHaveBeenCalledWith('Noch 3 Tage.');
    await screen.findByText('✓ kopiert');
  });

  it('zeigt die Text-Bausteine mit Hooks, Captions und Belegen', async () => {
    endpoints.health.mockResolvedValue({ features: { trailer_intelligence: true } });
    endpoints.insightPatterns.mockResolvedValue(DATEN);
    // Mode-scharfer Mock: NUR die Genre-Ebene liefert — die Sektion darf
    // nicht doppelt erscheinen, nur weil das Panel zwei Ebenen abfragt.
    endpoints.insightPatternBriefing.mockImplementation(({ mode } = {}) => (
      mode === 'genre'
        ? Promise.resolve({
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
        })
        : Promise.reject(new Error('404'))
    ));
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
    // Die Titel-Ebene hat 404 geliefert — ihre Sektion existiert nicht.
    expect(screen.queryByText('Text-Bausteine je Titel')).toBeNull();
  });

  it('zeigt die Titel-Bausteine als eigene Sektion', async () => {
    endpoints.health.mockResolvedValue({ features: { trailer_intelligence: true } });
    endpoints.insightPatterns.mockResolvedValue(DATEN);
    endpoints.insightPatternBriefing.mockImplementation(({ mode } = {}) => (
      mode === 'title'
        ? Promise.resolve({
          mode: 'title', iso_year: 2026, iso_week: 34, window_days: 90,
          generated_at: '2026-08-24T06:30:00+00:00', model_used: true,
          citation_dropped: 0,
          llm_output: {
            bausteine: [{
              muster: 'Wicked auf TikTok',
              begruendung: '25,0 % Breakout-Quote bei erwarteten 12,0 %.',
              hooks_de: ['Der Countdown läuft.'],
              hooks_en: ['The countdown is on.'],
              captions_de: ['Wicked — ab Donnerstag im Kino.'],
              captions_en: ['Wicked — in theaters Thursday.'],
              hashtags: ['wicked'],
              cited_post_ids: ['https://x.test/p/9'],
            }],
            data_caveats: [],
          },
        })
        : Promise.reject(new Error('404'))
    ));
    render(<PatternsBlock />);

    await screen.findByText('Text-Bausteine je Titel');
    expect(screen.getByText('Wicked auf TikTok')).toBeTruthy();
    // Captions der Titel-Ebene tragen den ECHTEN Titel, keinen
    // [TITEL]-Platzhalter.
    expect(screen.getByText('Wicked — ab Donnerstag im Kino.')).toBeTruthy();
    // Die Genre-Ebene hat 404 geliefert — ihre Sektion existiert nicht.
    expect(screen.queryByText('Text-Bausteine aus den Mustern')).toBeNull();
  });

  it('blendet die Baustein-Sektion ohne Briefing still aus (404 ist kein Fehler)', async () => {
    endpoints.health.mockResolvedValue({ features: { trailer_intelligence: true } });
    endpoints.insightPatterns.mockResolvedValue(DATEN);
    // beforeEach: insightPatternBriefing rejected — wie vor dem ersten
    // Montags-Cron-Lauf.
    render(<PatternsBlock />);
    await zuZahlen();

    await screen.findAllByText('Romance');
    expect(screen.queryByText('Text-Bausteine aus den Mustern')).toBeNull();
    // Und vor allem: KEIN Fehlerkasten — die Muster-Tabellen stehen.
    expect(screen.queryByText(/Muster momentan nicht verfügbar/)).toBeNull();
  });
});
