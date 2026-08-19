import React, { useState } from 'react';

export function ImagePreview({ imageUrl, evidenceQuality, sources = [] }) {
  // Callers pass a sources array of candidate URLs (proposal cards use
  // display_image_candidates from the backend; review/asset cards use the raw
  // asset URL fields). Iterate via onError until one loads or all are spent.
  // imageUrl is kept as a single-URL shortcut for any caller still using it.
  const candidates = sources.length > 0 ? sources.filter(Boolean) : (imageUrl ? [imageUrl] : []);
  const [index, setIndex] = useState(0);

  // Wartung 20.08.2026 — vorher:
  //   useEffect(() => { setIndex(0); }, [candidates.join('|')]);
  //
  // Derselbe Fall wie in ``CollapsibleCard``: abgeleiteter Zustand, per
  // Effect nachgezogen. Das war hier nicht nur unsauber, sondern sichtbar
  // falsch. Der Effect laeuft NACH dem Render, also rendert React bei
  // einem Wechsel der Kandidatenliste zuerst einmal mit dem ALTEN Index:
  //
  //   Liste A hat 3 Quellen, zwei davon sind kaputt -> index steht auf 2.
  //   Liste B kommt mit einer Quelle. Erster Render: index 2 >= 1, also
  //   zeigt die Karte fuer einen Frame "Bild laedt nicht" — obwohl die
  //   neue Quelle noch gar nicht probiert wurde. Erst der Effect setzt
  //   zurueck und der zweite Render zeigt das Bild.
  //
  // Die dokumentierte Form ist die Anpassung waehrend des Renders: React
  // verwirft den angefangenen Render und faengt sofort mit dem neuen
  // Index neu an, ohne dazwischen etwas auf den Bildschirm zu bringen.
  const kandidatenSchluessel = candidates.join('|');
  const [vorigerSchluessel, setVorigerSchluessel] = useState(kandidatenSchluessel);
  if (kandidatenSchluessel !== vorigerSchluessel) {
    setVorigerSchluessel(kandidatenSchluessel);
    setIndex(0);
  }

  if (candidates.length === 0) {
    const subtitle = evidenceQuality === 'missing' ? 'Keine Bildquelle erfasst' : 'Bildquelle nicht verfügbar';
    return (
      <div className="preview-placeholder preview-placeholder--no-source">
        <strong>Kein Bild</strong>
        <span>{subtitle}</span>
      </div>
    );
  }

  if (index >= candidates.length) {
    return (
      <div className="preview-placeholder preview-placeholder--load-failed">
        <strong>Bild lädt nicht</strong>
        <span>Quelle nicht erreichbar</span>
      </div>
    );
  }

  return (
    <img
      src={candidates[index]}
      alt="Creative Vorschau"
      onError={() => setIndex((current) => current + 1)}
    />
  );
}
