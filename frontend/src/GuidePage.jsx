import React from 'react';
import { SiteFooter } from './components/SiteFooter';

// Anleitung fuer neue Nutzer (Wolf-Wunsch 2026-07-20): wie nutzt man
// Creative Radar sinnvoll — allgemeiner Aufbau plus drei Rollen-
// Abschnitte (Motion-Designer, Cutter/Editoren, PMs/Kontakter).
// Reine Inhalts-Seite, kein API-Call; liegt hinter dem Login-Gate
// (Route /anleitung in App.jsx), verlinkt aus Startseite und Footer.
//
// Inhaltliche Anker sind die REALEN Sektionen des Wochenbriefs
// (InsightWeekly.jsx): Headline, Breakouts, Top-Posts, Film-
// Detailansicht, Markt-Zeitreihe, ER-Prognose, „Diese Woche im
// Detail", „Kreativ-Empfehlungen" (mit Unterpunkten Cutter /
// Motion-Designer / Creative Producer), Plattform-Details, Methodik.
// Wenn Brief-Sektionen umbenannt werden, hier mitziehen.

const ROLES = [
  {
    key: 'motion',
    title: 'Für Motion-Designer',
    intro: 'Dein Fokus: Wie sehen die Assets aus, die diese Woche funktioniert haben — und was davon lässt sich auf eigene Projekte übertragen?',
    steps: [
      'Öffne die Studios, die zu deinem aktuellen Projekt passen, und geh direkt zu den Top-Posts. Jeder Post ist zum Original verlinkt — schau dir Key Art, Typo-Animationen und Format-Entscheidungen im Original an.',
      'Im Brief findest du unter „Kreativ-Empfehlungen" einen eigenen Motion-Designer-Block: konkrete Gestaltungs-Beobachtungen der Woche mit Verweis auf die belegenden Posts.',
      'Nutze den Drei-Märkte-Vergleich als Stil-Radar: dieselbe Kampagne läuft in US, DE und UK oft mit unterschiedlichem Artwork und Schnitt — die Unterschiede zeigen, was pro Markt als wirksam gilt.',
      'Die Sammel-Kacheln „Verleiher & Independents" lohnen sich als Ideen-Quelle abseits des Mainstream-Looks — gerade die Independent-Segmente probieren visuell mehr aus.',
    ],
    example: 'Beispiel: Du baust Key Art für einen DE-Release. Im Brief des US-Studios siehst du, welches Motiv dort die höchste Interaktion holt — und in der Film-Detailansicht, ob der DE-Kanal schon eine eigene Variante fährt.',
  },
  {
    key: 'cutter',
    title: 'Für Cutter / Editoren',
    intro: 'Dein Fokus: Welche Schnitte, Längen und Openings performen — pro Plattform und pro Markt?',
    steps: [
      'Starte bei den Breakouts im Brief: Posts, die deutlich über dem Kanal-Schnitt liegen. Öffne die Originale und achte auf die ersten drei Sekunden — dort entscheidet sich die Performance.',
      'Unter „Kreativ-Empfehlungen" gibt es einen eigenen Cutter-Block mit den Schnitt-Beobachtungen der Woche, jeweils mit Beleg-Posts.',
      'Die Plattform-Details zeigen, wie sich TikTok, Instagram und YouTube unterscheiden — was auf TikTok als 15-Sekünder läuft, braucht auf YouTube oft eine andere Dramaturgie.',
      'Vor dem eigenen Schnitt zu einem Titel: Film-Detailansicht öffnen und den bisherigen Kampagnenverlauf des Films über alle Märkte ansehen — welche Asset-Typen (Teaser, Trailer, Clip) wurden schon gespielt und wie kamen sie an?',
    ],
    example: 'Beispiel: Du schneidest einen DE-Trailer für einen US-Titel. Die Markt-Zeitreihe zeigt dir, wann die US-Kampagne Fahrt aufgenommen hat, und die Top-Posts zeigen, welcher Trailer-Cut dort die Interaktion geholt hat.',
  },
  {
    key: 'pm',
    title: 'Für PMs / Kontakter',
    intro: 'Dein Fokus: Gesprächsgrundlage nach außen (Kunde/Studio) und Briefing-Material nach innen (Team) — belegbar statt gefühlt.',
    steps: [
      'Der Montag-Rhythmus: Jede Woche nach Wochenabschluss stehen frische Briefs bereit. Ein 10-Minuten-Durchgang durch „deine" Studios reicht, um sprechfähig zu sein.',
      'Die Headline und „Diese Woche im Detail" fassen pro Studio zusammen, was auffiel — inklusive Watch-Outs und Konkurrenz-Blick. Das ist dein Kunden-Update; die verlinkten Posts sind deine Belege.',
      'Der Drei-Märkte-Vergleich ist dein Pitch-Argument: „In US läuft die Kampagne bereits mit X — im DE-Feed fehlt das noch" lässt sich direkt aus dem Brief zeigen.',
      'Die ER-Prognose ehrlich kommunizieren: Märkte ohne belastbaren Trend werden bewusst als „zu schwankend" ausgewiesen statt mit einer Scheinzahl. Ein „dazu machen wir keine Prognose" ist ein Feature, kein Mangel.',
      'Für interne Briefings: Kreativ-Empfehlungen (Cutter/Motion-Designer/Creative Producer) direkt ans Team weiterreichen — die Blöcke sind dafür geschrieben.',
    ],
    example: 'Beispiel: Kunden-Jour-fixe am Dienstag. Montagabend den Studio-Brief öffnen, Headline + zwei Beleg-Posts notieren, einen Blick auf Breakouts und Prognose — fertig ist das fundierte Update in unter 15 Minuten.',
  },
];

