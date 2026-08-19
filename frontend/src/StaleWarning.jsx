import React, { useMemo, useState } from 'react';

// Schwellen in Tagen, gemessen ab `generated_at` bis `Date.now()`.
// Default-Spec (Briefing 17.05.2026 §B1, Wolf-bestätigt):
//   ≤ 7 Tage  → fresh (keine Warnung)
//   8 - 14    → aged  (orange Info-Box, role="status")
//   > 14      → stale (rote Critical-Box, role="alert")
const AGED_THRESHOLD_DAYS = 7;
const STALE_THRESHOLD_DAYS = 14;
const MS_PER_DAY = 1000 * 60 * 60 * 24;

export default function StaleWarning({ generatedAt }) {
  // Wartung 20.08.2026 — ``Date.now()`` stand vorher direkt im useMemo.
  // Das ist waehrend des Renders unrein (react-hooks/purity), und die
  // Regel zeigt hier auf einen echten Widerspruch: der useMemo haengt an
  // ``generatedAt``, nicht an der Zeit. Das Alter wurde also beim ersten
  // Rendern berechnet und danach nie wieder — bei einem ueber Nacht
  // offenen Tab blieb die Warnung auf dem Stand von gestern stehen.
  //
  // Der Zeitpunkt wird jetzt EINMAL beim Mounten genommen und ist damit
  // ein ausgesprochener Stichtag statt eines zufaelligen. Die Wirkung
  // entspricht dem bisherigen Verhalten (``generatedAt`` aendert sich
  // waehrend einer Sitzung praktisch nie, und die Schwellen sind in
  // TAGEN — ein paar Minuten Drift sind bedeutungslos); neu ist nur,
  // dass es dasteht, statt sich aus der Memo-Mechanik zu ergeben.
  //
  // Mitwachsen waehrend die Seite offen ist waere ein Timer und damit
  // eine andere Entscheidung — nicht Teil dieses Lint-Durchgangs.
  const [jetzt] = useState(() => Date.now());
  const ageDays = useMemo(() => {
    if (!generatedAt) return null;
    const ts = new Date(generatedAt).getTime();
    if (Number.isNaN(ts)) return null;
    return Math.floor((jetzt - ts) / MS_PER_DAY);
  }, [generatedAt, jetzt]);

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
