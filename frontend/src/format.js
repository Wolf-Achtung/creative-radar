// Platin 5 (2026-07-13) — reine, seiteneffektfreie Helper aus App.jsx
// ausgelagert (Formatierung, Titel-Heuristiken, Pair-Kachel-Defaults).
// Erster Schritt der Frontend-Modularisierung: NUR Logik ohne JSX/Hooks,
// damit sie ohne Rendering-Umgebung (jsdom) unit-testbar ist. Verhalten
// ist 1:1 identisch zum vorherigen In-File-Code — reine Verschiebung,
// keine Änderung. Siehe format.test.js für die Testabdeckung.

export function formatNumber(value) {
  if (value === null || value === undefined || value === '') return '—';
  try { return new Intl.NumberFormat('de-DE').format(Number(value)); } catch (_) { return String(value); }
}

export function formatDate(value) {
  if (!value) return 'Datum offen';
  try { return new Date(value).toLocaleDateString('de-DE'); } catch (_) { return 'Datum offen'; }
}

export function formatDateTime(value) {
  if (!value) return '—';
  try { return new Date(value).toLocaleString('de-DE'); } catch (_) { return '—'; }
}

// Diagnose-Folge 2026-07-06: read-only Anzeige des letzten Cron-Laufs, ohne
// selbst einen Lauf anzustossen (im Unterschied zum Polling in
// triggerFullSync). Deutschsprachige Statuslabels fuer die vier moeglichen
// CronRun.status-Werte (siehe cron.py).
const CRON_RUN_STATUS_LABELS = {
  running: 'läuft noch',
  completed: 'abgeschlossen',
  failed: 'fehlgeschlagen',
  error: 'Fehler',
  budget_exceeded: 'abgebrochen (Budget)',
};

export function formatCronRunStatus(status) {
  return CRON_RUN_STATUS_LABELS[status] || status || '—';
}

export function clip(text, max = 260) {
  if (!text) return '';
  return text.length > max ? `${text.slice(0, max).trim()} …` : text;
}

// Titel-Suche in der Entscheidungs-Queue (21.08.2026, Wolfs Befund):
// das Zuordnungs-Dropdown listete zehntausende Titel und fand „Lügen
// über meine Mutter" nicht per Tippen — Umlaute und Teilworte brauchten
// den exakten String. Die Suche normalisiert beide Seiten
// akzent-unabhaengig (ü→u, é→e), sodass „lugen" den Umlaut-Titel findet.
export function normalizeTitleForSearch(value) {
  return String(value || '')
    .toLowerCase()
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/\s+/g, ' ')
    .trim();
}

export function searchTitles(titles, query, limit = 6) {
  const q = normalizeTitleForSearch(query);
  if (q.length < 2) return [];
  const treffer = [];
  for (const title of titles || []) {
    const felder = [title.title_original, title.title_local, title.franchise, ...(title.aliases || [])];
    if (felder.filter(Boolean).some((feld) => normalizeTitleForSearch(feld).includes(q))) {
      treffer.push(title);
      if (treffer.length >= limit) break;
    }
  }
  return treffer;
}

export function normalizeHandle(value) {
  const clean = (value || '').trim().replace(/^@/, '').replace(/\/$/, '');
  if (clean.includes('tiktok.com/@')) return clean.split('tiktok.com/@')[1].split('/')[0];
  return clean;
}

export function textPool(asset) {
  return [
    asset.title_name,
    asset.title_local,
    asset.franchise,
    asset.placement_title_text,
    asset.caption,
    asset.ai_summary_de,
    asset.ai_summary_en,
    asset.ai_trend_notes,
    asset.ocr_text,
    asset.kinetic_text,
  ].filter(Boolean).join(' ');
}