export default function GuidePage() {
  return (
    <main>
      <header className="hero" style={{ background: '#1f4d4d' }}>
        <div>
          <a href="/" className="hero__back-link">← zur Übersicht</a>
          <p className="eyebrow">Anleitung</p>
          <h1>So nutzt du Creative Radar</h1>
          <p>Der schnellste Weg zu verwertbaren Erkenntnissen — allgemein und für deine Rolle.</p>
        </div>
      </header>

      <section className="card">
        <h2>So ist die Seite aufgebaut</h2>
        <p>
          <strong>Die großen Studios — einzeln im Blick:</strong> Eine Kachel pro Studio, jeweils im
          Drei-Märkte-Vergleich DE · US · UK. Ein Klick öffnet den vollständigen Wochenbrief mit
          Headline, Breakouts, Top-Posts (zum Original verlinkt), Film-Detailansicht, Markt-Zeitreihe,
          ER-Prognose und Kreativ-Empfehlungen.
        </p>
        <p>
          <strong>Verleiher &amp; Independents — das Feld im Überblick:</strong> Sechs Sammel-Kacheln,
          die jeweils viele kleinere Player eines Segments bündeln (DE, US, UK). Sie klappen direkt auf
          der Startseite auf und zeigen die Linie der Woche — nicht einzelne Anbieter.
        </p>
        <p>
          <strong>Rhythmus:</strong> Die Daten beziehen sich auf die zuletzt abgeschlossene Kalenderwoche
          und werden montags automatisch aktualisiert. Alle Aussagen in den Briefs sind mit den
          zugrunde liegenden Posts belegt — im Zweifel immer das Original öffnen.
        </p>
        <p className="muted">
          {'Tipp: Fast jede Sektion hat ein kleines „?" bzw. „Was zeigt diese Sektion?" — dahinter steckt eine Kurz-Erklärung direkt an Ort und Stelle.'}
        </p>
      </section>

      {ROLES.map((role) => (
        <section key={role.key} className="card">
          <h2>{role.title}</h2>
          <p>{role.intro}</p>
          <ol className="guide-steps">
            {role.steps.map((step, index) => (
              <li key={index}>{step}</li>
            ))}
          </ol>
          <p className="guide-example">{role.example}</p>
        </section>
      ))}

      <section className="card">
        <h2>Noch Fragen?</h2>
        <p>
          Kein Zugang, Code kommt nicht an, oder ein Studio fehlt dir? Melde dich beim
          Creative-Radar-Team — Zugänge und Quellen werden zentral gepflegt.
        </p>
      </section>

      <SiteFooter />
    </main>
  );
}
