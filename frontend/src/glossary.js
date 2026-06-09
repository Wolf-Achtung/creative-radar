// V3 Sprint 8 — zentrale, zweisprachige Begriffsquelle für das Erklär-System.
//
// EINE Quelle für beide Zugänge: die Info-Punkte am Begriff (GlossaryHint) UND
// den ausklappbaren Gesamt-Glossar-Block. Kein Duplizieren von Definitionen.
//
// Zweisprachiges Fundament: jeder Eintrag trägt { de, en }. Aktuell rendert das
// Frontend NUR den de-Zweig; der en-Zweig ist befüllt, aber noch nicht sichtbar
// (Sprachumschalter ist ein späterer, dann additiver Schritt). Bei Fachbegriffen
// (Engagement Rate, Saves, Breakout) nutzt der en-Zweig die etablierte englische
// Branchen-Entsprechung, keine wörtliche Übersetzung.
//
// Ton: sachlich, keine Werbesprache, Zahlen ausgeschrieben (Ton-Pass-Geist #249).
//
// GLOSSARY_ORDER bestimmt die Reihenfolge im Gesamt-Glossar-Block; die
// Info-Punkte referenzieren einzelne Keys über <GlossaryHint term="…" />.

export const GLOSSARY = {
  er: {
    de: {
      term: 'Engagement-Rate (ER)',
      definition:
        'Reaktionen je Aufruf: Likes plus Kommentare, geteilt durch die Aufrufe — nur für Posts mit Aufrufen über null. Genutzt in der Markt-Vergleichsleiste, der Zeitreihe und der Prognose. Nicht zu verwechseln mit der Aktivierungs-Rate, die zusätzlich Saves mitzählt.',
    },
    en: {
      term: 'Engagement Rate (ER)',
      definition:
        'Reactions per view: likes plus comments divided by views, counting only posts with more than zero views. Used in the market comparison bar, the timeline and the forecast. Not to be confused with the activation rate, which also counts saves.',
    },
  },
  activationRate: {
    de: {
      term: 'Aktivierungs-Rate',
      definition:
        'Anteil der Zuschauer, die reagiert haben: Likes plus Kommentare plus Saves, geteilt durch die Aufrufe (TikTok und Instagram); bei YouTube ohne Saves. Genutzt in den Top-Posts. Nicht zu verwechseln mit der Engagement-Rate, die Saves nicht mitzählt. Auf TikTok sind fünf bis zehn Prozent üblich, darüber stark.',
    },
    en: {
      term: 'Activation Rate',
      definition:
        'Share of viewers who reacted: likes plus comments plus saves divided by views (TikTok and Instagram); without saves on YouTube. Used in the top posts. Not to be confused with the engagement rate, which does not count saves. On TikTok five to ten percent is typical, above that is strong.',
    },
  },
  views: {
    de: {
      term: 'Aufrufe',
      definition:
        'Wie oft ein Post angesehen wurde. In der Markt-Vergleichsleiste und der Zeitreihe die Summe über alle Posts eines Markts. Aufrufe sind ein aktueller Stand und wachsen über die Zeit.',
    },
    en: {
      term: 'Views',
      definition:
        'How often a post was viewed. In the market comparison bar and the timeline this is the sum across all posts of a market. View counts are a current snapshot and grow over time.',
    },
  },
  reactions: {
    de: {
      term: 'Reactions (Likes)',
      definition:
        'Die Likes eines Posts. In einigen Ansichten als „Reactions" bezeichnet — dieselbe Zahl.',
    },
    en: {
      term: 'Reactions (Likes)',
      definition:
        "A post's likes. Labelled “Reactions” in some views — the same number.",
    },
  },
  comments: {
    de: { term: 'Kommentare', definition: 'Die Anzahl der Kommentare unter einem Post.' },
    en: { term: 'Comments', definition: 'The number of comments on a post.' },
  },
  saves: {
    de: {
      term: 'Saves',
      definition:
        'Wie oft ein Post gespeichert wurde (TikTok und Instagram). YouTube liefert keine Saves. Fließt in die Aktivierungs-Rate ein, nicht in die Engagement-Rate.',
    },
    en: {
      term: 'Saves',
      definition:
        'How often a post was bookmarked (TikTok and Instagram). YouTube does not report saves. Counts toward the activation rate, not the engagement rate.',
    },
  },
  posts: {
    de: { term: 'Posts', definition: 'Die Anzahl der Beiträge im jeweiligen Zeitraum und Markt.' },
    en: { term: 'Posts', definition: 'The number of posts in the given period and market.' },
  },
  breakout: {
    de: {
      term: 'Breakout',
      definition:
        'Ein Post, der deutlich über dem Durchschnitt seines Kanals liegt. Gemessen als Abstand zum Kanal-Schnitt (z-Wert) und als Vielfaches, zum Beispiel vier Mal über dem Kanal-Schnitt. Nur aussagekräftig, wenn der Kanal im Zeitraum genug Posts hatte.',
    },
    en: {
      term: 'Breakout',
      definition:
        'A post that performs well above its channel average. Measured as the distance from the channel mean (z-score) and as a multiple, for example four times the channel average. Only meaningful when the channel had enough posts in the period.',
    },
  },
  r2: {
    de: {
      term: 'Bestimmtheitsmaß (R²)',
      definition:
        'Ein Gütemaß der Prognose-Linie zwischen null und eins. Eins bedeutet, die Linie trifft die bisherigen Werte genau; niedrige Werte bedeuten, die Prognose ist wenig belastbar.',
    },
    en: {
      term: 'Coefficient of determination (R²)',
      definition:
        'A goodness-of-fit measure for the forecast line between zero and one. One means the line matches the past values exactly; low values mean the forecast is not very reliable.',
    },
  },
  forecast: {
    de: {
      term: 'Prognose',
      definition:
        'Ein aus den bisherigen Wochen per linearer Regression fortgeschriebener Schätzwert der Engagement-Rate für die kommende Woche. Bei dünner Datenbasis unsicher — das Bestimmtheitsmaß zeigt die Verlässlichkeit.',
    },
    en: {
      term: 'Forecast',
      definition:
        "An estimate of next week's engagement rate, extrapolated from the previous weeks by linear regression. Uncertain on a thin data basis — the coefficient of determination shows how reliable it is.",
    },
  },
  market: {
    de: {
      term: 'Markt (DE / US / UK)',
      definition:
        'Das Land, dem ein Kanal zugeordnet ist: Deutschland (DE), die USA (US) oder Großbritannien (UK). Andere Märkte werden in dieser Ansicht nicht gezeigt.',
    },
    en: {
      term: 'Market (DE / US / UK)',
      definition:
        'The country a channel belongs to: Germany (DE), the United States (US) or the United Kingdom (UK). Other markets are not shown in this view.',
    },
  },
  coverage: {
    de: {
      term: 'Coverage',
      definition:
        'Anteil der Posts, die einem konkreten Filmtitel zugeordnet werden konnten. Zeigt, wie zielgerichtet ein Kanal einen Titel bewirbt.',
    },
    en: {
      term: 'Coverage',
      definition:
        "Share of posts that could be matched to a specific film title. Indicates how focused a channel's promotion of a title is.",
    },
  },
  crossMarketMatch: {
    de: {
      term: 'Cross-Market-Match',
      definition:
        'Posts, die in zwei Märkten denselben Filmtitel bewerben (zum Beispiel Deutschland und die USA). Erlaubt den direkten Vergleich, wie derselbe Titel in beiden Märkten läuft.',
    },
    en: {
      term: 'Cross-market match',
      definition:
        'Posts that promote the same film title in two markets (for example Germany and the United States). Allows a direct comparison of how the same title performs in both markets.',
    },
  },
  tldr: {
    de: {
      term: 'TLDR',
      definition:
        '„Too long, didn\'t read" — die Kurzfassung des Briefs in wenigen Sätzen.',
    },
    en: {
      term: 'TL;DR',
      definition:
        "“Too long, didn't read” — the brief's summary in a few sentences.",
    },
  },
  headline: {
    de: {
      term: 'Headline',
      definition:
        'Der Kopf-Satz des Briefs: die wichtigste Aussage der Woche in einer Zeile.',
    },
    en: {
      term: 'Headline',
      definition: "The brief's top line: the week's key takeaway in one sentence.",
    },
  },
  topPerformer: {
    de: {
      term: 'Top-Performer',
      definition:
        'Die Posts mit den höchsten Werten im Zeitraum, sortierbar nach Aufrufen, Reactions oder Aktivierungs-Rate.',
    },
    en: {
      term: 'Top performers',
      definition:
        'The posts with the highest values in the period, sortable by views, reactions or activation rate.',
    },
  },
  roundup: {
    de: {
      term: 'Roundup',
      definition:
        'Ein wöchentlicher Überblick über ein Marktsegment statt eines einzelnen Studio-Paars.',
    },
    en: {
      term: 'Roundup',
      definition: 'A weekly overview of a market segment rather than a single studio pair.',
    },
  },
  studioBrief: {
    de: {
      term: 'Studio-Brief',
      definition:
        'Der Wochen-Bericht zu einem Studio-Paar — ein Markt gegen einen anderen, zum Beispiel die deutsche und die US-Präsenz desselben Studios.',
    },
    en: {
      term: 'Studio brief',
      definition:
        "The weekly report for a studio pair — one market against another, for example a studio's German and US presence.",
    },
  },
};

// Reihenfolge im Gesamt-Glossar-Block (B). Kennzahlen zuerst, dann Konzepte.
export const GLOSSARY_ORDER = [
  'er',
  'activationRate',
  'views',
  'reactions',
  'comments',
  'saves',
  'posts',
  'breakout',
  'forecast',
  'r2',
  'market',
  'coverage',
  'crossMarketMatch',
  'topPerformer',
  'headline',
  'tldr',
  'studioBrief',
  'roundup',
];

// Aktuell gerenderte Sprache. Fundament für den späteren Sprachumschalter —
// HEUTE fix 'de'; der en-Zweig ist befüllt, wird aber nicht angezeigt.
export const GLOSSARY_LANG = 'de';

// Definition eines Begriffs in der aktiven Sprache (Fallback auf de).
export function glossaryDefinition(term, lang = GLOSSARY_LANG) {
  const entry = GLOSSARY[term];
  if (!entry) return '';
  return (entry[lang] || entry.de).definition;
}
