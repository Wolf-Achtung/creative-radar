// Sektions-Titel auf der Startseite (Wolf 26.08.): "Breakouts — was
// gerade explodiert" und "Was gerade funktioniert — und was nicht"
// standen direkt untereinander und lasen sich wie dieselbe Aussage.
//
// Der Unterschied ist EINZELFALL gegen MUSTER: die Breakouts-Sektion
// zeigt konkrete Posts zum Anschauen (30 Tage), die Muster-Sektion
// Merkmale ueber viele Posts hinweg (90 Tage). Beide Ueberschriften
// muessen das im Wortlaut tragen — und der Hilfetext muss dieselbe
// Ueberschrift nennen wie die Sektion, sonst sucht der Leser im Panel
// nach einer Sektion, die es unter dem Namen nicht mehr gibt.
//
// Quelltext-Pins nach dem Muster von App.reihenfolge.test.js: billig,
// und ein Zurueckrutschen faellt sofort auf.
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const breakouts = readFileSync('src/BreakoutsBlock.jsx', 'utf8');
const patterns = readFileSync('src/PatternsBlock.jsx', 'utf8');
const help = readFileSync('src/section_help.js', 'utf8');

const BREAKOUT_TITEL = 'Einzelne Posts, die herausstechen';
const MUSTER_TITEL = 'Was gerade funktioniert — und was nicht';

describe('Sektions-Titel: Einzelfall gegen Muster', () => {
  it('die Breakouts-Sektion nennt sich nach ihrem Inhalt: einzelne Posts', () => {
    expect(breakouts.includes(BREAKOUT_TITEL)).toBe(true);
    // Die alte Formulierung las sich wie die Muster-Sektion darunter.
    expect(breakouts.includes('was gerade explodiert')).toBe(false);
  });

  it('die Muster-Sektion grenzt sich im Lead gegen Einzel-Posts ab', () => {
    expect(patterns.includes(MUSTER_TITEL)).toBe(true);
    expect(patterns.includes('Keine einzelnen Posts, sondern Merkmale')).toBe(true);
    // "Trailer-Intelligence" war Analysten-Jargon im Nutzer-Panel
    // (Klartext-Sweep #449); die Zeitangabe traegt jetzt das Thema.
    expect(patterns.includes('Muster aus 90 Tagen')).toBe(true);
  });

  it('kein Sektionstitel steht doppelt — die beiden sind unterscheidbar', () => {
    expect(BREAKOUT_TITEL).not.toBe(MUSTER_TITEL);
    // Keine der beiden Ueberschriften darf die andere enthalten oder
    // dieselbe Kernaussage tragen ("was gerade ...").
    expect(breakouts.includes(MUSTER_TITEL)).toBe(false);
    expect(patterns.includes(BREAKOUT_TITEL)).toBe(false);
  });

  it('der Hilfetext nennt dieselbe Ueberschrift wie die Sektion', () => {
    expect(help.includes(`title: '${BREAKOUT_TITEL}'`)).toBe(true);
  });
});
