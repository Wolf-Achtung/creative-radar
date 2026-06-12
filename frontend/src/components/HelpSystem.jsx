import React, { useEffect, useId, useRef, useState } from 'react';
import { GLOSSARY, glossaryDefinition } from '../glossary';

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
