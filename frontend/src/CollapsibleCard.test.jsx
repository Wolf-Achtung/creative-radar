// Wartung 20.08.2026 — der Klapp-Zustand von ``CollapsibleCard``.
//
// Anlass: ``defaultOpen`` wurde per ``useEffect(() => setIsOpen(...))``
// in den State gespiegelt — die Form, von der React abraet, weil sie
// zweimal rendert und den alten Zustand kurz sichtbar macht. Ersetzt
// durch die dokumentierte Anpassung waehrend des Renders.
//
// Die Zusage, die dabei erhalten bleiben muss, stand bis dahin nur als
// Kommentar da: ein User-Toggle ueberlebt Re-Renders, aber ein Wechsel
// des Ansichts-Modus (= neuer ``defaultOpen``-Wert) setzt zurueck.
// Genau das prueft diese Datei — sonst waere die Umstellung eine
// Behauptung.
//
// React wird ausdruecklich importiert: das Projekt faehrt die
// klassische JSX-Transformation (alle .jsx-Dateien importieren React),
// nicht die automatische Runtime.
import React from 'react';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { CollapsibleCard } from './InsightWeekly.jsx';

afterEach(cleanup);

function istOffen() {
  return document
    .querySelector('.collapsible-card')
    ?.classList.contains('is-open');
}

function karte(defaultOpen) {
  return (
    <CollapsibleCard title="Testkarte" defaultOpen={defaultOpen}>
      <p>Inhalt</p>
    </CollapsibleCard>
  );
}

function umschalten() {
  fireEvent.click(screen.getByRole('button', { name: /Testkarte/ }));
}

describe('CollapsibleCard — Klapp-Zustand', () => {
  it('startet in dem Zustand, den defaultOpen vorgibt', () => {
    const { unmount } = render(karte(false));
    expect(istOffen()).toBe(false);
    unmount();

    render(karte(true));
    expect(istOffen()).toBe(true);
  });

  it('behaelt den User-Toggle, solange defaultOpen gleich bleibt', () => {
    const { rerender } = render(karte(false));
    umschalten();
    expect(istOffen()).toBe(true);

    // Re-Render mit UNVERAENDERTEM defaultOpen — z.B. weil die Elternkarte
    // neue Daten bekommt. Der Toggle des Nutzers muss das ueberleben.
    rerender(karte(false));
    expect(istOffen()).toBe(true);
  });

  it('setzt zurueck, wenn defaultOpen wechselt', () => {
    const { rerender } = render(karte(false));
    umschalten();
    expect(istOffen()).toBe(true);

    // Ansichts-Modus-Wechsel: defaultOpen springt auf true.
    rerender(karte(true));
    expect(istOffen()).toBe(true);

    // Und wieder zurueck — jetzt muss die Karte zu sein, obwohl der
    // Nutzer sie vorher geoeffnet hatte.
    rerender(karte(false));
    expect(istOffen()).toBe(false);
  });

  it('laesst sich nach einem defaultOpen-Wechsel wieder frei bedienen', () => {
    const { rerender } = render(karte(false));
    rerender(karte(true));
    expect(istOffen()).toBe(true);

    umschalten();
    expect(istOffen()).toBe(false);

    // Kein erneuter Wechsel von defaultOpen — der Toggle haelt.
    rerender(karte(true));
    expect(istOffen()).toBe(false);
  });
});
