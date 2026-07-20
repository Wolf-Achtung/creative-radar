import React from 'react';
import { SiteFooter } from './components/SiteFooter';

// UX-Audit Befund 1 (2026-07-14) — Impressum & Datenschutz als eigene
// Routen. Ausbau 2026-07-20 (User-Login-Launch): die Datenschutzerklärung
// beschreibt jetzt konkret, was die Anwendung tatsächlich verarbeitet
// (Login per E-Mail-Code, technisch notwendige Cookies, Nutzungsprotokoll,
// Hosting-Dienstleister). Die MIT [ECKIGEN KLAMMERN] markierten Anbieter-
// Angaben im Impressum und beim Verantwortlichen ergänzt Wolf — hier
// stehen bewusst KEINE erfundenen Namen/Adressen. Beide Texte sind
// Entwürfe und ersetzen keine Rechtsberatung.
const LEGAL_CONTENT = {
  impressum: {
    title: 'Impressum',
    sections: [
      {
        heading: 'Angaben gemäß § 5 DDG',
        paragraphs: [
          '[Name des Unternehmens / des Betreibers]',
          '[Straße und Hausnummer]',
          '[PLZ und Ort]',
          'Vertreten durch: [Vor- und Nachname]',
        ],
      },
      {
        heading: 'Kontakt',
        paragraphs: [
          'E-Mail: [Kontakt-E-Mail-Adresse]',
          'Telefon: [Telefonnummer]',
        ],
      },
      {
        heading: 'Umsatzsteuer',
        paragraphs: [
          'Umsatzsteuer-Identifikationsnummer gemäß § 27a UStG: [USt-IdNr., falls vorhanden]',
        ],
      },
      {
        heading: 'Verantwortlich für den Inhalt nach § 18 Abs. 2 MStV',
        paragraphs: ['[Vor- und Nachname, Anschrift]'],
      },
    ],
  },
  datenschutz: {
    title: 'Datenschutzerklärung',
    sections: [
      {
        heading: 'Verantwortlicher',
        paragraphs: [
          'Verantwortlich für die Datenverarbeitung auf dieser Website ist: [Name und Anschrift des Betreibers — identisch zum Impressum], E-Mail: [Kontakt-E-Mail-Adresse].',
        ],
      },
      {
        heading: 'Zugang und Anmeldung (Login per E-Mail-Code)',
        paragraphs: [
          'Creative Radar ist ein geschlossener Bereich für einen festen Nutzerkreis. Für die Anmeldung verarbeiten wir Ihre E-Mail-Adresse, die zuvor vom Betreiber für den Zugang freigeschaltet wurde. Beim Login senden wir Ihnen einen einmalig verwendbaren, 10 Minuten gültigen Zahlencode per E-Mail; der Code wird in unserer Datenbank ausschließlich in verschlüsselter Form (Hash) gespeichert.',
          'Rechtsgrundlage ist Art. 6 Abs. 1 lit. b DSGVO (Bereitstellung des vereinbarten Zugangs). Für den Versand der Code-E-Mails nutzen wir den Dienst Resend (Resend, Inc.) mit Verarbeitung in der EU-Region (Irland).',
        ],
      },
      {
        heading: 'Cookies',
        paragraphs: [
          'Wir setzen ausschließlich technisch notwendige Cookies ein: ein Anmelde-Cookie, das Ihre Sitzung nach dem Login für bis zu 30 Tage aufrechterhält, sowie im Admin-Bereich ein separates Sitzungs-Cookie. Beide Cookies sind für die Funktion des Logins erforderlich (§ 25 Abs. 2 Nr. 2 TDDDG) und enthalten keine Tracking-Merkmale. Tracking-, Analyse- oder Marketing-Cookies von Drittanbietern setzen wir nicht ein.',
        ],
      },
      {
        heading: 'Nutzungsprotokoll im angemeldeten Bereich',
        paragraphs: [
          'Um das Angebot am tatsächlichen Bedarf des Nutzerkreises auszurichten, protokollieren wir für angemeldete Nutzer, welche Bereiche der Anwendung genutzt werden (z. B. „Wochenbrief geöffnet", „Anmeldung"), jeweils mit E-Mail-Adresse, Aktion und Zeitpunkt. Die Auswertung erfolgt ausschließlich intern durch den Betreiber; es findet keine Weitergabe an Dritte und kein Tracking über andere Websites hinweg statt.',
          'Rechtsgrundlage ist Art. 6 Abs. 1 lit. f DSGVO (berechtigtes Interesse an der bedarfsgerechten Weiterentwicklung des geschlossenen Angebots). Sie können dieser Verarbeitung jederzeit mit Wirkung für die Zukunft widersprechen — wenden Sie sich dazu an die oben genannte Kontaktadresse.',
        ],
      },
      {
        heading: 'Hosting und Infrastruktur',
        paragraphs: [
          'Die Website wird über Netlify, Inc. ausgeliefert (Web-Hosting). Anwendungsserver und Datenbank laufen bei Railway Corp. Beim Aufruf der Website verarbeiten diese Dienstleister technisch bedingt Verbindungsdaten (u. a. IP-Adresse, Zeitpunkt, abgerufene Ressource) in Server-Logs. Mit den Dienstleistern bestehen Auftragsverarbeitungsverträge; soweit eine Verarbeitung außerhalb der EU stattfindet, erfolgt sie auf Grundlage der EU-Standardvertragsklauseln bzw. des EU-US Data Privacy Framework.',
        ],
      },
      {
        heading: 'Eingebettete Inhalte von Social-Media-Plattformen',
        paragraphs: [
          'Die dargestellten Analysen zeigen Vorschaubilder öffentlicher Social-Media-Beiträge (Instagram, TikTok, YouTube). Diese werden über unsere eigene Infrastruktur zwischengeladen; erst wenn Sie einen Beitrag aktiv anklicken, verlassen Sie unsere Website und es gelten die Datenschutzbestimmungen der jeweiligen Plattform.',
        ],
      },
      {
        heading: 'Speicherdauer und Ihre Rechte',
        paragraphs: [
          'Login-Codes sind 10 Minuten gültig und werden bei jeder neuen Anforderung ersetzt. Ihr Nutzerkonto (E-Mail-Adresse) und das Nutzungsprotokoll speichern wir, solange Ihr Zugang besteht bzw. solange die Auswertung für den Betrieb erforderlich ist.',
          'Sie haben das Recht auf Auskunft (Art. 15 DSGVO), Berichtigung (Art. 16), Löschung (Art. 17), Einschränkung der Verarbeitung (Art. 18), Datenübertragbarkeit (Art. 20) und Widerspruch (Art. 21) sowie das Recht auf Beschwerde bei einer Datenschutz-Aufsichtsbehörde. Wenden Sie sich für alle Anliegen an die oben genannte Kontaktadresse.',
        ],
      },
    ],
  },
};

export default function LegalPage({ page }) {
  const content = LEGAL_CONTENT[page] || LEGAL_CONTENT.impressum;
  return (
    <main>
      <header className="hero" style={{ background: '#1f4d4d' }}>
        <div>
          <a href="/" className="hero__back-link">← zur Übersicht</a>
          <p className="eyebrow">Rechtliches</p>
          <h1>{content.title}</h1>
        </div>
      </header>
      {content.sections.map((section) => (
        <section key={section.heading} className="card">
          <h2>{section.heading}</h2>
          {section.paragraphs.map((paragraph, index) => (
            <p key={index}>{paragraph}</p>
          ))}
        </section>
      ))}
      <SiteFooter />
    </main>
  );
}
