// Katalog-Nachladen (25.08.2026) — Wolfs Befund aus der Staging-
// Abnahme: Das Flag war gesetzt, der Backend-Endpoint lief, und trotzdem
// gab es keinen Weg dorthin. Der Versuch ueber die Browser-Adresszeile
// scheiterte doppelt (GET statt POST, kein Bearer-Token). Ein Feature
// ohne Bedienweg ist kein Feature — deshalb der Knopf, und deshalb
// dieser Test.
import React from 'react';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { SourcesPanel } from './SourcesPanel';

afterEach(() => { cleanup(); vi.clearAllMocks(); });

const KNOPF = 'Fehlende Titel aus TMDb nachladen';

function renderPanel({ features = { katalog_nachladen: true }, onKatalogNachladen = () => {} } = {}) {
  return render(
    <SourcesPanel
      features={features}
      onKatalogNachladen={onKatalogNachladen}
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
      titles={[]}
      onToggleTitleOwnProject={() => {}}
      onFullSync={() => {}}
      cronBusy={false}
      cronMessage={null}
      lastCronRun={null}
    />
  );
}

describe('SourcesPanel Katalog-Nachladen', () => {
  it('ohne Flag ist der Knopf unsichtbar — Production-Sicht', () => {
    renderPanel({ features: {} });
    expect(screen.queryByRole('button', { name: KNOPF })).toBeNull();
  });

  it('mit Flag steht der Knopf da und loest den Lauf aus', () => {
    const onKatalogNachladen = vi.fn();
    renderPanel({ onKatalogNachladen });

    const knopf = screen.getByRole('button', { name: KNOPF });
    fireEvent.click(knopf);
    expect(onKatalogNachladen).toHaveBeenCalledTimes(1);
  });

  it('waehrend eines laufenden Vorgangs ist er gesperrt', () => {
    // Der Pfad legt Titel an; ein Doppelklick waehrend des Laufs ist
    // teurer als anderswo — dieselbe busy-Sperre wie die Nachbarknoepfe.
    const onKatalogNachladen = vi.fn();
    render(
      <SourcesPanel
        features={{ katalog_nachladen: true }}
        onKatalogNachladen={onKatalogNachladen}
        busy
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
        titles={[]}
        onToggleTitleOwnProject={() => {}}
        onFullSync={() => {}}
        cronBusy={false}
        cronMessage={null}
        lastCronRun={null}
      />
    );
    expect(screen.getByRole('button', { name: KNOPF }).disabled).toBe(true);
  });
});
