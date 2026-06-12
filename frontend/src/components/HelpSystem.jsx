import React, { useEffect, useId, useRef, useState } from 'react';
import { GLOSSARY, glossaryDefinition } from '../glossary';
import {
  isPlaceholderText,
  sectionHelpLong,
  sectionHelpShort,
  sectionHelpTitle,
} from '../section_help';

// Erklär-System — zentrales Modul (Sprint Bereichs-Erklärtexte, Commit A).
//
// HelpTooltip und GlossaryHint sind 1:1 aus InsightWeekly.jsx hierher
// umgezogen (reiner Umzug, kein Verhaltenswechsel), damit Landing-Seite,
// Roundup-Block und Admin-Bereich sie ebenfalls importieren können —
// vorher waren sie modul-privat im Pair-Brief. Die CSS-Klassen
// (help-tooltip-*) bleiben unverändert in styles.css.

// ---- HelpTooltip (V3 Sprint 3) ----------------------------------------
//
// Inline question-mark trigger that exposes a short explanation on hover
// (Desktop) and on tap (Mobile). Escape and click-outside close the
// open tooltip. The trigger is a real <button> for keyboard accessibility;
// the popup carries `role="tooltip"` and the trigger references it via
// `aria-describedby` while open.
//
// We don't use `title=` because that limits styling, can't carry rich
// content, and behaves inconsistently on touch devices.

export function HelpTooltip({ text, label = 'Erklärung anzeigen' }) {
  const [open, setOpen] = useState(false);
  const tooltipId = useId();
  const wrapperRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    function handlePointerDown(e) {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target)) {
        setOpen(false);
      }
    }
    function handleKey(e) {
      if (e.key === 'Escape') setOpen(false);
    }
    document.addEventListener('mousedown', handlePointerDown);
    document.addEventListener('touchstart', handlePointerDown);
    document.addEventListener('keydown', handleKey);
    return () => {
      document.removeEventListener('mousedown', handlePointerDown);
      document.removeEventListener('touchstart', handlePointerDown);
      document.removeEventListener('keydown', handleKey);
    };
  }, [open]);

  return (
    <span className="help-tooltip-wrapper" ref={wrapperRef}>
      <button
        type="button"
        className="help-tooltip-trigger"
        aria-label={label}
        aria-describedby={open ? tooltipId : undefined}
        aria-expanded={open}
        onClick={(e) => { e.preventDefault(); setOpen((o) => !o); }}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        tabIndex={0}
      >
        ?
      </button>
      {open && (
        <span className="help-tooltip-content" id={tooltipId} role="tooltip">
          {text}
        </span>
      )}
    </span>
  );
}

// ---- GlossaryHint (V3 Sprint 8 A) --------------------------------------
//
// Info-Punkt am Begriff. Dünner Wrapper über das UNVERÄNDERTE HelpTooltip:
// nimmt einen Glossar-Key und rendert die Kurzerklärung aus der zentralen
// Quelle (glossary.js). So speisen sich Info-Punkte und der Gesamt-
// Glossar-Block aus derselben Quelle. Rendert nichts bei unbekanntem Key.

export function GlossaryHint({ term }) {
  const entry = GLOSSARY[term];
  if (!entry) return null;
  return (
    <HelpTooltip
      text={glossaryDefinition(term)}
      label={`Was bedeutet ${entry.de.term}?`}
    />
  );
}

// ---- SectionHelp (Sprint Bereichs-Erklärtexte, Commit C) ----------------
//
// Zwei Bausteine pro Bereich, gespeist aus section_help.js (eine Quelle,
// gleiche Konvention wie glossary.js):
//
// - SectionHelpHint  → der kurze „was ist das"-Tooltip AM Sektionstitel
//   (inline neben dem h2/h3 platzieren; bei CollapsibleCards neben dem
//   Header-Button — der Trigger darf nicht IN den Button, nested buttons
//   sind invalides HTML).
// - SectionHelpPanel → der ausklappbare Erklär-Absatz („was zeigt es /
//   wofür nutzbar / worauf achten") direkt unter dem Titel. Natives
//   <details>, default zu — gleiches dependency-freie Muster wie der
//   GlossaryBlock.
//
// Beide rendern nichts bei unbekanntem Key (graceful degrade). Solange
// section_help.js Platzhalter trägt ([TODO …]), rendert das Panel den
// Text gedämpft (is-placeholder) — klar als Zwischenstand erkennbar,
// kein Layout-Bruch. ``onDark`` schaltet die Farbvariante für die
// dunklen Landing-Blöcke (Studios/Roundups).

export function SectionHelpHint({ sectionKey }) {
  const short = sectionHelpShort(sectionKey);
  if (!short) return null;
  const title = sectionHelpTitle(sectionKey);
  return (
    <HelpTooltip
      text={short}
      label={title ? `Was ist „${title}“?` : 'Was ist diese Sektion?'}
    />
  );
}

export function SectionHelpPanel({ sectionKey, onDark = false }) {
  const long = sectionHelpLong(sectionKey);
  if (!long) return null;
  return (
    <details className={`section-help${onDark ? ' section-help--on-dark' : ''}`}>
      <summary className="section-help-summary">Was zeigt diese Sektion?</summary>
      <p className={`section-help-text${isPlaceholderText(long) ? ' is-placeholder' : ''}`}>
        {long}
      </p>
    </details>
  );
}
