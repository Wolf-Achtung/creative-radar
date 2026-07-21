import React, { useState } from 'react';
import { SiteFooter } from './components/SiteFooter';

// UX-Audit Befund 1 (2026-07-14) — Impressum & Datenschutz als eigene
// Routen. 2026-07-20: Anbieterangaben von Wolf eingepflegt (Wolf Hohl,
// Berlin). Die Texte sind sorgfältige Entwürfe, keine Rechtsberatung.
//
// Spam-/Crawler-Schutz (Wolf-Vorgabe): Telefonnummer und E-Mail-Adresse
// stehen NIRGENDS im Klartext — weder im HTML noch als Literal im
// JS-Bundle. Sie liegen als Zeichencode-Arrays vor und werden erst beim
// Klick auf "anzeigen" dekodiert und gerendert. Harvester, die HTML oder
// Bundles nach mailto:/Telefonnummern-Mustern durchsuchen, finden nichts;
// echte Besucher haben einen Klick Mehraufwand. (Kein perfekter Schutz —
// ein gezielter Headless-Browser kommt dran — aber das Massen-Scraping
// läuft ins Leere.)
const PHONE_CODES = [48, 49, 55, 56, 45, 51, 54, 54, 55, 49, 50, 54];
const EMAIL_CODES = [119, 111, 108, 102, 64, 116, 114, 97, 105, 108, 101, 114, 104, 97, 117, 115, 46, 100, 101];

function decode(codes) {
  return String.fromCharCode(...codes);
}

function ProtectedContact() {
  const [revealed, setRevealed] = useState(false);
  if (!revealed) {
    return (
      <div>
        <button type="button" className="secondary" onClick={() => setRevealed(true)}>
          Kontaktdaten anzeigen
        </button>
        <p className="muted" style={{ marginTop: '0.75rem', fontSize: '0.85em' }}>
          Zum Schutz vor Spam werden E-Mail-Adresse und Telefonnummer erst nach Klick angezeigt.
        </p>
      </div>
    );
  }
  const email = decode(EMAIL_CODES);
  const phone = decode(PHONE_CODES);
  return (
    <div>
      <p>E-Mail: <a href={`mailto:${email}`}>{email}</a></p>
      <p>Telefon: <a href={`tel:${phone.replace(/-/g, '')}`}>{phone}</a></p>
    </div>
  );
}

