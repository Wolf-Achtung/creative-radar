// Playbook-Test-Mail-Sektion (21.08.2026) — Wolfs Pruef-Weg ohne curl.
// Der Button uebersetzt die Endpoint-Antwort in einen Klartext-Satz;
// auf Staging (emails_disabled) warnt er, dass nichts ankam.
import React from 'react';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api/client', () => ({
  endpoints: { adminPlaybookMailTest: vi.fn(), adminPatternBriefingGenerate: vi.fn() },
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
});
