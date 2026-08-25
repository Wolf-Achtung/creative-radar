// Platin 5 (2026-07-13) — Unit-Tests für die aus App.jsx ausgelagerten
// reinen Helper-Funktionen. Erster Vitest-Testfall im Projekt für React-
// Komponenten-begleitende Logik (bisher nur node:test für imageUrl.js).
import { describe, expect, it } from 'vitest';
import {
  clip,
  computePairSectionDefaults,
  findExactTitleForSuggestion,
  formatCronRunStatus,
  formatDate,
  formatDateTime,
  formatKatalogNachladen,
  formatMultiplier,
  formatNumber,
  formatPairUpdated,
  formatPct1,
  formatUsdCents,
  getAssetDisplayTitle,
  inferTitleHint,
  isBriefStale,
  normalizeHandle,
  pairDeviationLabel,
  textPool,
  titleFromHashtag,
} from './format.js';

describe('formatNumber', () => {
  it('formats with German thousands separators', () => {
    expect(formatNumber(12345)).toBe('12.345');
  });
  it('returns em-dash for null/undefined/empty', () => {
    expect(formatNumber(null)).toBe('—');
    expect(formatNumber(undefined)).toBe('—');
    expect(formatNumber('')).toBe('—');
  });
});

describe('formatDate / formatDateTime', () => {
  it('returns a fallback string for missing values', () => {
    expect(formatDate(null)).toBe('Datum offen');
    expect(formatDateTime(null)).toBe('—');
  });
  it('formats a valid ISO date without throwing', () => {
    expect(formatDate('2026-05-11T10:00:00Z')).not.toBe('Datum offen');
    expect(formatDateTime('2026-05-11T10:00:00Z')).not.toBe('—');
  });
});

describe('formatCronRunStatus', () => {
  it('maps known statuses to German labels', () => {
    expect(formatCronRunStatus('completed')).toBe('abgeschlossen');
    expect(formatCronRunStatus('running')).toBe('läuft noch');
    expect(formatCronRunStatus('budget_exceeded')).toBe('abgebrochen (Budget)');
  });
  it('falls back to the raw value or em-dash', () => {
    expect(formatCronRunStatus('mystery')).toBe('mystery');
    expect(formatCronRunStatus(null)).toBe('—');
  });
});

describe('clip', () => {
  it('leaves short text untouched', () => {
    expect(clip('hello', 10)).toBe('hello');
  });
  it('truncates long text with an ellipsis', () => {
    expect(clip('a'.repeat(20), 5)).toBe('aaaaa …');
  });
  it('returns empty string for falsy input', () => {
    expect(clip(null)).toBe('');
    expect(clip(undefined)).toBe('');
  });
});

describe('normalizeHandle', () => {
  it('strips a leading @ and trailing slash', () => {
    expect(normalizeHandle('@warnerbros/')).toBe('warnerbros');
  });
  it('extracts the handle from a full TikTok URL', () => {
    expect(normalizeHandle('https://www.tiktok.com/@netflix/video/1')).toBe('netflix');
  });
});

describe('textPool', () => {
  it('joins all present text fields with spaces', () => {
    const pool = textPool({ title_name: 'A', caption: 'B', ocr_text: null });
    expect(pool).toBe('A B');
  });
  it('returns empty string when no fields are present', () => {
    expect(textPool({})).toBe('');
  });
});

describe('titleFromHashtag', () => {
  it('splits camelCase and strips marketing suffixes', () => {
    expect(titleFromHashtag('#DrawnToYouMovie')).toBe('Drawn To You');
  });
  it('returns empty string for too-short results', () => {
    expect(titleFromHashtag('#ab')).toBe('');
  });
});

