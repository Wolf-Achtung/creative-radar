import React from 'react';

const ACTION_HELP = [
  ['Für Report freigeben', 'Der Treffer ist relevant und kommt in den Report-Anhang.'],
  ['Als Top-Fund markieren', 'Besonders stark oder strategisch wichtig; erscheint prominent im Weekly Report.'],
  ['Später prüfen', 'Noch unsicher; bleibt in der Prüfliste und wird nicht in den Report übernommen.'],
  ['Aussortieren', 'Für diese Beobachtung nicht brauchbar; wird ausgeblendet und nicht berichtet.'],
  ['Bild/Text analysieren', 'Bild/Text automatisch auswerten: Titel-/Claim-Platzierung, Kinetic, OCR.'],
];

export function ReviewGuide() {
  return (
    <div className="review-guide">
      <div>
        <h3>So entsteht der Weekly Report</h3>
        <ol>
          <li><strong>Filmtitel prüfen:</strong> Ist der Treffer einem Film oder einer Franchise zuordenbar?</li>
          <li><strong>Relevanz entscheiden:</strong> Ist es ein brauchbares Creative-Muster?</li>
          <li><strong>Highlight setzen:</strong> Nur besonders auffällige oder strategisch wichtige Treffer markieren.</li>
          <li><strong>Visual prüfen:</strong> Bei Titel/Claim/Kinetic-Verdacht die Bild-/Text-Prüfung starten.</li>
        </ol>
      </div>
      <div className="action-help">
        <h3>Was bedeuten die Aktionen?</h3>
        {ACTION_HELP.map(([label, text]) => <p key={label}><strong>{label}:</strong> {text}</p>)}
      </div>
    </div>
  );
}
