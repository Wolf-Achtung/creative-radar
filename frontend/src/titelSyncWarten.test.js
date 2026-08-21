// Titel-Sync-Polling (21.08.2026) — die Logik hinter dem Hintergrund-
// Umbau des "Titelquellen aktualisieren"-Buttons. Kritisch: ein ALTER
// fertiger Lauf darf nicht als Ergebnis durchgehen, bevor die neue
// Row ueberhaupt existiert.
import { describe, expect, it, vi } from 'vitest';

import { neuesterLauf, warteAufTitelSyncEnde } from './titelSyncWarten';

const sofort = () => Promise.resolve();

describe('warteAufTitelSyncEnde', () => {
  it('liefert den neuen Lauf, sobald er fertig ist', async () => {
    const fetchRuns = vi.fn()
      .mockResolvedValueOnce([{ id: 'alt', status: 'success' }])
      .mockResolvedValueOnce([{ id: 'neu', status: 'running' }])
      .mockResolvedValueOnce([{ id: 'neu', status: 'success', upserted_count: 7 }]);

    const lauf = await warteAufTitelSyncEnde(fetchRuns, 'alt', { warte: sofort });

    expect(lauf).toEqual({ id: 'neu', status: 'success', upserted_count: 7 });
    expect(fetchRuns).toHaveBeenCalledTimes(3);
  });

  it('haelt einen alten fertigen Lauf nicht fuer das neue Ergebnis', async () => {
    // Direkt nach dem 202 existiert die neue Row noch nicht — oben
    // steht weiter der alte success-Lauf. Der darf NICHT zurueckkommen.
    const fetchRuns = vi.fn().mockResolvedValue([{ id: 'alt', status: 'success' }]);

    const lauf = await warteAufTitelSyncEnde(fetchRuns, 'alt', {
      warte: sofort, maxVersuche: 4,
    });

    expect(lauf).toBeNull();
    expect(fetchRuns).toHaveBeenCalledTimes(4);
  });

  it('meldet einen fehlgeschlagenen neuen Lauf als solchen', async () => {
    const fetchRuns = vi.fn()
      .mockResolvedValue([{ id: 'neu', status: 'error', error_message: 'kaputt' }]);

    const lauf = await warteAufTitelSyncEnde(fetchRuns, 'alt', { warte: sofort });

    expect(lauf.status).toBe('error');
    expect(lauf.error_message).toBe('kaputt');
  });

  it('ueberlebt einzelne Fetch-Fehler beim Polling', async () => {
    const fetchRuns = vi.fn()
      .mockRejectedValueOnce(new Error('Netz weg'))
      .mockResolvedValueOnce([{ id: 'neu', status: 'success' }]);

    const lauf = await warteAufTitelSyncEnde(fetchRuns, 'alt', { warte: sofort });

    expect(lauf.status).toBe('success');
  });

  it('gibt nach dem Zeitbudget auf statt ewig zu pollen', async () => {
    const fetchRuns = vi.fn().mockResolvedValue([{ id: 'neu', status: 'running' }]);

    const lauf = await warteAufTitelSyncEnde(fetchRuns, 'alt', {
      warte: sofort, maxVersuche: 3,
    });

    expect(lauf).toBeNull();
    expect(fetchRuns).toHaveBeenCalledTimes(3);
  });
});

describe('neuesterLauf', () => {
  it('liefert die oberste Row oder null', async () => {
    expect(await neuesterLauf(() => Promise.resolve([{ id: 'a' }, { id: 'b' }]))).toEqual({ id: 'a' });
    expect(await neuesterLauf(() => Promise.resolve([]))).toBeNull();
    expect(await neuesterLauf(() => Promise.resolve(null))).toBeNull();
  });
});
