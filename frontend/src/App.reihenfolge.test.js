// Wolfs Anordnung 25.08.: "Verleiher & Independents" (RoundupBlock)
// steht direkt hinter den grossen Studios — und die Text-Bausteine
// leben NUR noch im Admin (MonitoringPanel BriefingSection), nicht
// mehr im Nutzer-Panel. Quelltext-Pins nach dem Muster der
// Backend-Erreichbarkeits-Waechter: billig, und eine Umsortierung
// oder ein Wieder-Einbau faellt sofort auf.
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const app = readFileSync('src/App.jsx', 'utf8');
const patterns = readFileSync('src/PatternsBlock.jsx', 'utf8');

describe('Sektions-Reihenfolge und Bausteine-Umzug', () => {
  it('RoundupBlock steht vor BreakoutsBlock (direkt hinter den Studios)', () => {
    const roundup = app.indexOf('<RoundupBlock />');
    const breakouts = app.indexOf('<BreakoutsBlock />');
    expect(roundup).toBeGreaterThan(-1);
    expect(breakouts).toBeGreaterThan(-1);
    expect(roundup).toBeLessThan(breakouts);
  });

  it('das Nutzer-Panel rendert keine Text-Bausteine mehr', () => {
    expect(patterns.includes('BausteinSektion')).toBe(false);
    expect(patterns.includes('insightPatternBriefing')).toBe(false);
  });
});
