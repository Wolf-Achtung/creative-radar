// Wartung 20.08.2026 — Kandidaten-Durchlauf von ``ImagePreview``.
//
// Anlass: der Index wurde per ``useEffect(() => setIndex(0), [key])``
// zurueckgesetzt, wenn die Kandidatenliste wechselte. Derselbe Fall wie
// in ``CollapsibleCard`` — abgeleiteter Zustand ueber einen Effect —
// nur mit sichtbarer Folge, weil der Effect erst NACH dem Render laeuft:
// ein Frame lang stand der alte Index gegen die neue Liste.
//
// Diese Datei haelt beide Zusagen fest, die dabei erhalten bleiben
// muessen: der Durchlauf durch kaputte Quellen, und der Neuanfang bei
// einer neuen Liste — ohne den Fehlstand dazwischen.
//
// React wird ausdruecklich importiert: klassische JSX-Transformation.
import React from 'react';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { ImagePreview } from './ImagePreview.jsx';

afterEach(cleanup);

function bild() {
  return document.querySelector('img');
}

function ladefehler() {
  // Der echte Browser feuert onError, wenn die Quelle nicht laedt.
  fireEvent.error(bild());
}

describe('ImagePreview — Kandidaten-Durchlauf', () => {
  it('zeigt die erste Quelle', () => {
    render(<ImagePreview sources={['a.jpg', 'b.jpg']} />);
    expect(bild().getAttribute('src')).toBe('a.jpg');
  });

  it('geht bei einem Ladefehler zur naechsten Quelle', () => {
    render(<ImagePreview sources={['a.jpg', 'b.jpg']} />);
    ladefehler();
    expect(bild().getAttribute('src')).toBe('b.jpg');
  });

  it('zeigt den Fehlstand erst, wenn alle Quellen verbraucht sind', () => {
    render(<ImagePreview sources={['a.jpg', 'b.jpg']} />);
    ladefehler();
    ladefehler();
    expect(bild()).toBeNull();
    expect(screen.getByText('Bild lädt nicht')).toBeTruthy();
  });

  it('faengt bei einer neuen Kandidatenliste wieder vorne an', () => {
    const { rerender } = render(<ImagePreview sources={['a.jpg', 'b.jpg']} />);
    ladefehler();
    expect(bild().getAttribute('src')).toBe('b.jpg');

    rerender(<ImagePreview sources={['c.jpg', 'd.jpg']} />);
    expect(bild().getAttribute('src')).toBe('c.jpg');
  });

  it('zeigt bei einer KUERZEREN neuen Liste zu KEINEM Zeitpunkt den Fehlstand', () => {
    // Das ist der Fehler, der die Umstellung ausgeloest hat, und der
    // einzige Test hier, der die beiden Bauformen ueberhaupt
    // unterscheidet.
    //
    // Auf den ENDzustand zu pruefen genuegt nicht: ``rerender`` laeuft
    // in ``act()``, React fuehrt den Effect also noch vor der
    // Rueckkehr aus. Am Ende steht bei beiden Varianten dasselbe da —
    // ein Test auf das Ergebnis waere gruen geblieben, auch mit dem
    // useEffect. (Genau so nachgestellt: Guard zurueckgebaut, Test
    // blieb gruen. Deshalb steht hier jetzt etwas anderes.)
    //
    // Der Unterschied liegt im COMMIT dazwischen:
    //
    //   useEffect        -> React committet den Zwischenstand ins DOM
    //                       (index=2 gegen eine Liste der Laenge 1 =>
    //                       "Bild laedt nicht"), der Effect setzt
    //                       zurueck, ein zweiter Commit ersetzt ihn.
    //                       Der Nutzer sieht kurz einen Fehler, der
    //                       keiner ist.
    //   Render-Anpassung -> React verwirft den angefangenen Durchgang
    //                       VOR dem Commit. Der Zwischenstand erreicht
    //                       das DOM nie.
    //
    // Ein MutationObserver sieht genau diesen Unterschied: er
    // protokolliert jeden Knoten, der ins DOM gelangt — auch einen, der
    // Millisekunden spaeter wieder verschwindet.
    const { container, rerender } = render(
      <ImagePreview sources={['a.jpg', 'b.jpg', 'c.jpg']} />
    );
    ladefehler();
    ladefehler();
    expect(bild().getAttribute('src')).toBe('c.jpg');

    const gesehen = [];
    const beobachter = new MutationObserver(() => {});
    beobachter.observe(container, { childList: true, subtree: true });

    rerender(<ImagePreview sources={['nur-eine.jpg']} />);

    // ``takeRecords`` liest die bereits angefallenen Eintraege synchron
    // aus, ohne auf den Microtask der Callback-Zustellung zu warten.
    for (const eintrag of beobachter.takeRecords()) {
      for (const knoten of eintrag.addedNodes) {
        if (knoten.nodeType === 1) gesehen.push(knoten.className);
      }
    }
    beobachter.disconnect();

    expect(gesehen.join(' ')).not.toContain('preview-placeholder--load-failed');
    expect(screen.queryByText('Bild lädt nicht')).toBeNull();
    expect(bild().getAttribute('src')).toBe('nur-eine.jpg');
  });

  it('behaelt den Durchlauf, solange die Liste dieselbe bleibt', () => {
    // Re-Render mit gleicher Liste (neues Array, gleicher Inhalt) — z.B.
    // weil die Elternkarte neue Daten bekommt. Der Fortschritt durch die
    // kaputten Quellen darf dabei nicht verlorengehen, sonst laeuft die
    // Karte in eine Schleife aus Ladefehlern.
    const { rerender } = render(<ImagePreview sources={['a.jpg', 'b.jpg']} />);
    ladefehler();
    expect(bild().getAttribute('src')).toBe('b.jpg');

    rerender(<ImagePreview sources={['a.jpg', 'b.jpg']} />);
    expect(bild().getAttribute('src')).toBe('b.jpg');
  });

  it('faellt ohne Quelle auf den Platzhalter zurueck', () => {
    render(<ImagePreview sources={[]} evidenceQuality="missing" />);
    expect(screen.getByText('Keine Bildquelle erfasst')).toBeTruthy();
  });

  it('nimmt imageUrl als Einzelquelle, wenn sources leer ist', () => {
    render(<ImagePreview imageUrl="einzeln.jpg" />);
    expect(bild().getAttribute('src')).toBe('einzeln.jpg');
  });
});
