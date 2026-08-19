// Wartung 20.08.2026 — Altersschwellen von ``StaleWarning``.
//
// Anlass: ``Date.now()`` stand direkt im ``useMemo``, der an
// ``generatedAt`` haengt. Das ist waehrend des Renders unrein
// (react-hooks/purity) und war zugleich sachlich widerspruechlich — das
// Alter haengt an der Zeit, die Memo-Abhaengigkeit aber nicht. Der
// Zeitpunkt wird jetzt einmal beim Mounten genommen.
//
// Die Schwellen selbst (Briefing 17.05.2026 §B1) standen bis dahin nur
// als Kommentar in der Datei. Hier sind sie festgehalten — sonst waere
// die Umstellung eine Behauptung.
//
// React wird ausdruecklich importiert: klassische JSX-Transformation.
import React from 'react';
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import StaleWarning from './StaleWarning.jsx';

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const MS_PRO_TAG = 1000 * 60 * 60 * 24;
// Fester Bezugspunkt statt "jetzt": der Test soll nicht davon abhaengen,
// wann er laeuft.
const JETZT = new Date('2026-08-20T12:00:00Z');

function vorTagen(tage) {
  return new Date(JETZT.getTime() - tage * MS_PRO_TAG).toISOString();
}

function zeige(generatedAt) {
  vi.useFakeTimers();
  vi.setSystemTime(JETZT);
  return render(<StaleWarning generatedAt={generatedAt} />);
}

describe('StaleWarning — Schwellen', () => {
  it('schweigt bei einem frischen Brief (<= 7 Tage)', () => {
    zeige(vorTagen(3));
    expect(document.querySelector('.stale-warning')).toBeNull();
  });

  it('schweigt noch genau auf der 7-Tage-Grenze', () => {
    zeige(vorTagen(7));
    expect(document.querySelector('.stale-warning')).toBeNull();
  });

  it('warnt ab dem 8. Tag als role="status"', () => {
    zeige(vorTagen(8));
    const box = screen.getByRole('status');
    expect(box.className).toContain('stale-warning--aged');
    expect(box.textContent).toContain('älter als eine Woche');
  });

  it('bleibt auf der 14-Tage-Grenze bei der milden Stufe', () => {
    zeige(vorTagen(14));
    expect(screen.getByRole('status').className).toContain('stale-warning--aged');
  });

  it('schlaegt ab dem 15. Tag als role="alert" an', () => {
    zeige(vorTagen(15));
    const box = screen.getByRole('alert');
    expect(box.className).toContain('stale-warning--critical');
    expect(box.textContent).toContain('älter als zwei Wochen');
  });

  it('rechnet gegen die echte Uhr, nicht gegen einen eingefrorenen Wert', () => {
    // Der Kern der Korrektur. Vorher war ``Date.now()`` im useMemo an
    // ``generatedAt`` gebunden — hier wird geprueft, dass das Alter
    // ueberhaupt aus dem Vergleich mit der aktuellen Zeit entsteht:
    // dasselbe Datum ist je nach "heute" frisch oder alt.
    const datum = '2026-08-01T12:00:00Z';

    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-08-05T12:00:00Z')); // 4 Tage alt
    const { unmount } = render(<StaleWarning generatedAt={datum} />);
    expect(document.querySelector('.stale-warning')).toBeNull();
    unmount();

    vi.setSystemTime(new Date('2026-08-20T12:00:00Z')); // 19 Tage alt
    render(<StaleWarning generatedAt={datum} />);
    expect(screen.getByRole('alert').className).toContain('stale-warning--critical');
  });

  it('schweigt ohne generatedAt', () => {
    zeige(undefined);
    expect(document.querySelector('.stale-warning')).toBeNull();
  });

  it('schweigt bei einem unlesbaren Datum', () => {
    // Kein Absturz und keine "NaN Tage"-Box: unbekanntes Alter heisst
    // keine Aussage, nicht falsche Aussage.
    zeige('kein datum');
    expect(document.querySelector('.stale-warning')).toBeNull();
  });
});
