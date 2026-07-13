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

export function formatPct1(fraction) {
  if (fraction === null || fraction === undefined) return '—';
  return `${(Number(fraction) * 100).toFixed(1)}%`;
}

export function formatMultiplier(value) {
  if (value === null || value === undefined) return '—';
  return `${Number(value).toFixed(1)}x`;
}
