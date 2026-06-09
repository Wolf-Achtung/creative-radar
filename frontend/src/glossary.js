// V3 Sprint 8 — zentrale, zweisprachige Begriffsquelle für das Erklär-System.
//
// EINE Quelle für beide Zugänge: die Info-Punkte am Begriff (GlossaryHint) UND
// den ausklappbaren Gesamt-Glossar-Block. Kein Duplizieren von Definitionen.
//
// Zwei Längen pro Begriff (Sprint-8-Nachbesserung):
// - ``short``: 1-2 Sätze (was + Formel, knappe Abgrenzung) → Tooltip-Bubble.
// - ``long``: volle Erklärung inkl. Plattform-Unterschied/Benchmark → Glossar-
//   Block am Seitenende, wo Platz für den ganzen Kontext ist.
// Die Bubble bleibt lesbar, der Kontext geht nicht verloren — eine Quelle.
//
// Zweisprachiges Fundament: jeder Eintrag trägt { de, en } mit je { term,
// short, long }. Aktuell rendert das Frontend NUR den de-Zweig; der en-Zweig
// ist befüllt, aber noch nicht sichtbar (Sprachumschalter ist ein späterer,
// dann additiver Schritt). Bei Fachbegriffen (Engagement Rate, Saves, Breakout)
// nutzt der en-Zweig die etablierte englische Branchen-Entsprechung.
//
// Ton: sachlich, keine Werbesprache, Zahlen ausgeschrieben (Ton-Pass-Geist #249).

