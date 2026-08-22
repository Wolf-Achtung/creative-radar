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

function renderPanel({ titles = [], onToggleTitleOwnProject = () => {} } = {}) {
  return render(
    <SourcesPanel
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
});
