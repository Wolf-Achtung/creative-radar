import React from 'react';
import { formatNumber } from '../format';

export function MetricStrip({ asset }) {
  const metrics = [
    ['Views', asset.visible_views],
    ['Likes', asset.visible_likes],
    ['Shares', asset.visible_shares],
    ['Comments', asset.visible_comments],
  ];
  return (
    <div className="metric-strip" aria-label="Öffentlich sichtbare Kennzahlen">
      {metrics.map(([label, value]) => (
        <span key={label}><b>{formatNumber(value)}</b><small>{label}</small></span>
      ))}
    </div>
  );
}