export const GLOSSARY = {
  er: {
    de: {
      term: 'Engagement-Rate (ER)',
      short:
        'Reaktionen je Aufruf: (Likes + Kommentare) ÷ Aufrufe. Ohne Saves — anders als die Aktivierungs-Rate.',
      long:
        'Reaktionen je Aufruf: Likes plus Kommentare, geteilt durch die Aufrufe — nur für Posts mit Aufrufen über null. Genutzt in der Markt-Vergleichsleiste, der Zeitreihe und der Prognose. Nicht zu verwechseln mit der Aktivierungs-Rate, die zusätzlich Saves mitzählt.',
    },
    en: {
      term: 'Engagement Rate (ER)',
      short:
        'Reactions per view: (likes + comments) ÷ views. Without saves — unlike the activation rate.',
      long:
        'Reactions per view: likes plus comments divided by views, counting only posts with more than zero views. Used in the market comparison bar, the timeline and the forecast. Not to be confused with the activation rate, which also counts saves.',
    },
  },
  activationRate: {
    de: {
      term: 'Aktivierungs-Rate',
      short:
        'Reaktionen je Aufruf, mit Saves: (Likes + Kommentare + Saves) ÷ Aufrufe. Zählt Saves mit — anders als die Engagement-Rate.',
      long:
        'Anteil der Zuschauer, die reagiert haben: Likes plus Kommentare plus Saves, geteilt durch die Aufrufe (TikTok und Instagram); bei YouTube ohne Saves. Genutzt in den Top-Posts. Nicht zu verwechseln mit der Engagement-Rate, die Saves nicht mitzählt. Auf TikTok sind fünf bis zehn Prozent üblich, darüber stark.',
    },
    en: {
      term: 'Activation Rate',
      short:
        'Reactions per view, with saves: (likes + comments + saves) ÷ views. Counts saves — unlike the engagement rate.',
      long:
        'Share of viewers who reacted: likes plus comments plus saves divided by views (TikTok and Instagram); without saves on YouTube. Used in the top posts. Not to be confused with the engagement rate, which does not count saves. On TikTok five to ten percent is typical, above that is strong.',
    },
  },
  views: {
    de: {
      term: 'Aufrufe',
      short: 'Wie oft ein Post angesehen wurde; in Leiste und Zeitreihe die Summe je Markt.',
      long:
        'Wie oft ein Post angesehen wurde. In der Markt-Vergleichsleiste und der Zeitreihe die Summe über alle Posts eines Markts. Aufrufe sind ein aktueller Stand und wachsen über die Zeit.',
    },
    en: {
      term: 'Views',
      short: 'How often a post was viewed; in the bar and timeline the sum per market.',
      long:
        'How often a post was viewed. In the market comparison bar and the timeline this is the sum across all posts of a market. View counts are a current snapshot and grow over time.',
    },
  },
  reactions: {
    de: {
      term: 'Reactions (Likes)',
      short: 'Die Likes eines Posts; in einigen Ansichten „Reactions" genannt.',
      long:
        'Die Likes eines Posts. In einigen Ansichten als „Reactions" bezeichnet — dieselbe Zahl.',
    },
    en: {
      term: 'Reactions (Likes)',
      short: "A post's likes; called “reactions” in some views.",
      long:
        "A post's likes. Labelled “Reactions” in some views — the same number.",
    },
  },
  comments: {
    de: {
      term: 'Kommentare',
      short: 'Anzahl der Kommentare unter einem Post.',
      long: 'Die Anzahl der Kommentare unter einem Post.',
    },
    en: {
      term: 'Comments',
      short: 'Number of comments on a post.',
      long: 'The number of comments on a post.',
    },
  },
  saves: {
    de: {
      term: 'Saves',
      short: 'Wie oft ein Post gespeichert wurde (TikTok/Instagram). Zählt in die Aktivierungs-Rate.',
      long:
        'Wie oft ein Post gespeichert wurde (TikTok und Instagram). YouTube liefert keine Saves. Fließt in die Aktivierungs-Rate ein, nicht in die Engagement-Rate.',
    },
    en: {
      term: 'Saves',
      short: 'How often a post was bookmarked (TikTok/Instagram). Counts toward the activation rate.',
      long:
        'How often a post was bookmarked (TikTok and Instagram). YouTube does not report saves. Counts toward the activation rate, not the engagement rate.',
    },
  },
  posts: {
    de: {
      term: 'Posts',
      short: 'Anzahl der Beiträge im Zeitraum und Markt.',
      long: 'Die Anzahl der Beiträge im jeweiligen Zeitraum und Markt.',
    },
    en: {
      term: 'Posts',
      short: 'Number of posts in the period and market.',
      long: 'The number of posts in the given period and market.',
    },
  },
  breakout: {
    de: {
      term: 'Breakout',
      short: 'Ein Post deutlich über dem Kanal-Schnitt, als Vielfaches des Schnitts.',
      long:
        'Ein Post, der deutlich über dem Durchschnitt seines Kanals liegt. Gemessen als Abstand zum Kanal-Schnitt (z-Wert) und als Vielfaches, zum Beispiel vier Mal über dem Kanal-Schnitt. Nur aussagekräftig, wenn der Kanal im Zeitraum genug Posts hatte.',
    },
    en: {
      term: 'Breakout',
      short: 'A post well above its channel average, as a multiple of the mean.',
      long:
        'A post that performs well above its channel average. Measured as the distance from the channel mean (z-score) and as a multiple, for example four times the channel average. Only meaningful when the channel had enough posts in the period.',
    },
  },
  r2: {
    de: {
      term: 'Bestimmtheitsmaß (R²)',
      short: 'Güte der Prognose-Linie, null bis eins: eins = exakt, niedrig = wenig belastbar.',
      long:
        'Ein Gütemaß der Prognose-Linie zwischen null und eins. Eins bedeutet, die Linie trifft die bisherigen Werte genau; niedrige Werte bedeuten, die Prognose ist wenig belastbar.',
    },
    en: {
      term: 'Coefficient of determination (R²)',
      short: 'Forecast-line fit, zero to one: one = exact, low = unreliable.',
      long:
        'A goodness-of-fit measure for the forecast line between zero and one. One means the line matches the past values exactly; low values mean the forecast is not very reliable.',
    },
  },
  forecast: {
    de: {
      term: 'Prognose',
      short: 'Fortschreibung der Engagement-Rate auf die nächste Woche per linearer Regression.',
      long:
        'Ein aus den bisherigen Wochen per linearer Regression fortgeschriebener Schätzwert der Engagement-Rate für die kommende Woche. Bei dünner Datenbasis unsicher — das Bestimmtheitsmaß zeigt die Verlässlichkeit.',
    },
    en: {
      term: 'Forecast',
      short: "Next week's engagement rate, extrapolated by linear regression.",
      long:
        "An estimate of next week's engagement rate, extrapolated from the previous weeks by linear regression. Uncertain on a thin data basis — the coefficient of determination shows how reliable it is.",
    },
  },
  market: {
    de: {
      term: 'Markt (DE / US / UK)',
      short: 'Das Land eines Kanals: Deutschland (DE), die USA (US), Großbritannien (UK).',
      long:
        'Das Land, dem ein Kanal zugeordnet ist: Deutschland (DE), die USA (US) oder Großbritannien (UK). Andere Märkte werden in dieser Ansicht nicht gezeigt.',
    },
    en: {
      term: 'Market (DE / US / UK)',
      short: "A channel's country: Germany (DE), the United States (US), the United Kingdom (UK).",
      long:
        'The country a channel belongs to: Germany (DE), the United States (US) or the United Kingdom (UK). Other markets are not shown in this view.',
    },
  },
  coverage: {
    de: {
      term: 'Coverage',
      short: 'Anteil der Posts mit zugeordnetem Filmtitel.',
      long:
        'Anteil der Posts, die einem konkreten Filmtitel zugeordnet werden konnten. Zeigt, wie zielgerichtet ein Kanal einen Titel bewirbt.',
    },
    en: {
      term: 'Coverage',
      short: 'Share of posts matched to a film title.',
      long:
        "Share of posts that could be matched to a specific film title. Indicates how focused a channel's promotion of a title is.",
    },
  },
  crossMarketMatch: {
    de: {
      term: 'Cross-Market-Match',
      short: 'Derselbe Filmtitel, in zwei Märkten beworben — direkt vergleichbar.',
      long:
        'Posts, die in zwei Märkten denselben Filmtitel bewerben (zum Beispiel Deutschland und die USA). Erlaubt den direkten Vergleich, wie derselbe Titel in beiden Märkten läuft.',
    },
    en: {
      term: 'Cross-market match',
      short: 'The same film title promoted in two markets — directly comparable.',
      long:
        'Posts that promote the same film title in two markets (for example Germany and the United States). Allows a direct comparison of how the same title performs in both markets.',
    },
  },
  tldr: {
    de: {
      term: 'TLDR',
      short: 'Kurzfassung des Briefs in wenigen Sätzen.',
      long: '„Too long, didn\'t read" — die Kurzfassung des Briefs in wenigen Sätzen.',
    },
    en: {
      term: 'TL;DR',
      short: "The brief's summary in a few sentences.",
      long: "“Too long, didn't read” — the brief's summary in a few sentences.",
    },
  },
  headline: {
    de: {
      term: 'Headline',
      short: 'Der Kopf-Satz: die wichtigste Aussage der Woche.',
      long: 'Der Kopf-Satz des Briefs: die wichtigste Aussage der Woche in einer Zeile.',
    },
    en: {
      term: 'Headline',
      short: "The top line: the week's key takeaway.",
      long: "The brief's top line: the week's key takeaway in one sentence.",
    },
  },
  topPerformer: {
    de: {
      term: 'Top-Performer',
      short: 'Die Posts mit den höchsten Werten im Zeitraum.',
      long:
        'Die Posts mit den höchsten Werten im Zeitraum, sortierbar nach Aufrufen, Reactions oder Aktivierungs-Rate.',
    },
    en: {
      term: 'Top performers',
      short: 'The posts with the highest values in the period.',
      long:
        'The posts with the highest values in the period, sortable by views, reactions or activation rate.',
    },
  },
  roundup: {
    de: {
      term: 'Roundup',
      short: 'Wöchentlicher Überblick über ein Marktsegment.',
      long:
        'Ein wöchentlicher Überblick über ein Marktsegment statt eines einzelnen Studio-Paars.',
    },
    en: {
      term: 'Roundup',
      short: 'Weekly overview of a market segment.',
      long: 'A weekly overview of a market segment rather than a single studio pair.',
    },
  },
  studioBrief: {
    de: {
      term: 'Studio-Brief',
      short: 'Wochen-Bericht zu einem Studio-Paar (ein Markt gegen einen anderen).',
      long:
        'Der Wochen-Bericht zu einem Studio-Paar — ein Markt gegen einen anderen, zum Beispiel die deutsche und die US-Präsenz desselben Studios.',
    },
    en: {
      term: 'Studio brief',
      short: 'Weekly report for a studio pair (one market vs another).',
      long:
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

// Kurz-Erklärung (Tooltip-Bubble). Fallback short → long.
export function glossaryDefinition(term, lang = GLOSSARY_LANG) {
  const entry = GLOSSARY[term];
  if (!entry) return '';
  const t = entry[lang] || entry.de;
  return t.short || t.long;
}

// Volle Erklärung (Glossar-Block am Seitenende). Fallback long → short.
export function glossaryFull(term, lang = GLOSSARY_LANG) {
  const entry = GLOSSARY[term];
  if (!entry) return '';
  const t = entry[lang] || entry.de;
  return t.long || t.short;
}
