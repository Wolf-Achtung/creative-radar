import React, { useMemo } from 'react';

// Schwellen in Tagen, gemessen ab `generated_at` bis `Date.now()`.
// Default-Spec (Briefing 17.05.2026 §B1, Wolf-bestätigt):
//   ≤ 7 Tage  → fresh (keine Warnung)
//   8 - 14    → aged  (orange Info-Box, role="status")
//   > 14      → stale (rote Critical-Box, role="alert")
const AGED_THRESHOLD_DAYS = 7;
const STALE_THRESHOLD_DAYS = 14;
const MS_PER_DAY = 1000 * 60 * 60 * 24;

export default function StaleWarning({ generatedAt }) {
  const ageDays = useMemo(() => {
    if (!generatedAt) return null;
    const ts = new Date(generatedAt).getTime();
    if (Number.isNaN(ts)) return null;
    return Math.floor((Date.now() - ts) / MS_PER_DAY);
  }, [generatedAt]);

  if (ageDays === null || ageDays <= AGED_THRESHOLD_DAYS) return null;

  const formattedDate = new Date(generatedAt).toLocaleDateString('de-DE');
  const isStale = ageDays > STALE_THRESHOLD_DAYS;

  if (isStale) {
    return (
      <div className="stale-warning stale-warning--critical" role="alert">
        <span className="stale-warning__icon" aria-hidden="true">⛔</span>
        <div>
          <strong>Brief vom {formattedDate}</strong> — älter als zwei Wochen.
          Entscheidungen auf Basis dieses Briefs sind nicht empfohlen.
        </div>
      </div>
    );
  }

  return (
    <div className="stale-warning stale-warning--aged" role="status">
      <span className="stale-warning__icon" aria-hidden="true">⚠</span>
      <div>
        <strong>Brief vom {formattedDate}</strong> — älter als eine Woche.
        Neuer Wochen-Brief steht aus.
      </div>
    </div>
  );
}
