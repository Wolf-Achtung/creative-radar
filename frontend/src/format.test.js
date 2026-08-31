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
  formatBeweisZeile,
  formatKampagnenstart,
  formatLift,
  formatReleaseEinordnung,
  formatKatalogAnwendenLabel,
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

  it('trennt Katalog-Mehrdeutigkeit von den beiden TMDb-Faellen', () => {
    // Drei verschiedene Ursachen mit drei verschiedenen Reaktionen:
    // "zwei gleichnamige Titel liegen schon da", "TMDb kennt das Werk
    // gar nicht" (Handarbeit) und "TMDb kennt es mehrfach" (nur noch
    // auswaehlen). Der alte Sammel-Zaehler tmdb_unklar warf die letzten
    // beiden zusammen — an "10 nicht eindeutig" war nicht abzulesen,
    // ob die zehn automatisierbar sind (Wolfs Lauf vom 31.08.2026).
    const text = formatKatalogNachladen({
      ...basis, katalog_mehrdeutig: 3,
      tmdb_ohne_treffer: 2, tmdb_ohne_treffer_namen: ['Noga'],
      tmdb_mehrdeutig: 1, tmdb_mehrdeutig_namen: ['The Fox'],
    });
    expect(text).toContain('3 mit gleichnamigem Titel im Katalog');
    expect(text).toContain('2 ohne aktuellen TMDb-Treffer (Handarbeit): Noga');
    expect(text).toContain('1 mehrfach bei TMDb — Auswahl in „Treffer prüfen": The Fox');
  });

  it('nennt bei jedem liegen gelassenen Fall die Namen', () => {
    const text = formatKatalogNachladen({
      ...basis, nicht_belegt: 2, nicht_belegt_namen: ['Wolves', 'Salz und Wasser'],
    });
    expect(text).toContain('2 ohne Text-Beleg: Wolves, Salz und Wasser');
  });

  it('liest das Altformat mit Sammel-Zaehler weiter', () => {
    // Ein Cron-Summary von vor der Aufteilung traegt noch tmdb_unklar.
    const text = formatKatalogNachladen({ ...basis, tmdb_unklar: 4 });
    expect(text).toContain('4 bei TMDb nicht eindeutig');
  });

  it('schweigt ueber Zaehler, die auf null stehen', () => {
    expect(formatKatalogNachladen(basis)).not.toContain('liegen gelassen');
  });

  it('sagt, wenn noch etwas offen ist', () => {
    expect(formatKatalogNachladen({ ...basis, offen_danach: 27 }))
      .toContain('Noch 27');
  });
});

describe('formatKatalogAnwendenLabel', () => {
  it('nennt Anlagen und Zuordnungen getrennt', () => {
    expect(formatKatalogAnwendenLabel({ angelegt: 3, zugeordnet: 20 }))
      .toBe('3 Titel anlegen, 20 Treffer zuordnen');
  });

  it('laesst die Anlagen weg, wenn es keine gibt', () => {
    // Wolfs Lauf: 0 angelegt, 18 zugeordnet. "0 Titel anlegen" waere
    // hier irrefuehrend — der Knopf tut genau eine Sache.
    expect(formatKatalogAnwendenLabel({ angelegt: 0, zugeordnet: 18 }))
      .toBe('18 Treffer zuordnen');
  });

  it('faellt ohne Wirkung auf einen neutralen Text zurueck', () => {
    expect(formatKatalogAnwendenLabel({ angelegt: 0, zugeordnet: 0 })).toBe('Übernehmen');
    expect(formatKatalogAnwendenLabel(null)).toBe('Übernehmen');
  });
});

// Kampagnenstart-Label (25.08.2026): Wolfs Staging-Abnahme zeigte
// "1 Wochen vor Release", "0 Wochen vor Release" und "-1 Wochen vor
// Release" — grammatisch falsch bzw. irrefuehrend. Negative
// Vorlauftage bedeuten: die Kampagne begann erst NACH dem Release.
describe('formatKampagnenstart', () => {
  it('nennt Mehrzahl-Wochen vor dem Release', () => {
    expect(formatKampagnenstart(147)).toBe('21 Wochen vor Release');
  });

  it('kennt den Singular', () => {
    expect(formatKampagnenstart(7)).toBe('1 Woche vor Release');
  });

  it('nennt die Release-Woche beim Namen statt "0 Wochen"', () => {
    expect(formatKampagnenstart(2)).toBe('in der Release-Woche');
  });

  it('sagt "nach Release" statt negativer Wochen', () => {
    expect(formatKampagnenstart(-7)).toBe('1 Woche nach Release');
    expect(formatKampagnenstart(-49)).toBe('7 Wochen nach Release');
  });

  it('faellt bei fehlendem Wert auf den Strich zurueck', () => {
    expect(formatKampagnenstart(null)).toBe('—');
  });
});