describe('inferTitleHint', () => {
  it('prefers an already-assigned title_name', () => {
    const hint = inferTitleHint({ title_name: 'Zoomania' }, []);
    expect(hint).toEqual({ label: 'Zoomania', source: 'zugeordnet/erkannt' });
  });
  it('matches against the known titles pool via caption text', () => {
    const asset = { caption: 'Check out Zoomania in cinemas now' };
    const titles = [{ title_original: 'Zoomania' }];
    const hint = inferTitleHint(asset, titles);
    expect(hint).toEqual({ label: 'Zoomania', source: 'Vorschlag aus Text/Caption' });
  });
  it('falls back to a hashtag-derived suggestion', () => {
    const asset = { caption: 'New trailer #DrawnToYouMovie out now' };
    const hint = inferTitleHint(asset, []);
    expect(hint.source).toBe('Vorschlag aus Hashtag');
    expect(hint.label).toBe('Drawn To You');
  });
  it('returns the open placeholder when nothing matches', () => {
    const hint = inferTitleHint({ caption: 'no signal here' }, []);
    expect(hint).toEqual({ label: 'Filmtitel noch offen', source: 'Bitte zuordnen' });
  });
});

describe('getAssetDisplayTitle', () => {
  it('marks an assigned title as automatically recognized', () => {
    const label = getAssetDisplayTitle({ title_name: 'Zoomania' }, []);
    expect(label).toBe('Automatisch erkannt: Zoomania');
  });
  it('marks a plausible hint as a suggestion', () => {
    const asset = { caption: 'trailer #DrawnToYouMovie' };
    expect(getAssetDisplayTitle(asset, [])).toBe('Vorschlag: Drawn To You');
  });
  it('asks for manual assignment when nothing is inferred', () => {
    expect(getAssetDisplayTitle({}, [])).toBe('Filmtitel bitte zuordnen');
  });
});

describe('findExactTitleForSuggestion', () => {
  const titles = [{ title_original: 'Zoomania', title_local: 'Zootopia', aliases: ['Zootropolis'] }];
  it('matches case-insensitively against original/local/aliases', () => {
    expect(findExactTitleForSuggestion('zootropolis', titles)).toBe(titles[0]);
    expect(findExactTitleForSuggestion('ZOOMANIA', titles)).toBe(titles[0]);
  });
  it('returns null for no match or empty input', () => {
    expect(findExactTitleForSuggestion('Unknown Movie', titles)).toBeNull();
    expect(findExactTitleForSuggestion('', titles)).toBeNull();
    expect(findExactTitleForSuggestion(null, titles)).toBeNull();
  });
});

describe('computePairSectionDefaults', () => {
  it('picks the most common market combination and frequency', () => {
    const pairs = [
      { markets: ['DE', 'US'], frequency_label: 'wöchentlich' },
      { markets: ['DE', 'US'], frequency_label: 'wöchentlich' },
      { markets: ['US', 'UK'], frequency_label: 'monatlich' },
    ];
    const defaults = computePairSectionDefaults(pairs);
    expect(defaults.defaultMarkets.sort()).toEqual(['DE', 'US']);
    expect(defaults.defaultFrequency).toBe('wöchentlich');
  });
  it('returns empty defaults for an empty list', () => {
    expect(computePairSectionDefaults([])).toEqual({ defaultMarkets: [], defaultFrequency: null, defaultKey: '' });
  });
});

describe('pairDeviationLabel', () => {
  const defaults = { defaultMarkets: ['DE', 'US'], defaultFrequency: 'wöchentlich' };
  it('returns null for a pair matching the defaults', () => {
    const pair = { markets: ['DE', 'US'], frequency_label: 'wöchentlich' };
    expect(pairDeviationLabel(pair, defaults)).toBeNull();
  });
  it('renders a market deviation pill', () => {
    const pair = { markets: ['US', 'UK'], frequency_label: 'wöchentlich' };
    expect(pairDeviationLabel(pair, defaults)).toBe('US+UK');
  });
  it('combines market and frequency deviations', () => {
    const pair = { markets: ['US', 'UK'], frequency_label: 'monatlich' };
    expect(pairDeviationLabel(pair, defaults)).toBe('US+UK · monatlich');
  });
});

