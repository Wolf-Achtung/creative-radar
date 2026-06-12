// Sprint Bereichs-Erklärtexte (Commit B) — zentrale, zweisprachige Quelle
// für die Bereichs-/Sektions-Erklärungen. Schwester-Datei zu glossary.js:
// dort die BEGRIFFE (Kennzahlen: ER, Saves, Breakout …), hier die BEREICHE
// („was zeigt diese Sektion, was kann ich damit anfangen, worauf achten").
//
// Struktur pro Key — gleiche Konventionen wie das Glossar:
// - { de, en } mit je { title, short, long }.
// - ``title``: der Sektions-Name, wie der Nutzer ihn sieht (für
//   ARIA-Labels und eine spätere Hilfe-Seite).
// - ``short``: 1 Satz „was ist das" → Tooltip am Sektionstitel.
// - ``long``: Absatz nach dem Muster „was zeigt es / wofür nutzbar /
//   worauf achten" → ausklappbarer Erklär-Absatz (SectionHelp).
// - Aktuell rendert das Frontend NUR den de-Zweig (SECTION_HELP_LANG);
//   der en-Zweig hängt später am selben Sprachumschalter wie das Glossar.
//
// ALLE Texte sind in dieser Phase klar markierte Platzhalter ("[TODO …]")
// — die echten Texte liefert Wolf separat und ersetzt sie hier, ohne dass
// Komponenten angefasst werden müssen. Die Anzeige-Komponente
// (components/HelpSystem.jsx) rendert Platzhalter gedämpft, damit der
// Zwischenstand auf der Live-App sauber aussieht.
//
// Key-Konvention: <bereich><Sektion> in camelCase, Bereichs-Präfixe
// ``pair`` (Pair-Brief-Seite), ``landing`` (Startseite), ``admin``
// (Werkzeug-Bereich). Die Key-Liste entspricht der R1-Bereichs-Landkarte
// des Recon vom 12.06.2026.

const todo = (sectionDe, sectionEn) => ({
  de: {
    title: sectionDe,
    short: `[TODO short — ${sectionDe}: 1 Satz „was ist das"]`,
    long: `[TODO long — ${sectionDe}: was zeigt es / wofür nutzbar / worauf achten]`,
  },
  en: {
    title: sectionEn,
    short: `[TODO short — ${sectionEn}]`,
    long: `[TODO long — ${sectionEn}]`,
  },
});

export const SECTION_HELP = {
  // ---- Pair-Brief (/insights/weekly/:pair) -----------------------------
  pairHeadline: todo('Headline & TLDR', 'Headline & TL;DR'),
  pairFilmDetail: todo('Film-Detail (Posts pro Titel)', 'Film detail (posts per title)'),
  pairTopPosts: todo('Top-Posts', 'Top posts'),
  pairCrossMarket: todo('Drei Märkte, ein Film', 'Three markets, one film'),
  pairTimeline: todo('Markt-Zeitreihe', 'Market timeline'),
  pairForecast: todo('ER-Prognose', 'ER forecast'),
  pairBreakouts: todo('Breakouts dieser Woche', 'Breakouts this week'),
  pairWeekDetail: todo('Diese Woche im Detail', 'This week in detail'),
  pairRoles: todo('Kreativ-Empfehlungen (Cutter / Motion / Producer)', 'Creative recommendations (editor / motion / producer)'),
  pairPlatformDetails: todo('Plattform-Details', 'Platform details'),
  pairMethodology: todo('Methodik / Daten-Caveats', 'Methodology / data caveats'),

  // ---- Landing (/) ------------------------------------------------------
  landingStudios: todo('Die großen Studios (Brief-Kacheln)', 'The major studios (brief tiles)'),
  landingRoundups: todo('Verleiher & Independents (Roundups)', 'Distributors & independents (roundups)'),

  // ---- Admin (/admin) ----------------------------------------------------
  adminReports: todo('Report erstellen', 'Create report'),
  adminReview: todo('Treffer prüfen', 'Review hits'),
  adminSources: todo('Quellen', 'Sources'),
};

// Reihenfolge-Konvention analog GLOSSARY_ORDER — Lese-Reihenfolge der
// Seiten (Pair-Brief von oben nach unten, dann Landing, dann Admin).
// Heute nur dokumentarisch / für eine spätere Hilfe-Seite relevant.
export const SECTION_HELP_ORDER = [
  'pairHeadline',
  'pairFilmDetail',
  'pairTopPosts',
  'pairCrossMarket',
  'pairTimeline',
  'pairForecast',
  'pairBreakouts',
  'pairWeekDetail',
  'pairRoles',
  'pairPlatformDetails',
  'pairMethodology',
  'landingStudios',
  'landingRoundups',
  'adminReports',
  'adminReview',
  'adminSources',
];

// Aktuell gerenderte Sprache — gleiche Konvention wie GLOSSARY_LANG.
// Der spätere Sprachumschalter stellt beide gemeinsam um.
export const SECTION_HELP_LANG = 'de';

function entryFor(key, lang) {
  const entry = SECTION_HELP[key];
  if (!entry) return null;
  return entry[lang] || entry.de;
}

// Sektions-Name (für ARIA-Labels, z. B. „Was zeigt ‚Top-Posts‘?").
export function sectionHelpTitle(key, lang = SECTION_HELP_LANG) {
  const t = entryFor(key, lang);
  return t ? t.title : '';
}

// Kurz-Erklärung (Tooltip am Sektionstitel). Fallback short → long.
export function sectionHelpShort(key, lang = SECTION_HELP_LANG) {
  const t = entryFor(key, lang);
  if (!t) return '';
  return t.short || t.long;
}

// Volle Erklärung (ausklappbarer Absatz). Fallback long → short.
export function sectionHelpLong(key, lang = SECTION_HELP_LANG) {
  const t = entryFor(key, lang);
  if (!t) return '';
  return t.long || t.short;
}

// Platzhalter-Erkennung: solange Wolfs echte Texte fehlen, rendert die
// Anzeige-Komponente [TODO …]-Texte gedämpft (kein Layout-Bruch, klar
// als Zwischenstand erkennbar).
export function isPlaceholderText(text) {
  return typeof text === 'string' && text.trimStart().startsWith('[TODO');
}