// Release-Countdown (Roadmap Schritt 2, 25.08.): die Einordnung eines
// Wir-Projekts gegen den Markt-Kampagnenstart. Jede Fallgruppe einmal —
// der Satz ist die Anzeige, nicht Beiwerk.
describe('formatReleaseEinordnung', () => {
  it('ohne Release-Datum: klarer Hinweis', () => {
    expect(formatReleaseEinordnung({ tageBisRelease: null, marktMedianTage: 84, eigenePosts: 0, eigenerStartTage: null }))
      .toMatch(/Kein Release-Datum/);
  });

  it('Release vorbei: Nachlese statt Countdown', () => {
    expect(formatReleaseEinordnung({ tageBisRelease: -60, marktMedianTage: 84, eigenePosts: 5, eigenerStartTage: 70 }))
      .toBe('Release war vor 9 Wochen. 5 eigene Posts zugeordnet — Zeit für die Nachlese.');
    expect(formatReleaseEinordnung({ tageBisRelease: -60, marktMedianTage: 84, eigenePosts: 0, eigenerStartTage: null }))
      .toMatch(/Keine eigenen Posts/);
  });

  it('Kampagne laeuft: Vergleich frueher/spaeter als der Markt', () => {
    expect(formatReleaseEinordnung({ tageBisRelease: 21, marktMedianTage: 84, eigenePosts: 4, eigenerStartTage: 91 }))
      .toBe('Eure Kampagne läuft seit 13 Wochen vor Release (4 Posts). Vergleichbare Kampagnen starten meist 12 Wochen vor dem Release — ihr wart früher dran.');
    expect(formatReleaseEinordnung({ tageBisRelease: 21, marktMedianTage: 84, eigenePosts: 4, eigenerStartTage: 42 }))
      .toMatch(/ihr habt später begonnen/);
  });

  it('noch keine Posts: Abstand zum typischen Startfenster', () => {
    expect(formatReleaseEinordnung({ tageBisRelease: 126, marktMedianTage: 84, eigenePosts: 0, eigenerStartTage: null }))
      .toBe('Release in 18 Wochen, noch keine eigenen Posts zugeordnet. Vergleichbare Kampagnen starten meist 12 Wochen vor dem Release — bis zum typischen Startfenster sind es noch 6 Wochen.');
    expect(formatReleaseEinordnung({ tageBisRelease: 84, marktMedianTage: 84, eigenePosts: 0, eigenerStartTage: null }))
      .toMatch(/im typischen Startfenster/);
    expect(formatReleaseEinordnung({ tageBisRelease: 42, marktMedianTage: 84, eigenePosts: 0, eigenerStartTage: null }))
      .toMatch(/spät dran/);
  });

  it('ohne Markt-Benchmark bleibt der Satz ohne Vergleich', () => {
    expect(formatReleaseEinordnung({ tageBisRelease: 42, marktMedianTage: null, eigenePosts: 0, eigenerStartTage: null }))
      .toBe('Release in 6 Wochen, noch keine eigenen Posts zugeordnet.');
  });
});


// Beweis-Loop (Roadmap Schritt 3, 25.08.): der Satz je Empfehlungs-
// Zelle — umgesetzt/gewirkt in jeder Kombination.
describe('formatBeweisZeile', () => {
  it('nicht umgesetzt: abgeschlossene vs. laufende Folgewoche', () => {
    expect(formatBeweisZeile({ umgesetzt: 0, medianLiftWir: null, gewirkt: null, folgewocheAbgeschlossen: true }))
      .toBe('in der Folgewoche nicht umgesetzt.');
    expect(formatBeweisZeile({ umgesetzt: 0, medianLiftWir: null, gewirkt: null, folgewocheAbgeschlossen: false }))
      .toMatch(/läuft noch/);
  });

  it('umgesetzt und gewirkt: Lift mit Komma und klarem Urteil', () => {
    expect(formatBeweisZeile({ umgesetzt: 2, medianLiftWir: 1.8, gewirkt: true, folgewocheAbgeschlossen: true }))
      .toBe('2 Posts in der Folgewoche, Median 1,8x — über eurem Kanal-Schnitt, hat gewirkt.');
  });

  it('umgesetzt, aber unter dem Schnitt: ehrliches Urteil, Singular', () => {
    expect(formatBeweisZeile({ umgesetzt: 1, medianLiftWir: 0.5, gewirkt: false, folgewocheAbgeschlossen: true }))
      .toBe('1 Post in der Folgewoche, Median 0,5x — unter eurem Kanal-Schnitt geblieben.');
  });
});


// Referenz-Suche (Layout-Feedback 25.08.): Lift als lesbare Zahl.
describe('formatLift', () => {
  it('rundet gross auf ganze Zahlen, klein auf eine Nachkommastelle', () => {
    expect(formatLift(2377.7699)).toBe('2378x');
    expect(formatLift(39.02)).toBe('39x');
    expect(formatLift(6.69)).toBe('6,7x');
    expect(formatLift(2)).toBe('2x');
    expect(formatLift(null)).toBe('—');
  });
});