describe('formatPairUpdated / isBriefStale', () => {
  it('formats a date as DD.MM.', () => {
    expect(formatPairUpdated('2026-05-11T10:00:00Z')).toMatch(/^\d{2}\.\d{2}\.$/);
  });
  it('returns empty string for invalid/missing input', () => {
    expect(formatPairUpdated(null)).toBe('');
    expect(formatPairUpdated('not-a-date')).toBe('');
  });
  it('flags a nine-day-old brief as stale', () => {
    const nineDaysAgo = new Date(Date.now() - 9 * 24 * 60 * 60 * 1000).toISOString();
    expect(isBriefStale(nineDaysAgo)).toBe(true);
  });
  it('does not flag a one-day-old brief as stale', () => {
    const oneDayAgo = new Date(Date.now() - 1 * 24 * 60 * 60 * 1000).toISOString();
    expect(isBriefStale(oneDayAgo)).toBe(false);
  });
  it('treats missing values as not stale', () => {
    expect(isBriefStale(null)).toBe(false);
  });
});

describe('formatUsdCents / formatPct1 / formatMultiplier', () => {
  it('formats USD cents as currency', () => {
    expect(formatUsdCents(4200)).toContain('42,00');
  });
  it('formats a fraction as a one-decimal percentage', () => {
    expect(formatPct1(0.841)).toBe('84.1%');
  });
  it('formats a multiplier with one decimal and an x-suffix', () => {
    expect(formatMultiplier(6.789)).toBe('6.8x');
  });
  it('returns em-dash for null/undefined across all three', () => {
    expect(formatUsdCents(null)).toBe('—');
    expect(formatPct1(undefined)).toBe('—');
    expect(formatMultiplier(null)).toBe('—');
  });
});

// Katalog-Nachladen (25.08.2026): Die Meldung ist der ganze Bericht —
// an ihr entscheidet Wolf, ob er anlegt. Sie stand vorher als lokale
// Funktion in AdminApp und war damit ungetestet: eine Mutation, die
// den Mehrdeutigkeits-Zaehler aus der Zeile entfernte, ueberlebte
// unbemerkt. Seitdem liegt sie hier.
describe('formatKatalogNachladen', () => {
  const basis = {
    geprueft: 20, angelegt: 2, zugeordnet: 19, angelegte_titel: ['Lanterns'],
    nicht_belegt: 0, tmdb_unklar: 0, katalog_mehrdeutig: 0, fehler: 0,
    offen_danach: 0, vorschau: true,
  };

  it('sagt bei der Vorschau ausdruecklich, dass nichts geaendert wurde', () => {
    const text = formatKatalogNachladen(basis);
    expect(text).toContain('VORSCHAU');
    expect(text).toContain('wuerden angelegt');
    expect(text).toContain('Lanterns');
  });

  it('nennt beim Ernstfall die Anlage als geschehen', () => {
    const text = formatKatalogNachladen({ ...basis, vorschau: false });
    expect(text).not.toContain('VORSCHAU');
    expect(text).toContain('2 Titel angelegt');
  });

  it('erklaert eine leere Vorschau statt nur "0 geprueft" zu zeigen', () => {
    // Wolfs erster Klick in Staging meldete "0 geprueft" — daran war
    // nicht abzulesen, ob das Feature kaputt ist oder nichts zu tun hat.
    const text = formatKatalogNachladen({ ...basis, geprueft: 0, angelegt: 0 });
    expect(text).toContain('Katalog-Luecken-Marker');
    expect(text).toContain('KI-Pruefung');
  });

  it('trennt Katalog-Mehrdeutigkeit von TMDb-Unklarheit', () => {
    // Zwei verschiedene Ursachen mit zwei verschiedenen Reaktionen:
    // "zwei gleichnamige Titel liegen schon da" gegen "TMDb kennt den
    // Namen nicht eindeutig". Eine gemeinsame Zeile fuehrte zur
    // falschen Reaktion.
    const text = formatKatalogNachladen({
      ...basis, katalog_mehrdeutig: 3, tmdb_unklar: 1,
    });
    expect(text).toContain('3 mit gleichnamigem Titel im Katalog');
    expect(text).toContain('1 bei TMDb nicht eindeutig');
  });

  it('schweigt ueber Zaehler, die auf null stehen', () => {
    expect(formatKatalogNachladen(basis)).not.toContain('liegen gelassen');
  });

  it('sagt, wenn noch etwas offen ist', () => {
    expect(formatKatalogNachladen({ ...basis, offen_danach: 27 }))
      .toContain('Noch 27');
  });
});
