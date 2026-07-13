import React from 'react';
import { SectionHelpHint, SectionHelpPanel } from './HelpSystem';

// Sprint Bereichs-Erklärtexte (Commit C): optionaler ``helpKey`` hängt den
// „was ist das"-Tooltip an den Titel und rendert den ausklappbaren
// Erklär-Absatz (SectionHelpPanel) direkt darunter — gespeist aus
// section_help.js. Ohne ``helpKey`` unverändertes Verhalten.
export function Section({ title, kicker, children, className = '', helpKey = null }) {
  return (
    <section className={`card ${className}`}>
      {kicker && <p className="section-kicker">{kicker}</p>}
      <h2>{title}{helpKey && <SectionHelpHint sectionKey={helpKey} />}</h2>
      {helpKey && <SectionHelpPanel sectionKey={helpKey} />}
      {children}
    </section>
  );
}
