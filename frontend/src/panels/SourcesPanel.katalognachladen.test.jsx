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
  onVorschlaegeAufraeumen = () => {},
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
      onVorschlaegeAufraeumen={onVorschlaegeAufraeumen}
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
    // Exakte Namen: der Nachbarknopf heisst "Bestehende Treffer neu
    // zuordnen" und wuerde jedes lose Muster mitnehmen.
    expect(screen.queryByRole('button', { name: 'Übernehmen' })).toBeNull();
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

    const knopf = screen.getByRole('button', { name: 'Übernehmen' });
    expect(knopf.disabled).toBe(true);
    fireEvent.click(knopf);
    expect(onKatalogAnwenden).not.toHaveBeenCalled();
  });

  it('eine Vorschau ganz ohne Wirkung schaltet nicht frei', () => {
    // Nichts anzulegen UND nichts zuzuordnen — ein Klick ins Leere
    // soll nicht wie eine Entscheidung aussehen.
    const onKatalogAnwenden = vi.fn();
    renderPanel({
      onKatalogAnwenden,
      katalogVorschau: { angelegt: 0, zugeordnet: 0, vorschau: true },
    });

    expect(screen.getByRole('button', { name: 'Übernehmen' }).disabled).toBe(true);
    expect(onKatalogAnwenden).not.toHaveBeenCalled();
  });

  it('ohne Neuanlage, aber mit Zuordnungen ist der Knopf offen', () => {
    // Wolfs Lauf vom 25.08.2026: "0 Titel wuerden angelegt, 18 Treffer
    // zugeordnet" — und der Knopf war gesperrt, weil die Bedingung an
    // ``angelegt`` hing. 18 Assets warteten auf ihre Zuordnung.
    const onKatalogAnwenden = vi.fn();
    renderPanel({
      onKatalogAnwenden,
      katalogVorschau: { angelegt: 0, zugeordnet: 18, vorschau: true },
    });

    const knopf = screen.getByRole('button', { name: '18 Treffer zuordnen' });
    expect(knopf.disabled).toBe(false);
    fireEvent.click(knopf);
    expect(onKatalogAnwenden).toHaveBeenCalledTimes(1);
  });

  it('nennt Anlagen und Zuordnungen getrennt', () => {
    renderPanel({ katalogVorschau: { angelegt: 3, zugeordnet: 20, vorschau: true } });

    const knopf = screen.getByRole('button', { name: '3 Titel anlegen, 20 Treffer zuordnen' });
    expect(knopf.disabled).toBe(false);
  });

  it('waehrend eines laufenden Vorgangs sind beide gesperrt', () => {
    renderPanel({
      busy: true,
      katalogVorschau: { angelegt: 3, zugeordnet: 20, vorschau: true },
    });

    expect(screen.getByRole('button', { name: VORSCHAU }).disabled).toBe(true);
    expect(
      screen.getByRole('button', { name: '3 Titel anlegen, 20 Treffer zuordnen' }).disabled,
    ).toBe(true);
  });
});


// Aufgeraeumt am 31.08.2026 (Wolfs Befund: „viel zu viele Buttons").
describe('SourcesPanel Aufraeumen-Knopf', () => {
  it('EIN sichtbarer Pipeline-Knopf, die Einzelschritte im Aufklapper', () => {
    const onVorschlaegeAufraeumen = vi.fn();
    renderPanel({ onVorschlaegeAufraeumen });

    fireEvent.click(screen.getByRole('button', { name: 'Vorschläge aufräumen (Autopilot → KI → TMDb)' }));
    expect(onVorschlaegeAufraeumen).toHaveBeenCalledTimes(1);

    // Die frueheren Buttons existieren weiter — aber hinter einem
    // <details>-Aufklapper, nicht mehr als sechsfache Knopfreihe.
    const aufklapper = screen.getByText('Einzelne Schritte — für Diagnose und Sonderfälle').closest('details');
    expect(aufklapper).not.toBeNull();
    for (const name of [
      'Titelquellen aktualisieren',
      'Bestehende Treffer neu zuordnen',
      'Offene Vorschläge automatisch bestätigen',
      'Rest-Vorschläge mit KI prüfen',
      'Fehlende Titel prüfen (Vorschau)',
    ]) {
      const knopf = screen.getByRole('button', { name });
      expect(aufklapper.contains(knopf)).toBe(true);
    }
  });

  it('der Aufraeumen-Knopf braucht kein Nachladen-Flag — die Pipeline meldet den Skip selbst', () => {
    renderPanel({ features: {} });
    expect(screen.getByRole('button', { name: 'Vorschläge aufräumen (Autopilot → KI → TMDb)' })).toBeTruthy();
  });
});
