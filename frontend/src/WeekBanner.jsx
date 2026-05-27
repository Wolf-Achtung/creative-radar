import React from 'react';

// Brief-Header UX-Sprint (17.05.2026) — Sticky-Sub-Banner unter dem Hero,
// der die Wochenzuordnung permanent sichtbar haelt. Komplementaer zur
// Stale-Warning aus PR #148: dort konditional ab 7d/14d, hier durchgaengig.

const MONTHS_DE = [
  'Januar', 'Februar', 'März', 'April', 'Mai', 'Juni',
  'Juli', 'August', 'September', 'Oktober', 'November', 'Dezember',
];

// ISO 8601: KW 1 ist die Woche mit dem 4. Januar. Algorithmus rein in UTC,
// damit die Berechnung der Montag-Sonntag-Grenzen unabhaengig von der
// User-Zeitzone deterministisch bleibt — eine ISO-Woche ist ein Kalender-
// Konstrukt, kein Wall-Clock-Konstrukt.
function computeIsoWeekRange(isoYear, isoWeek) {
  const jan4 = new Date(Date.UTC(isoYear, 0, 4));
  const jan4Day = jan4.getUTCDay() || 7;
  const week1Monday = new Date(jan4);
  week1Monday.setUTCDate(jan4.getUTCDate() - (jan4Day - 1));
  const start = new Date(week1Monday);
  start.setUTCDate(week1Monday.getUTCDate() + (isoWeek - 1) * 7);
  const end = new Date(start);
  end.setUTCDate(start.getUTCDate() + 6);
  return { start, end };
}

function formatGermanDateRange(start, end) {
  const sD = start.getUTCDate();
  const eD = end.getUTCDate();
  const sM = MONTHS_DE[start.getUTCMonth()];
  const eM = MONTHS_DE[end.getUTCMonth()];
  const sY = start.getUTCFullYear();
  const eY = end.getUTCFullYear();
  if (sY === eY && sM === eM) {
    return `${sD}.–${eD}. ${eM} ${eY}`;
  }
  if (sY === eY) {
    return `${sD}. ${sM} – ${eD}. ${eM} ${eY}`;
  }
  return `${sD}. ${sM} ${sY} – ${eD}. ${eM} ${eY}`;
}

// "17.05." — Tag.Monat mit fuehrender Null, ohne Jahr (Jahr steht im
// Wochenbereich-Slot direkt nebendran). Lokale Zeitzone — die User-
// Wahrnehmung von "wann wurde der Brief erstellt" folgt der Wall-Clock-
// Zeit, nicht UTC. Konsistent mit ``formatDateISO`` in InsightWeekly.jsx.
export function formatDateShort(value) {
  if (!value) return '—';
  try {
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return String(value);
    const day = String(d.getDate()).padStart(2, '0');
    const month = String(d.getMonth() + 1).padStart(2, '0');
    return `${day}.${month}.`;
  } catch (_) {
    return String(value);
  }
}

// Datenfenster-Spanne aus generatedAt und windowDays ableiten. Spiegelt
// die Backend-Aggregation (window_end = now zur Generierungszeit,
// window_start = window_end - windowDays). Wenn entweder generatedAt oder
// windowDays fehlen (Pre-Sprint-Briefs ohne window_days-Persistenz), fallen
// wir auf den ISO-Wochen-Bereich zurueck — das war das Verhalten vor 27.05.
function computeDataWindowRange(generatedAt, windowDays) {
  if (!generatedAt || !windowDays) return null;
  const end = new Date(generatedAt);
  if (Number.isNaN(end.getTime())) return null;
  const start = new Date(end);
  start.setUTCDate(end.getUTCDate() - windowDays);
  return { start, end };
}

export default function WeekBanner({ isoYear, isoWeek, generatedAt, windowDays }) {
  if (!isoYear || !isoWeek) return null;
  // Header-Wording 27.05.2026: "Wochenanalyse" war irrefuehrend, weil der
  // Brief 30 Tage aggregiert (aggregate_pair window_days=30), nicht eine
  // Kalenderwoche. KW bleibt als Cohort-Identifier (der Brief ist persistent
  // unter (pair, iso_year, iso_week) gekeyed), das Datenfenster wird nun
  // explizit gezeigt. Fallback auf die ISO-Wochen-Spanne nur fuer Pre-
  // Sprint-Briefs ohne windowDays-Persistenz.
  const dataWindow = computeDataWindowRange(generatedAt, windowDays)
    || computeIsoWeekRange(isoYear, isoWeek);
  const range = formatGermanDateRange(dataWindow.start, dataWindow.end);
  const windowLabel = windowDays ? `${windowDays}-Tage-Fenster` : 'Wochenfenster';
  const updatedDate = formatDateShort(generatedAt);

  return (
    <div className="week-banner">
      <span className="week-banner__range">
        Studio-Brief KW {isoWeek} · {windowLabel} {range}
      </span>
      <span className="week-banner__updated">
        Aktualisiert: {updatedDate}
      </span>
    </div>
  );
}
