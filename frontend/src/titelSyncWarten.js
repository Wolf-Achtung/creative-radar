// Titel-Sync-Polling (21.08.2026): der Sync antwortet seit dem Umbau
// sofort mit 202 und laeuft im Hintergrund — das Frontend wartet nicht
// mehr auf die (minutenlange) Antwort, sondern pollt GET /sync/runs,
// bis ein NEUER Lauf fertig ist. "Neu" heisst: andere Row-Id als der
// juengste Lauf VOR dem Start — sonst wuerde ein alter success-Lauf
// sofort als Ergebnis durchgehen, bevor die neue Row ueberhaupt
// angelegt ist.

export async function neuesterLauf(fetchRuns) {
  const runs = await fetchRuns();
  return Array.isArray(runs) && runs.length ? runs[0] : null;
}

export async function warteAufTitelSyncEnde(fetchRuns, vorherigeLaufId, {
  intervallMs = 15000,
  maxVersuche = 120,
  warte = (ms) => new Promise((resolve) => { setTimeout(resolve, ms); }),
} = {}) {
  for (let versuch = 0; versuch < maxVersuche; versuch += 1) {
    await warte(intervallMs);
    let lauf = null;
    try {
      lauf = await neuesterLauf(fetchRuns);
    } catch (_) {
      continue; // einzelner Fetch-Fehler beendet das Warten nicht
    }
    if (!lauf || lauf.id === vorherigeLaufId) continue; // neue Row noch nicht da
    if (lauf.status !== 'running') return lauf;
  }
  return null; // Zeitbudget erschoepft — Lauf laeuft womoeglich noch
}
