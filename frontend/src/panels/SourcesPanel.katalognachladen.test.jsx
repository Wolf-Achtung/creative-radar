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

const VORSCHAU = 'Fehlende Titel prüfen (Vorschau)';

function renderPanel({
  features = { katalog_nachladen: true },
  onKatalogVorschau = () => {},
  onKatalogAnwenden = () => {},
  katalogVorschau = null,
  busy = false,
} = {}) {
  return render(
    <SourcesPanel
      features={features}
      onKatalogVorschau={onKatalogVorschau}
      onKatalogAnwenden={onKatalogAnwenden}
      katalogVorschau={katalogVorschau}
      busy={busy}
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
  it('ohne Flag ist kein Knopf da — Production-Sicht', () => {
    renderPanel({ features: {} });
    expect(screen.queryByRole('button', { name: VORSCHAU })).toBeNull();
    expect(screen.queryByRole('button', { name: /wirklich anlegen/ })).toBeNull();
  });

  it('mit Flag steht die Vorschau bereit und loest den Lauf aus', () => {
    const onKatalogVorschau = vi.fn();
    renderPanel({ onKatalogVorschau });

    fireEvent.click(screen.getByRole('button', { name: VORSCHAU }));
    expect(onKatalogVorschau).toHaveBeenCalledTimes(1);
  });

  it('ohne Vorschau laesst sich nichts anlegen', () => {
    // Der Kern des Zwei-Stufen-Ablaufs: schreiben erst, nachdem jemand
    // gesehen hat, WAS geschrieben wuerde.
    const onKatalogAnwenden = vi.fn();
    renderPanel({ onKatalogAnwenden, katalogVorschau: null });

    const anlegen = screen.getByRole('button', { name: /wirklich anlegen/ });
    expect(anlegen.disabled).toBe(true);
    fireEvent.click(anlegen);
    expect(onKatalogAnwenden).not.toHaveBeenCalled();
  });

  it('eine Vorschau ohne Anlagen schaltet nicht frei', () => {
    // "0 angelegt" ist ein Ergebnis, keine Freigabe — ein Klick ins
    // Leere soll nicht wie eine Entscheidung aussehen.
    const onKatalogAnwenden = vi.fn();
    renderPanel({ onKatalogAnwenden, katalogVorschau: { angelegt: 0, vorschau: true } });

    expect(screen.getByRole('button', { name: /wirklich anlegen/ }).disabled).toBe(true);
    expect(onKatalogAnwenden).not.toHaveBeenCalled();
  });

  it('nach einer Vorschau mit Treffern nennt der Knopf die Zahl und wendet an', () => {
    const onKatalogAnwenden = vi.fn();
    renderPanel({ onKatalogAnwenden, katalogVorschau: { angelegt: 3, vorschau: true } });

    const anlegen = screen.getByRole('button', { name: '3 Titel wirklich anlegen' });
    expect(anlegen.disabled).toBe(false);
    fireEvent.click(anlegen);
    expect(onKatalogAnwenden).toHaveBeenCalledTimes(1);
  });

  it('waehrend eines laufenden Vorgangs sind beide gesperrt', () => {
    renderPanel({ busy: true, katalogVorschau: { angelegt: 3, vorschau: true } });

    expect(screen.getByRole('button', { name: VORSCHAU }).disabled).toBe(true);
    expect(screen.getByRole('button', { name: /wirklich anlegen/ }).disabled).toBe(true);
  });
});
