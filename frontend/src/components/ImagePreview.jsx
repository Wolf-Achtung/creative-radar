import React, { useEffect, useState } from 'react';

export function ImagePreview({ imageUrl, evidenceQuality, sources = [] }) {
  // Callers pass a sources array of candidate URLs (proposal cards use
  // display_image_candidates from the backend; review/asset cards use the raw
  // asset URL fields). Iterate via onError until one loads or all are spent.
  // imageUrl is kept as a single-URL shortcut for any caller still using it.
  const candidates = sources.length > 0 ? sources.filter(Boolean) : (imageUrl ? [imageUrl] : []);
  const [index, setIndex] = useState(0);

  useEffect(() => { setIndex(0); }, [candidates.join('|')]);

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