export function titleFromHashtag(tag) {
  const stripped = tag.replace(/^#/, '').replace(/movie|film|official|trailer|themovie$/gi, '');
  const words = stripped
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .replace(/[_-]+/g, ' ')
    .trim();
  return words.length >= 3 ? words : '';
}

export function inferTitleHint(asset, titles) {
  if (asset.title_name || asset.placement_title_text) {
    return { label: asset.title_name || asset.placement_title_text, source: 'zugeordnet/erkannt' };
  }
  const pool = textPool(asset);
  const poolLower = pool.toLowerCase();
  const matchedTitle = titles.find((title) => {
    const candidates = [title.title_original, title.title_local, title.franchise, ...(title.keywords || [])].filter(Boolean);
    return candidates.some((candidate) => poolLower.includes(String(candidate).toLowerCase()));
  });
  if (matchedTitle) return { label: matchedTitle.title_original, source: 'Vorschlag aus Text/Caption' };

  const hashtags = [...pool.matchAll(/#([A-Za-z][A-Za-z0-9_]{3,})/g)].map((m) => titleFromHashtag(m[0])).filter(Boolean);
  const useful = hashtags.find((tag) => !/fyp|viral|cinema|only in theaters|trailer/i.test(tag));
  if (useful) return { label: useful, source: 'Vorschlag aus Hashtag' };

  const quoted = pool.match(/[„“"]([^„“"]{3,44})[„“"]/);
  if (quoted?.[1]) return { label: quoted[1], source: 'Vorschlag aus Text' };
  return { label: 'Filmtitel noch offen', source: 'Bitte zuordnen' };
}

export function getAssetDisplayTitle(asset, titles) {
  const hint = inferTitleHint(asset, titles);
  if (asset.title_name || asset.title_id) {
    return `Automatisch erkannt: ${hint.label}`;
  }
  if (hint.label && hint.label !== 'Filmtitel noch offen' && hint.label !== 'Filmtitel offen') {
    return `Vorschlag: ${hint.label}`;
  }
  return 'Filmtitel bitte zuordnen';
}

// Candidate-Review (Sprint Candidate-Wiring): einen OPEN-TitleCandidate-
// ``suggested_title`` auf einen EXAKTEN aktiven Title abbilden (title_original
// / title_local / aliases, case-insensitiv). Null wenn kein exakter Treffer —
// dann ist kein Blind-Bestätigen möglich, der Admin ordnet manuell zu.
export function findExactTitleForSuggestion(suggestedTitle, titles) {
  if (!suggestedTitle) return null;
  const needle = String(suggestedTitle).trim().toLowerCase();
  if (!needle) return null;
  return (titles || []).find((t) =>
    [t.title_original, t.title_local, ...(t.aliases || [])]
      .filter(Boolean)
      .some((v) => String(v).trim().toLowerCase() === needle),
  ) || null;
}

export function computePairSectionDefaults(pairs) {
  if (!pairs || pairs.length === 0) {
    return { defaultMarkets: [], defaultFrequency: null, defaultKey: '' };
  }
  const tally = (values) => {
    const counts = new Map();
    for (const v of values) counts.set(v, (counts.get(v) || 0) + 1);
    let best = null;
    let bestCount = 0;
    for (const [v, c] of counts.entries()) {
      if (c > bestCount) { best = v; bestCount = c; }
    }
    return best;
  };
  const marketKeys = pairs.map((p) => [...(p.markets || [])].sort().join('+'));
  const defaultMarketKey = tally(marketKeys) || '';
  const defaultMarkets = defaultMarketKey ? defaultMarketKey.split('+') : [];
  const defaultFrequency = tally(pairs.map((p) => p.frequency_label).filter(Boolean));
  return {
    defaultMarkets,
    defaultFrequency,
    defaultKey: `${defaultMarketKey}|${defaultFrequency || ''}`,
  };
}

// Sprint 28.05.2026 (Studio-Kennzahl) — kompaktes "TT.MM."-Format fuer
// die "Aktualisiert"-Zeile der Kachel. Volles Jahr braucht's hier
// nicht: die Kachel zeigt die juengste Brief-Generierung des Pairs,
// die ist per Definition aus den letzten Wochen. ``null`` /
// nicht-parsbares Datum → leerer String, der Caller schreibt dann das
// Em-Dash "Aktualisiert —".
export function formatPairUpdated(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit' });
}

// Sprint 28.05.2026 (Option B) — Brief ist "stale" wenn die juengste
// generated_at-Zeit mehr als 7 Tage zurueckliegt. Frontend macht das
// transparent ("Aktualisiert TT.MM. — aelter als eine Woche"), damit
// der Nutzer nicht denkt, die Headline waere von dieser Woche, wenn
// der Cron mal hakt. 7 Tage = ein Brief-Rhythmus; danach ist die
// Anzeige "nicht mehr aktuell".
const STALE_THRESHOLD_MS = 7 * 24 * 60 * 60 * 1000;
export function isBriefStale(value) {
  if (!value) return false;
  const ts = new Date(value).getTime();
  if (Number.isNaN(ts)) return false;
  return (Date.now() - ts) > STALE_THRESHOLD_MS;
}

export function pairDeviationLabel(pair, defaults) {
  // Pill-Text wenn die Kachel von der Sektions-Default-Kombi abweicht.
  // ``null`` = Default, kein Pill noetig. Markt-Abweichung wird als
  // Markt-Liste gerendert ("US+UK"), Frequenz-Abweichung als
  // Frequenz-Wort. Beides moeglich.
  const markets = [...(pair.markets || [])].sort().join('+');
  const defaultMarkets = defaults.defaultMarkets.join('+');
  const marketsDiffer = markets !== defaultMarkets;
  const frequencyDiffers = defaults.defaultFrequency
    && pair.frequency_label
    && pair.frequency_label !== defaults.defaultFrequency;
  if (!marketsDiffer && !frequencyDiffers) return null;
  const parts = [];
  if (marketsDiffer) parts.push((pair.markets || []).join('+'));
  if (frequencyDiffers) parts.push(pair.frequency_label);
  return parts.join(' · ');
}

export function formatUsdCents(cents) {
  if (cents === null || cents === undefined) return '—';
  try { return new Intl.NumberFormat('de-DE', { style: 'currency', currency: 'USD' }).format(Number(cents) / 100); }
  catch (_) { return `$${(Number(cents) / 100).toFixed(2)}`; }
}

// Kosten-Audit 2026-08-01: Sub-Cent-Betraege (ein gpt-5.4-mini-Call
// kostet ~0,13 ct) verschwinden in der Cent-Spalte auf 0. Millicents
// sind der praezise Wert (1 Cent = 1000 Millicents); zwei Nachkomma-
// stellen reichen, weil das Feld immer Monatssummen zeigt.
export function formatUsdMillicents(millicents) {
  if (millicents === null || millicents === undefined) return '—';
  return formatUsdCents(Number(millicents) / 1000);
}

export function formatPct1(fraction) {
  if (fraction === null || fraction === undefined) return '—';
  return `${(Number(fraction) * 100).toFixed(1)}%`;
}

export function formatMultiplier(value) {
  if (value === null || value === undefined) return '—';
  return `${Number(value).toFixed(1)}x`;
}


// Katalog-Nachladen (25.08.2026): die Meldung ist der ganze Bericht —
// Wolf entscheidet daran, ob er anlegt. Sie gehoert deshalb hierher,
// wo sie pruefbar ist, und nicht in eine Handler-Funktion.
//
// Zwei Unterscheidungen tragen die Aussage:
//   Vorschau vs. Ernstfall — gleiche Zahlen, andere Bedeutung.
//   "im Katalog mehrdeutig" vs. "bei TMDb nicht eindeutig" — das eine
//   heisst "zwei gleichnamige Titel liegen schon da", das andere "TMDb
//   kennt den Namen nicht eindeutig". Verschiedene Ursachen,
//   verschiedene Reaktionen.
export function formatKatalogNachladen(r) {
  const namen = (r.angelegte_titel || []).length
    ? ` Titel: ${r.angelegte_titel.join(', ')}.`
    : '';
  // Seit 31.08.2026 nennt jede Kategorie ihre Namen (Backend liefert
  // sie gedeckelt mit): "10 bleiben liegen" ohne das WELCHE hiess
  // vorher, die Queue von Hand mit der Zaehlung abzugleichen.
  const mitNamen = (anzahl, text, namen) => {
    const liste = (namen || []).length ? `: ${namen.join(', ')}` : '';
    return `${anzahl} ${text}${liste}`;
  };
  const offen = [];
  if (r.nicht_belegt) {
    offen.push(mitNamen(r.nicht_belegt, 'ohne Text-Beleg', r.nicht_belegt_namen));
  }
  // Der alte Sammel-Zaehler warf "TMDb kennt es nicht" und "TMDb kennt
  // es mehrfach" zusammen — das eine ist echte Handarbeit, das andere
  // nur eine Auswahl (dafuer gibt es die TMDb-Auswahl in der Queue).
  if (r.tmdb_ohne_treffer) {
    offen.push(mitNamen(
      r.tmdb_ohne_treffer,
      'ohne aktuellen TMDb-Treffer (Handarbeit)',
      r.tmdb_ohne_treffer_namen
    ));
  }
  if (r.tmdb_mehrdeutig) {
    offen.push(mitNamen(
      r.tmdb_mehrdeutig,
      'mehrfach bei TMDb — Auswahl in „Treffer prüfen"',
      r.tmdb_mehrdeutig_namen
    ));
  }
  // Altformat vor der Aufteilung (z. B. ein Cron-Summary von vorher).
  if (r.tmdb_unklar) offen.push(`${r.tmdb_unklar} bei TMDb nicht eindeutig`);
  if (r.katalog_mehrdeutig) {
    offen.push(mitNamen(
      r.katalog_mehrdeutig,
      'mit gleichnamigem Titel im Katalog',
      r.katalog_mehrdeutig_namen
    ));
  }
  if (r.fehler) offen.push(`${r.fehler} mit TMDb-Fehler`);
  const liegengelassen = offen.length
    ? ` Bewusst liegen gelassen: ${offen.join(', ')}.`
    : '';
  const rest = r.offen_danach
    ? ` Noch ${r.offen_danach} mit Katalog-Luecke — danach erneut klicken.`
    : '';
  if (r.vorschau) {
    if (!r.geprueft) {
      return 'Vorschau: kein Kandidat traegt einen Katalog-Luecken-Marker. '
        + 'Der entsteht in der KI-Pruefung — es gibt gerade nichts nachzuladen.';
    }
    return `VORSCHAU — es wurde nichts geaendert. ${r.geprueft} geprueft, `
      + `${r.angelegt} Titel wuerden angelegt, ${r.zugeordnet} Treffer zugeordnet.`
      + `${namen}${liegengelassen}${rest}`;
  }
  return `Katalog-Nachladen: ${r.geprueft} geprueft, ${r.angelegt} Titel angelegt, `
    + `${r.zugeordnet} Treffer zugeordnet.${namen}${liegengelassen}${rest}`;
}


// Autopilot-Meldung — bis 31.08.2026 ein Inline-String in AdminApp und
// damit ungetestet (dieselbe Falle wie einst bei formatKatalogNachladen).
export function formatAutopilot(r) {
  if (r?.error) return `Autopilot fehlgeschlagen: ${r.error}`;
  return (
    `Autopilot: ${r.auto_assigned} Vorschläge automatisch bestätigt, `
    + `${r.resolved_already_assigned} bereits zugeordnete geschlossen, `
    + `${r.ignored_stale} alte schwache Vorschläge aussortiert. `
    + `Offen bleiben: ${r.skipped_no_exact_match} ohne Exakt-Treffer, `
    + `${r.skipped_low_confidence} unter der Sicherheits-Schwelle, `
    + `${r.skipped_ambiguous} mehrdeutig.`
  );
}

export function formatKiPruefung(r) {
  if (r?.error) return `KI-Prüfung fehlgeschlagen: ${r.error}`;
  if (r.skipped === 'anthropic_not_configured') {
    return 'KI-Prüfung übersprungen: kein Anthropic-API-Key in dieser Umgebung.';
  }
  const rest = r.offen_danach
    ? ` Noch ${r.offen_danach} ungeprüft — für die nächste Runde einfach erneut klicken.`
    : ' Alle Rest-Vorschläge sind jetzt KI-geprüft — was übrig ist, ist echte Handarbeit (mit KI-Hinweis in „Treffer prüfen").';
  // Der korrigierte Vorschlag ist der Hauptnutzen bei Posts, deren
  // Titel der Katalog nicht kennt (24.08.2026) — ohne ihn liest sich
  // "0 sicher zugeordnet" wie ein Totalausfall, obwohl die Karten
  // jetzt den richtigen Knopf tragen.
  const korrigiert = r.vorschlag_korrigiert
    ? ` ${r.vorschlag_korrigiert} Vorschläge auf den beworbenen Titel korrigiert.`
    : '';
  return (
    `KI-Prüfung: ${r.geprueft} neu geprüft, ${r.zugeordnet} sicher zugeordnet, `
    + `${r.unsicher} zur Hand-Prüfung markiert.${korrigiert}${rest}`
  );
}

// Der Aufräum-Knopf (31.08.2026, Wolfs Befund „viel zu viele Buttons"):
// EIN Klick laesst die Kandidaten-Pipeline in Cron-Reihenfolge laufen —
// diese Meldung ist der Gesamtbericht der drei Schritte.
export function formatVorschlaegeAufraeumen(r) {
  const teile = [formatAutopilot(r.autopilot || {}), formatKiPruefung(r.ki || {})];
  const nachladen = r.nachladen || {};
  if (nachladen.error) {
    teile.push(`Katalog-Nachladen fehlgeschlagen: ${nachladen.error}`);
  } else if (nachladen.skipped) {
    teile.push('Katalog-Nachladen übersprungen (Feature-Flag aus).');
  } else {
    teile.push(formatKatalogNachladen(nachladen));
  }
  return teile.join('\n');
}


// Beschriftung des Anwenden-Knopfes beim Katalog-Nachladen.
//
// Wolfs Lauf vom 25.08.2026: "0 Titel wuerden angelegt, 18 Treffer
// zugeordnet" — und der Knopf war gesperrt, weil ich ihn an
// ``angelegt > 0`` gehaengt hatte. Die Absicht war richtig (ein Klick
// ins Leere soll nicht wie eine Entscheidung aussehen), die Bedingung
// falsch: der Nutzen dieses Laufs lag ganz bei den Zuordnungen.
//
// ``zugeordnet`` deckt beides ab — es zaehlt jede Zuordnung, ob der
// Titel neu angelegt oder schon vorhanden war.
export function formatKatalogAnwendenLabel(vorschau) {
  if (!vorschau || !vorschau.zugeordnet) return 'Übernehmen';
  const teile = [];
  if (vorschau.angelegt) teile.push(`${vorschau.angelegt} Titel anlegen`);
  teile.push(`${vorschau.zugeordnet} Treffer zuordnen`);
  return teile.join(', ');
}


// Kampagnenstart-Label im Monitoring. Vorher stand dort nackt
// ``Math.round(tage/7) + " Wochen vor Release"`` — das ergab
// "1 Wochen", "0 Wochen vor Release" und "-1 Wochen vor Release"
// (Wolfs Staging-Abnahme, 25.08.2026). Negative Vorlauftage heissen:
// der fruehste Post kam NACH dem Release.
// Release-Countdown (Roadmap Schritt 2, 25.08.): die Einordnung eines
// Wir-Projekts gegen den Markt-Kampagnenstart, als ganzer Satz. Hier
// statt im Panel, damit die Fallunterscheidung testbar ist — dieselbe
// Regel wie bei formatKatalogNachladen.
export function formatReleaseEinordnung({ tageBisRelease, marktMedianTage, eigenePosts, eigenerStartTage }) {
  if (tageBisRelease === null || tageBisRelease === undefined) {
    return 'Kein Release-Datum am Titel — ohne Datum keine Zeitleiste.';
  }
  const wochen = (tage) => {
    const n = Math.abs(Math.round(tage / 7));
    return `${n} ${n === 1 ? 'Woche' : 'Wochen'}`;
  };
  if (tageBisRelease < -7) {
    const basis = `Release war vor ${wochen(tageBisRelease)}.`;
    return eigenePosts > 0
      ? `${basis} ${eigenePosts} eigene Posts zugeordnet — Zeit für die Nachlese.`
      : `${basis} Keine eigenen Posts zugeordnet.`;
  }
  const laeuft = eigenePosts > 0 && eigenerStartTage !== null && eigenerStartTage !== undefined;
  if (laeuft) {
    const basis = `Eure Kampagne läuft seit ${wochen(eigenerStartTage)} vor Release (${eigenePosts} Posts).`;
    if (marktMedianTage === null || marktMedianTage === undefined) return basis;
    return eigenerStartTage >= marktMedianTage
      ? `${basis} Vergleichbare Kampagnen starten meist ${wochen(marktMedianTage)} vor dem Release — ihr wart früher dran.`
      : `${basis} Vergleichbare Kampagnen starten meist ${wochen(marktMedianTage)} vor dem Release — ihr habt später begonnen.`;
  }
  const basis = tageBisRelease <= 7
    ? 'Release-Woche, noch keine eigenen Posts zugeordnet.'
    : `Release in ${wochen(tageBisRelease)}, noch keine eigenen Posts zugeordnet.`;
  if (marktMedianTage === null || marktMedianTage === undefined) return basis;
  const bisStartfenster = tageBisRelease - marktMedianTage;
  if (bisStartfenster > 7) {
    return `${basis} Vergleichbare Kampagnen starten meist ${wochen(marktMedianTage)} vor dem Release — bis zum typischen Startfenster sind es noch ${wochen(bisStartfenster)}.`;
  }
  if (bisStartfenster >= -7) {
    return `${basis} Vergleichbare Kampagnen starten meist ${wochen(marktMedianTage)} vor dem Release — ihr seid jetzt im typischen Startfenster.`;
  }
  return `${basis} Vergleichbare Kampagnen starten meist ${wochen(marktMedianTage)} vor dem Release — das typische Startfenster liegt ${wochen(bisStartfenster)} zurück, ihr seid spät dran.`;
}

// Beweis-Loop (Roadmap Schritt 3, 25.08.): eine Empfehlungs-Zelle der
// Snapshot-Woche als ganzer Satz — umgesetzt? gewirkt? Hier statt im
// Panel, damit die Fallunterscheidung testbar ist.
export function formatBeweisZeile({ umgesetzt, medianLiftWir, gewirkt, folgewocheAbgeschlossen }) {
  if (umgesetzt === 0) {
    return folgewocheAbgeschlossen
      ? 'in der Folgewoche nicht umgesetzt.'
      : 'Folgewoche läuft noch — bisher nicht umgesetzt.';
  }
  const posts = `${umgesetzt} ${umgesetzt === 1 ? 'Post' : 'Posts'}`;
  const lift = String(medianLiftWir).replace('.', ',');
  return gewirkt
    ? `${posts} in der Folgewoche, Median ${lift}x — über eurem Kanal-Schnitt, hat gewirkt.`
    : `${posts} in der Folgewoche, Median ${lift}x — unter eurem Kanal-Schnitt geblieben.`;
}

// Referenz-Suche (Layout-Feedback 25.08.): Lift als lesbare Zahl —
// "2377.7699" ist keine Auskunft. Ab 10x zaehlt die Groessenordnung
// (ganze Zahl), darunter eine Nachkommastelle; deutsches Komma.
export function formatLift(lift) {
  if (lift === null || lift === undefined) return '—';
  const gerundet = lift >= 10 ? Math.round(lift) : Math.round(lift * 10) / 10;
  return `${String(gerundet).replace('.', ',')}x`;
}

export function formatKampagnenstart(vorlaufTage) {
  if (vorlaufTage === null || vorlaufTage === undefined) return '—';
  const wochen = Math.round(vorlaufTage / 7);
  if (wochen === 0) return 'in der Release-Woche';
  const n = Math.abs(wochen);
  const einheit = n === 1 ? 'Woche' : 'Wochen';
  return wochen > 0 ? `${n} ${einheit} vor Release` : `${n} ${einheit} nach Release`;
}
