// Playbook-Test-Mail-Sektion (21.08.2026) — Wolfs Pruef-Weg ohne curl.
// Der Button uebersetzt die Endpoint-Antwort in einen Klartext-Satz;
// auf Staging (emails_disabled) warnt er, dass nichts ankam.
import React from 'react';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api/client', () => ({
  endpoints: {
    adminPlaybookMailTest: vi.fn(),
    adminPatternBriefingGenerate: vi.fn(),
    adminPatternBriefingLatest: vi.fn(),
  },
}));

import { endpoints } from '../api/client';
import { BriefingSection, PlaybookMailSection } from './MonitoringPanel';

afterEach(() => { cleanup(); vi.clearAllMocks(); });

describe('PlaybookMailSection', () => {
  it('meldet den Versand in Klartext', async () => {
    endpoints.adminPlaybookMailTest.mockResolvedValue({
      skipped: false, sent: 1, failed: 0, emails_disabled: false,
    });
    render(<PlaybookMailSection />);
    fireEvent.click(screen.getByText('Test-Mail jetzt senden'));
    await screen.findByText('Mail gesendet an 1 Empfänger.');
  });

  it('warnt auf Staging, dass der Mailer abgeschaltet ist', async () => {
    endpoints.adminPlaybookMailTest.mockResolvedValue({
      skipped: false, sent: 1, failed: 0, emails_disabled: true,
    });
    render(<PlaybookMailSection />);
    fireEvent.click(screen.getByText('Test-Mail jetzt senden'));
    await screen.findByText(/es kam nichts an. Diesen Test in Produktion ausführen/);
  });

  it('erklaert fehlende Empfaenger mit dem Variablen-Namen', async () => {
    endpoints.adminPlaybookMailTest.mockResolvedValue({
      skipped: true, reason: 'no_recipients', sent: 0, failed: 0,
    });
    render(<PlaybookMailSection />);
    fireEvent.click(screen.getByText('Test-Mail jetzt senden'));
    await screen.findByText(/PLAYBOOK_MAIL_RECIPIENTS in den Railway-Variablen pflegen/);
  });
});

describe('BriefingSection', () => {
  // Default: fuer die Ebene liegt noch nichts vor — der 404-Text des
  // Endpoints. Tests mit Anzeige-Daten ueberschreiben das gezielt.
  beforeEach(() => {
    endpoints.adminPatternBriefingLatest.mockRejectedValue(
      new Error('{"detail":"Noch kein Pattern-Briefing (genre) persistiert."}'),
    );
  });

  it('generiert die Titel-Ebene und meldet Bausteine samt Kosten', async () => {
    endpoints.adminPatternBriefingGenerate.mockResolvedValue({
      mode: 'title', iso_year: 2026, iso_week: 34, model: 'claude-x',
      bausteine: 3, citation_dropped: 1, llm_output_present: true,
      cost_usd_cents: 7,
    });
    render(<BriefingSection />);
    fireEvent.click(screen.getByText('Titel-Ebene generieren'));
    await screen.findByText(/3 Baustein\(e\) für KW 34\/2026 erstellt \(7 Cent\)/);
    expect(screen.getByText(/1 Baustein\(e\) verworfen, weil Belege fehlten/)).toBeTruthy();
    expect(endpoints.adminPatternBriefingGenerate).toHaveBeenCalledWith({ mode: 'title' });
  });

  it('erklaert den kostenfreien Leerlauf ohne Muster', async () => {
    endpoints.adminPatternBriefingGenerate.mockResolvedValue({
      mode: 'genre', iso_year: 2026, iso_week: 34, model: 'none',
      bausteine: 0, citation_dropped: 0, llm_output_present: false,
      cost_usd_cents: 0,
    });
    render(<BriefingSection />);
    fireEvent.click(screen.getByText('Genre-Ebene generieren'));
    await screen.findByText(/kein LLM-Aufruf, keine Kosten/);
  });

  it('zeigt das Ergebnis nach der Generierung direkt an', async () => {
    endpoints.adminPatternBriefingGenerate.mockResolvedValue({
      mode: 'genre', iso_year: 2026, iso_week: 34, model: 'claude-x',
      bausteine: 1, citation_dropped: 0, llm_output_present: true,
      cost_usd_cents: 6,
    });
    endpoints.adminPatternBriefingLatest.mockResolvedValue({
      mode: 'genre', iso_year: 2026, iso_week: 34, model: 'claude-x',
      llm_output: {
        bausteine: [{
          muster: 'Romance × Cover mit Titel-Tafel',
          begruendung: 'Liegt 3,2-mal öfter weit über dem Kanal-Schnitt.',
          hooks_de: ['Dieser Blick sagt alles.'],
          hooks_en: ['One look. That is the whole story.'],
          captions_de: ['[TITEL] — ab Freitag im Kino.'],
          captions_en: ['[TITLE] — in theaters Friday.'],
          hashtags: ['romance', 'kino'],
          cited_post_ids: ['https://x.test/p/1', 'https://x.test/p/2'],
        }],
        data_caveats: ['Nur 32 % Cover-Abdeckung.'],
      },
    });
    render(<BriefingSection />);
    fireEvent.click(screen.getByText('Genre-Ebene generieren'));
    await screen.findByText('Romance × Cover mit Titel-Tafel');
    expect(endpoints.adminPatternBriefingLatest).toHaveBeenCalledWith({ mode: 'genre' });
    expect(screen.getByText('Dieser Blick sagt alles.')).toBeTruthy();
    expect(screen.getByText('#romance #kino')).toBeTruthy();
    expect(screen.getByText('Post 1').getAttribute('href')).toBe('https://x.test/p/1');
    expect(screen.getByText(/Genre-Ebene · KW 34\/2026 · Modell: claude-x/)).toBeTruthy();
    expect(screen.getByText(/Nur 32 % Cover-Abdeckung/)).toBeTruthy();
  });

  it('holt das letzte Ergebnis der Titel-Ebene ohne neuen Lauf', async () => {
    endpoints.adminPatternBriefingLatest.mockResolvedValue({
      mode: 'title', iso_year: 2026, iso_week: 33, model: 'claude-x',
      llm_output: { bausteine: [], data_caveats: [] },
    });
    render(<BriefingSection />);
    fireEvent.click(screen.getByText('Letztes Ergebnis: Titel'));
    await screen.findByText(/keine Bausteine hinterlassen/);
    expect(endpoints.adminPatternBriefingLatest).toHaveBeenCalledWith({ mode: 'title' });
    expect(endpoints.adminPatternBriefingGenerate).not.toHaveBeenCalled();
  });

  it('erklaert eine Ebene ohne gespeichertes Briefing in Klartext', async () => {
    render(<BriefingSection />);
    fireEvent.click(screen.getByText('Letztes Ergebnis: Genre'));
    await screen.findByText(/wurde noch kein Briefing generiert/);
  });
});
