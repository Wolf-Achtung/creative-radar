// Wir-Projekte (22.08.2026) — Wolfs Befund: Trailerhaus betreut keine
// kompletten Kunden-Kanaele, sondern arbeitet projektweise. Die Wir-
// Markierung muss deshalb am TITEL moeglich sein: Umlaut-tolerante
// Suche (bei ~30k Titeln ist eine Checkliste unbenutzbar), Checkbox
// setzt/loescht die Markierung, markierte Projekte bleiben sichtbar.
import React from 'react';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { SourcesPanel } from './SourcesPanel';

afterEach(() => { cleanup(); vi.clearAllMocks(); });

function renderPanel({
  titles = [],
  onToggleTitleOwnProject = () => {},
  startBrief = null,
  onProjektStartBrief = () => {},
  onProjektExport = () => {},
  // Tests laufen per Default wie Staging: Flags an. Das Production-
  // Verhalten (Flags aus -> unsichtbar) hat seinen eigenen Test.
  features = { wir_projekte: true, projekt_start_brief: true },
} = {}) {
  return render(
    <SourcesPanel
      startBrief={startBrief}
      onProjektStartBrief={onProjektStartBrief}
      onProjektExport={onProjektExport}
      features={features}
      busy={false}
      channelFile={null}
      setChannelFile={() => {}}
      onImportChannelFile={() => {}}
      monitorForm={{ max_channels: 5, results_limit_per_channel: 5 }}
      setMonitorForm={() => {}}
      onInstagram={() => {}}
      tiktokForm={{ username: '', results_limit_per_channel: 5 }}
      setTiktokForm={() => {}}
      onTikTok={() => {}}
      whitelistStats={null}
      titleCandidates={[]}
      onSyncTitleSources={() => {}}
      onRematchAssets={() => {}}
      onCandidateAutopilot={() => {}}
      onCandidateLlmAssist={() => {}}
      onToggleChannelOwn={() => {}}
      channels={[]}
      titles={titles}
      onToggleTitleOwnProject={onToggleTitleOwnProject}
      onFullSync={() => {}}
      cronBusy={false}
      cronMessage={null}
      lastCronRun={null}
    />
  );
}

describe('SourcesPanel Wir-Projekte', () => {
  it('ohne Feature-Flags ist der Block unsichtbar — Production-Sicht', () => {
    const markiert = { id: 't1', title_original: 'H wie Habicht', is_own_project: true };
    renderPanel({ titles: [markiert], features: {} });

    expect(screen.queryByText(/Wir-Projekte markieren/)).toBeNull();
    expect(screen.queryByRole('button', { name: 'Start-Brief' })).toBeNull();
  });

  it('mit Wir-Projekte-Flag aber ohne Start-Brief-Flag fehlt nur der Button', () => {
    const markiert = { id: 't1', title_original: 'H wie Habicht', is_own_project: true };
    renderPanel({ titles: [markiert], features: { wir_projekte: true } });

    expect(screen.getByText(/Wir-Projekte markieren/)).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Start-Brief' })).toBeNull();
  });

  it('Umlaut-tolerante Suche findet den Titel, Checkbox markiert ihn', () => {
    const onToggle = vi.fn();
    const titel = { id: 't1', title_original: 'Lügen über meine Mutter', is_own_project: false };
    renderPanel({ titles: [titel], onToggleTitleOwnProject: onToggle });

    fireEvent.change(screen.getByLabelText('Titelsuche für Wir-Projekte'), {
      target: { value: 'lugen uber' },
    });
    fireEvent.click(screen.getByRole('checkbox', { name: /Lügen über meine Mutter/ }));
    expect(onToggle).toHaveBeenCalledWith(titel);
  });

  it('jedes markierte Projekt hat einen Start-Brief-Button', () => {
    const onBrief = vi.fn();
    const markiert = { id: 't1', title_original: 'H wie Habicht', is_own_project: true };
    renderPanel({ titles: [markiert], onProjektStartBrief: onBrief });

    fireEvent.click(screen.getByRole('button', { name: 'Start-Brief' }));
    expect(onBrief).toHaveBeenCalledWith(markiert);
  });

  it('der Start-Brief zeigt Genre-Standort und Empfehlungen mit Referenz-Posts', () => {
    const markiert = { id: 't1', title_original: 'H wie Habicht', is_own_project: true };
    renderPanel({
      titles: [markiert],
      startBrief: {
        titleId: 't1',
        daten: {
          title: { id: 't1', title_original: 'H wie Habicht', genre: 'Drama', genres: ['Drama'] },
          window_days: 90,
          posts_im_fenster: 412,
          genre_standort: { value: 'Drama', verdict: 'neutral', median_lift: 1.02, breakout_z: 0.4, sample_size: 18 },
          empfehlungen: [{
            dimension: 'cover_kinetik', value: 'title_card',
            verdict: 'over', median_lift: 1.18, breakout_z: 3.1, sample_size: 33,
            beispiele: [{
              post_url: 'https://x.test/p/1', handle: 'a24', lift: 3.0,
              caption: 'stark', asset_id: 'asset-1',
            }],
          }],
        },
      },
    });

    expect(screen.getByText(/Start-Brief: H wie Habicht/)).toBeTruthy();
    expect(screen.getByText(/Median-Lift 1.18x/)).toBeTruthy();
    const link = screen.getByTitle('stark');
    expect(link.getAttribute('href')).toBe('https://x.test/p/1');
    // Das Vorschaubild kommt vom Thumbnail-Proxy (gespeicherte Evidence).
    expect(link.querySelector('img').getAttribute('src')).toContain('/api/thumbnails/asset-1');
  });

  it('markierte Projekte stehen ohne Suche sichtbar in der Liste', () => {
    const onToggle = vi.fn();
    const markiert = { id: 't1', title_original: 'H wie Habicht', is_own_project: true };
    renderPanel({ titles: [markiert], onToggleTitleOwnProject: onToggle });

    expect(screen.getByText(/Wir-Projekte markieren \(1 markiert\)/)).toBeTruthy();
    const box = screen.getByRole('checkbox', { name: /H wie Habicht/ });
    expect(box.checked).toBe(true);
    fireEvent.click(box);
    expect(onToggle).toHaveBeenCalledWith(markiert);
  });

  // Projekt-One-Pager (25.08.2026, Roadmap Schritt 4): eigener Knopf,
  // eigenes Flag — unabhaengig vom Start-Brief freigebbar.
  it('One-Pager-Knopf erscheint nur mit projekt_export-Flag und ruft den Handler', () => {
    const projekt = { id: 't1', title_original: 'Unser Film', is_own_project: true };
    const onProjektExport = vi.fn();
    renderPanel({
      titles: [projekt],
      onProjektExport,
      features: { wir_projekte: true, projekt_start_brief: true, projekt_export: true },
    });
    fireEvent.click(screen.getByText('One-Pager'));
    expect(onProjektExport).toHaveBeenCalledWith(expect.objectContaining({ id: 't1' }));

    cleanup();
    renderPanel({
      titles: [projekt],
      features: { wir_projekte: true, projekt_start_brief: true },
    });
    expect(screen.queryByText('One-Pager')).toBeNull();
  });
});