const LEGAL_CONTENT = {
  impressum: {
    title: 'Impressum',
    sections: [
      {
        heading: 'Angaben gemäß § 5 DDG',
        paragraphs: ['Wolf Hohl', 'Greifswalder Str. 224a', '10405 Berlin'],
      },
      {
        heading: 'Kontakt',
        contact: true,
        paragraphs: [],
      },
      {
        heading: 'Verantwortlich für den Inhalt nach § 18 Abs. 2 MStV',
        paragraphs: ['Wolf Hohl, Greifswalder Str. 224a, 10405 Berlin'],
      },
      {
        heading: 'Haftungsausschluss',
        paragraphs: [
          'Diese Website dient ausschließlich der Information. Trotz sorgfältiger Prüfung übernehme ich keine Haftung für Inhalte externer Links — für sie sind ausschließlich deren Betreiber verantwortlich.',
        ],
      },
      {
        heading: 'Urheberrecht',
        paragraphs: [
          'Alle Inhalte dieser Website unterliegen dem deutschen Urheberrecht. Dargestellte Vorschaubilder und verlinkte Beiträge stammen aus öffentlichen Social-Media-Auftritten (Instagram, TikTok, YouTube); die Rechte an diesen Inhalten liegen bei den jeweiligen Urhebern bzw. Plattformen.',
        ],
      },
      {
        heading: 'Hinweis zu KI-gestützten Analysen',
        paragraphs: [
          'Die Wochenanalysen auf dieser Website werden KI-gestützt aus öffentlich zugänglichen Social-Media-Daten erstellt. Trotz sorgfältiger Aufbereitung kann es zu Ungenauigkeiten kommen — maßgeblich sind stets die verlinkten Original-Beiträge. Die Analysen dienen der Information und ersetzen keine Rechts- oder Anlageberatung.',
        ],
      },
    ],
  },
  datenschutz: {
    title: 'Datenschutzerklärung',
    sections: [
      {
        heading: 'Verantwortlicher',
        paragraphs: [
          'Verantwortlich für die Datenverarbeitung auf dieser Website ist: Wolf Hohl, Greifswalder Str. 224a, 10405 Berlin. Die Kontaktdaten finden Sie im Impressum (dort nach Klick auf „Kontaktdaten anzeigen").',
          'Der Schutz Ihrer persönlichen Daten ist mir ein besonderes Anliegen.',
        ],
      },
      {
        heading: 'Zugang und Anmeldung (Login per E-Mail-Code)',
        paragraphs: [
          'Creative Radar ist ein geschlossener Bereich für einen festen Nutzerkreis. Für die Anmeldung verarbeiten wir Ihre E-Mail-Adresse, die zuvor vom Betreiber für den Zugang freigeschaltet wurde. Beim Login senden wir Ihnen einen einmalig verwendbaren, 10 Minuten gültigen Zahlencode per E-Mail; der Code wird in unserer Datenbank ausschließlich in verschlüsselter Form (Hash) gespeichert.',
          'Rechtsgrundlage ist Art. 6 Abs. 1 lit. b DSGVO (Bereitstellung des vereinbarten Zugangs). Für den Versand der Code-E-Mails nutzen wir einen spezialisierten E-Mail-Versanddienstleister als Auftragsverarbeiter; die Verarbeitung erfolgt auf Servern in der EU.',
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
          'Rechtsgrundlage ist Art. 6 Abs. 1 lit. f DSGVO (berechtigtes Interesse an der bedarfsgerechten Weiterentwicklung des geschlossenen Angebots). Sie können dieser Verarbeitung jederzeit mit Wirkung für die Zukunft widersprechen — wenden Sie sich dazu an die im Impressum genannte Kontaktadresse.',
        ],
      },
      {
        heading: 'Kontaktaufnahme',
        paragraphs: [
          'Wenn Sie per E-Mail Kontakt aufnehmen, werden Ihre Angaben zur Bearbeitung der Anfrage und für Anschlussfragen sechs Monate gespeichert und danach gelöscht, sofern keine gesetzlichen Aufbewahrungspflichten bestehen.',
        ],
      },
      {
        // Bewusst KATEGORIEN statt Firmennamen (Wolf 21.07.): Art. 13
        // Abs. 1 lit. e DSGVO erlaubt "Empfaenger ODER Kategorien von
        // Empfaengern" — die konkreten Dienstleister werden auf eine
        // Auskunftsanfrage nach Art. 15 hin benannt (EuGH C-154/21),
        // muessen aber nicht oeffentlich ausgeschildert sein.
        heading: 'Hosting und Infrastruktur',
        paragraphs: [
          'Die Website wird über einen spezialisierten Web-Hosting-Dienstleister ausgeliefert; Anwendungsserver und Datenbank betreiben wir bei einem Cloud-Infrastruktur-Anbieter. Beim Aufruf der Website verarbeiten diese Dienstleister als Auftragsverarbeiter technisch bedingt Verbindungsdaten (u. a. IP-Adresse, Zeitpunkt, abgerufene Ressource) in Server-Logs. Mit allen Dienstleistern bestehen Auftragsverarbeitungsverträge; soweit eine Verarbeitung außerhalb der EU stattfindet, erfolgt sie auf Grundlage der EU-Standardvertragsklauseln bzw. des EU-US Data Privacy Framework. Auskunft über die konkret eingesetzten Dienstleister erteilen wir auf Anfrage über die im Impressum genannte Kontaktadresse.',
        ],
      },
      {
        heading: 'Eingebettete Inhalte von Social-Media-Plattformen',
        paragraphs: [
          'Die dargestellten Analysen zeigen Vorschaubilder öffentlicher Social-Media-Beiträge (Instagram, TikTok, YouTube). Diese werden über unsere eigene Infrastruktur zwischengeladen; erst wenn Sie einen Beitrag aktiv anklicken, verlassen Sie unsere Website und es gelten die Datenschutzbestimmungen der jeweiligen Plattform.',
        ],
      },
      {
        heading: 'Speicherdauer und Ihre Rechte laut DSGVO',
        paragraphs: [
          'Login-Codes sind 10 Minuten gültig und werden bei jeder neuen Anforderung ersetzt. Ihr Nutzerkonto (E-Mail-Adresse) und das Nutzungsprotokoll speichern wir, solange Ihr Zugang besteht bzw. solange die Auswertung für den Betrieb erforderlich ist.',
          'Sie haben das Recht auf Auskunft (Art. 15 DSGVO), Berichtigung (Art. 16), Löschung (Art. 17), Einschränkung der Verarbeitung (Art. 18), Datenübertragbarkeit (Art. 20) und Widerspruch (Art. 21) sowie das Recht, erteilte Einwilligungen zu widerrufen und sich bei einer Datenschutz-Aufsichtsbehörde zu beschweren. Wenden Sie sich für alle Anliegen an die im Impressum genannte Kontaktadresse.',
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
          {section.contact && <ProtectedContact />}
        </section>
      ))}
      <SiteFooter />
    </main>
  );
}
