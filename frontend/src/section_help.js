// Sprint Bereichs-Erklärtexte — zentrale, zweisprachige Quelle für die
// Bereichs-/Sektions-Erklärungen. Schwester-Datei zu glossary.js: dort die
// BEGRIFFE (Kennzahlen: ER, Saves, Breakout …), hier die BEREICHE
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
// Stand 12.06.2026: die de-Texte sind Wolfs redaktionelle Fassung (geliefert
// 12.06., hier unverändert eingesetzt). Die en-Zweige sind weiterhin klar
// markierte Platzhalter ([TODO …]) und werden zusammen mit dem
// Sprachumschalter übersetzt — die Anzeige-Komponente
// (components/HelpSystem.jsx) rendert Platzhalter gedämpft.
//
// Key-Konvention: <bereich><Sektion> in camelCase, Bereichs-Präfixe
// ``pair`` (Pair-Brief-Seite), ``landing`` (Startseite), ``admin``
// (Werkzeug-Bereich). Die Key-Liste entspricht der R1-Bereichs-Landkarte
// des Recon vom 12.06.2026.

export const SECTION_HELP = {
  // ---- Pair-Brief (/insights/weekly/:pair) -----------------------------
  pairHeadline: {
    de: {
      title: 'Headline & TLDR',
      short: 'Die wichtigste Aussage der Woche für dieses Studio, in einem Satz.',
      long:
        'Oben steht die zentrale Beobachtung der Woche, darunter fasst das TLDR die wichtigsten Punkte in wenigen Zeilen. Wer wenig Zeit hat, liest nur das und weiß das Wesentliche. Bitte beachten: Das ist eine Zusammenfassung — die Belege dazu stehen in den Sektionen darunter.',
    },
    en: {
      title: 'Headline & TL;DR',
      short: '[TODO short — Headline & TL;DR]',
      long: '[TODO long — Headline & TL;DR]',
    },
  },
  pairFilmDetail: {
    de: {
      title: 'Film-Detailansicht',
      short: 'Ein einzelner Fokus-Titel, aufgeschlüsselt nach seinen Posts pro Markt.',
      long:
        'Nimmt einen Titel der Woche und zeigt seine Posts getrennt nach Markt (DE/US/UK) — wie der Film in jedem Markt bespielt wurde und wie er ankam. Nutzbar, um einen einzelnen Titel genauer anzusehen, statt nur die aggregierten Studio-Zahlen. Bitte beachten: Die Auswahl folgt dem, was diese Woche im Fokus stand — ein fehlender Titel heißt nicht, dass er nicht lief, sondern dass er hier nicht im Mittelpunkt steht.',
    },
    en: {
      title: 'Film detail (posts per title)',
      short: '[TODO short — Film detail (posts per title)]',
      long: '[TODO long — Film detail (posts per title)]',
    },
  },
  pairTopPosts: {
    de: {
      title: 'Top-Posts',
      short: 'Die stärksten Posts der Woche, sortier- und plattformfilterbar.',
      long:
        'Die nach Interaktion führenden Posts dieses Studios, mit Filter nach Plattform und sortierbaren Spalten. Nutzbar, um schnell zu sehen, was diese Woche tatsächlich gezogen hat, und über den Plattform-Filter zu vergleichen, was auf TikTok, Instagram und YouTube vorne liegt. Bitte beachten: Hohe Reichweite und hohe Interaktionsrate sind nicht dasselbe — ein Post mit vielen Views, aber wenig Interaktion steht anders da als einer mit kleiner, aber sehr aktiver Reichweite.',
    },
    en: {
      title: 'Top posts',
      short: '[TODO short — Top posts]',
      long: '[TODO long — Top posts]',
    },
  },
  pairCrossMarket: {
    de: {
      title: 'Drei Märkte, ein Film',
      short: 'Wo derselbe Titel in mehreren Märkten lief — und wie unterschiedlich er ankam.',
      long:
        'Zeigt einen Titel, der in zwei oder drei Märkten (DE/US/UK) gleichzeitig beworben wurde, und stellt die Performance nebeneinander. Nutzbar, um zu sehen, ob ein Trailer-Schnitt oder eine Kampagne über Märkte hinweg gleich zieht oder ob ein Markt deutlich anders reagiert. Bitte beachten: Unterschiede können am Markt liegen (Publikum, Timing), nicht am Material — der Vergleich zeigt dass es anders läuft, nicht zwingend warum.',
    },
    en: {
      title: 'Three markets, one film',
      short: '[TODO short — Three markets, one film]',
      long: '[TODO long — Three markets, one film]',
    },
  },
  pairTimeline: {
    de: {
      title: 'Markt-Zeitreihe',
      short: 'Interaktion und Views pro Kalenderwoche und Markt im Verlauf.',
      long:
        'Zeigt die Entwicklung von Interaktionsrate und Views über die letzten Wochen, getrennt nach Markt. Nutzbar, um zu sehen, ob die aktuelle Woche ein Ausreißer ist oder einem längeren Trend folgt — der Kontext, den eine Einzelwoche allein nicht liefert. Bitte beachten: Die Zeitreihe fasst alle Plattformen eines Markts zusammen; ein plattformspezifischer Effekt kann darin untergehen.',
    },
    en: {
      title: 'Market timeline',
      short: '[TODO short — Market timeline]',
      long: '[TODO long — Market timeline]',
    },
  },
  pairForecast: {
    de: {
      title: 'ER-Prognose',
      short: 'Eine Tendenz, wohin sich die Interaktion entwickelt — nur wo die Daten sie hergeben.',
      long:
        'Schätzt auf Basis der letzten Wochen, in welche Richtung sich die Interaktionsrate pro Markt entwickelt. Zeigt eine Prognose dort, wo ein belastbarer Trend erkennbar ist; wo die Werte zu stark schwanken, steht bewusst „zu schwankend für eine Prognose" statt einer Scheinzahl. Bitte beachten: Die Prognose ist eine Tendenz, keine gesicherte Zahl — das angezeigte Bestimmtheitsmaß (R²) beschreibt, wie verlässlich die Linie ist, und bei dünner Datenbasis (wenige Wochen) ist auch ein gezeigter Wert mit Vorsicht zu lesen.',
    },
    en: {
      title: 'ER forecast',
      short: '[TODO short — ER forecast]',
      long: '[TODO long — ER forecast]',
    },
  },
  pairBreakouts: {
    de: {
      title: 'Breakouts dieser Woche',
      short: 'Posts, die deutlich über dem üblichen Schnitt ihres Kanals lagen.',
      long:
        'Listet einzelne Posts, deren Interaktion spürbar über dem liegt, was der jeweilige Kanal sonst erreicht — Ausreißer nach oben. Nutzbar, um zu erkennen, was diese Woche überdurchschnittlich funktioniert hat und einen genaueren Blick lohnt. Bitte beachten: Ein Breakout ist ein Einzelfall, kein Muster — ob dahinter etwas Wiederholbares steckt (Schnitt, Hook, Format) oder nur ein viraler Zufall, zeigt erst der Vergleich mehrerer.',
    },
    en: {
      title: 'Breakouts this week',
      short: '[TODO short — Breakouts this week]',
      long: '[TODO long — Breakouts this week]',
    },
  },
  pairWeekDetail: {
    de: {
      title: 'Diese Woche im Detail',
      short: 'Die ausführliche Lesart der Woche — Muster, Trends, Handlungsansätze, Watch-Outs.',
      long:
        'Die ausformulierte Analyse hinter der Headline: erkannte Muster, Trends, konkrete Handlungsansätze und Dinge, die im Auge zu behalten sind, dazu eine Einordnung der Tonalität. Nutzbar, wenn du über die Kurzfassung hinaus verstehen willst, was die Woche ausgemacht hat und was daraus folgt. Bitte beachten: Das ist eine Interpretation der Daten, keine reine Zahlenwiedergabe — die zugrunde liegenden Posts findest du in den Sektionen darüber.',
    },
    en: {
      title: 'This week in detail',
      short: '[TODO short — This week in detail]',
      long: '[TODO long — This week in detail]',
    },
  },
  pairRoles: {
    de: {
      title: 'Kreativ-Empfehlungen',
      short: 'Konkrete Empfehlungen pro Rolle — Schnitt, Motion, Produktion.',
      long:
        'Drei Tabs mit rollenspezifischen Empfehlungen für die Woche: für den Schnitt (Pace, Hook-Strategie, empfohlene Längen), für Motion-Design und für die Creative-Producer-Ebene. Nutzbar als direkter Übertrag von den Daten in die Praxis — was diese Woche für Schnitt, Motion und Produktion zu beachten ist. Bitte beachten: Die Empfehlungen leiten sich aus den beobachteten Mustern ab — sie sind ein begründeter Vorschlag, kein Automatismus; dein Urteil im Einzelfall geht vor.',
    },
    en: {
      title: 'Creative recommendations (editor / motion / producer)',
      short: '[TODO short — Creative recommendations (editor / motion / producer)]',
      long: '[TODO long — Creative recommendations (editor / motion / producer)]',
    },
  },
  pairPlatformDetails: {
    de: {
      title: 'Plattform-Details',
      short: 'Die Kanal-Zahlen pro Plattform und Markt im Detail.',
      long:
        'Schlüsselt die Performance pro Plattform (TikTok, Instagram, YouTube) und Markt auf — Kanal-Statistiken bis auf die einzelnen Kennzahlen. Nutzbar, wenn du gezielt wissen willst, wie eine bestimmte Plattform in einem bestimmten Markt lief, statt nur die zusammengefassten Zahlen. Bitte beachten: Hier sind die Werte plattformgetrennt — anders als in der Zeitreihe, die alle Plattformen eines Markts zusammenfasst.',
    },
    en: {
      title: 'Platform details',
      short: '[TODO short — Platform details]',
      long: '[TODO long — Platform details]',
    },
  },
  pairMethodology: {
    de: {
      title: 'Methodik / Daten-Caveats',
      short: 'Wie die Zahlen entstehen — und wo ihre Grenzen liegen.',
      long:
        'Erklärt die Grundlage der Auswertung und benennt die Einschränkungen der Datenbasis dieser Woche — etwa Lücken, kleine Stichproben oder Effekte, die das Bild verzerren können. Nutzbar, um einzuschätzen, wie belastbar die übrigen Sektionen sind. Bitte beachten: Caveats sind kein Defekt, sondern Ehrlichkeit über die Grenzen — eine Zahl mit Vorbehalt ist verlässlicher gelesen als eine ohne.',
    },
    en: {
      title: 'Methodology / data caveats',
      short: '[TODO short — Methodology / data caveats]',
      long: '[TODO long — Methodology / data caveats]',
    },
  },

  // ---- Landing (/) ------------------------------------------------------
  landingStudios: {
    de: {
      title: 'Die großen Studios — einzeln im Blick',
      short: 'Eine Kachel = ein einzelnes Studio im Drei-Märkte-Vergleich DE, US, UK — mit eigenem Wochenbrief.',
      long:
        'Jede Kachel steht für genau EIN Studio, das wir einzeln beobachten — im direkten Drei-Märkte-Vergleich Deutschland (DE), USA (US) und Großbritannien (UK). Der Wochenbrief stellt die Social-Media-Aktivität desselben Studios in diesen drei Märkten nebeneinander. Die Kachel zeigt die Headline der Woche, die Zahl der ausgewerteten Posts und wann zuletzt aktualisiert wurde; ein Klick führt in den vollständigen Wochenbrief. Anders als die Sammel-Kacheln der Sektion „Verleiher & Independents" weiter unten geht es hier also um einzelne Studios, nicht um gebündelte Segmente. Bitte beachten: Die Kachel ist nur die Kurzfassung — die Belege stehen im Brief dahinter.',
    },
    en: {
      title: 'The major studios (brief tiles)',
      short: '[TODO short — The major studios (brief tiles)]',
      long: '[TODO long — The major studios (brief tiles)]',
    },
  },
  landingRoundups: {
    de: {
      title: 'Verleiher & Independents — das Feld im Überblick',
      short: 'Sammel-Überblick pro Segment (DE, US, UK) — viele kleinere Player pro Kachel, nicht einzelne Anbieter.',
      long:
        'Anders als die Studio-Kacheln oben (ein Studio pro Kachel, eigener Wochenbrief) werden Verleiher und Independents hier nicht einzeln, sondern pro Segment zusammengefasst — DE, US und UK, jeweils Verleiher/Major und Independent. Jede Kachel klappt direkt auf der Seite auf und zeigt die Fokus-Titel des Segments. Nutzbar, um die Bewegung jenseits der großen Studios im Blick zu behalten — wo kleinere Player diese Woche auffielen. Bitte beachten: Ein Segment bündelt viele Kanäle — der Überblick zeigt die Linie, nicht jeden einzelnen Titel.',
    },
    en: {
      title: 'Distributors & independents (roundups)',
      short: '[TODO short — Distributors & independents (roundups)]',
      long: '[TODO long — Distributors & independents (roundups)]',
    },
  },

  // ---- Admin (/admin) ----------------------------------------------------
  adminReports: {
    de: {
      title: 'Report erstellen',
      short: 'Wochenbriefs manuell anstoßen oder neu erzeugen.',
      long:
        'Hier lässt sich die Erzeugung der Wochenbriefs auslösen — für einzelne Studios oder im Lauf. Nutzbar, um einen Brief außerhalb des regulären Wochenlaufs neu zu erzeugen, etwa nach einer Korrektur an den Daten. Bitte beachten: Ein Neu-Erzeugen überschreibt den bestehenden Brief der Woche und kostet einen Generierungslauf — gezielt einsetzen, nicht versehentlich.',
    },
    en: {
      title: 'Create report',
      short: '[TODO short — Create report]',
      long: '[TODO long — Create report]',
    },
  },
  adminReview: {
    de: {
      title: 'Treffer prüfen',
      short: 'Zuordnungen von Posts zu Titeln kontrollieren und korrigieren.',
      long:
        'Die KI ordnet Posts automatisch den Filmtiteln zu — sichere Treffer laufen ohne dein Zutun durch. Jeder Post fließt ohnehin voll in die Auswertung ein, ob mit Titel oder ohne. Standard ist „Nur Titel-Vorschläge": Hier landen ausschließlich die Fälle, bei denen die KI unsicher war und deine Entscheidung braucht — steht dort 0, ist nichts zu tun. „Alle Treffer" ist der freiwillige Vollzugriff auf den gesamten Bestand, falls du gezielt etwas nachschauen oder eine Zuordnung von Hand setzen willst. Bitte beachten: „Zugeordnet" heißt, ein Titel hängt dran; „Noch zu prüfen" heißt nur, dass noch kein Titel zugeordnet ist — kein Fehler, und meist kein Handlungsbedarf.',
    },
    en: {
      title: 'Review hits',
      short: '[TODO short — Review hits]',
      long: '[TODO long — Review hits]',
    },
  },
  adminSources: {
    de: {
      title: 'Quellen',
      short: 'Die überwachten Kanäle prüfen und verwalten.',
      long:
        'Übersicht der Kanäle, aus denen Daten gezogen werden — welche aktiv sind, was sie liefern. Nutzbar, um den Bestand der Quellen im Blick zu behalten und Auffälligkeiten (fehlende Daten, stille Kanäle) zu erkennen. Bitte beachten: Welche Kanäle hier geführt werden, bestimmt, was überhaupt in den Auswertungen landet — die Quellenliste ist die Basis von allem dahinter.',
    },
    en: {
      title: 'Sources',
      short: '[TODO short — Sources]',
      long: '[TODO long — Sources]',
    },
  },
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

// Platzhalter-Erkennung: [TODO …]-Texte (heute nur noch der en-Zweig)
// rendert die Anzeige-Komponente gedämpft (kein Layout-Bruch, klar als
// Zwischenstand erkennbar).
export function isPlaceholderText(text) {
  return typeof text === 'string' && text.trimStart().startsWith('[TODO');
}
