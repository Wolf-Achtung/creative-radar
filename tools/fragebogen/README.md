# Fragebogen — Trailer & Social-Spots (Fassung 2.2)

Eine einzelne, in sich geschlossene HTML-Seite. Kein Build, kein Framework, keine
Abhängigkeiten, keine externen Ressourcen — Schriften kommen vom System des
Betrachters, das Favicon steckt als Data-URI im Dokument.

22 Kernfragen, überwiegend Freitext, etwa 15 bis 20 Minuten.

## Aufbau

Der Fragebogen verankert sich an **einer einzelnen Fassung** eines konkreten
Projekts: A2 lässt alle geschnittenen Fassungen aufzählen, A3 wählt daraus die
eine aus, um die es in den Teilen 1 bis 3 geht. Diese Wahl wird in jedem der
drei Teile eingeblendet, damit beim Beantworten klar bleibt, wovon die Rede
ist. Erst Teil 4 verallgemeinert bewusst.

Teil 5 legt fünf Auswertungsbefunde zur Prüfung vor — Widerspruch ist dort das
eigentliche Ziel. Wo die Datengrundlage die Aussage nicht voll trägt, steht das
am Befund dabei. Teil 6 fragt nach der Reihenfolge dessen, was als Nächstes
gebaut wird; die Liste enthält ausschließlich Dinge, die es noch nicht gibt.

## Dateien

| Datei | Zweck |
|---|---|
| `index.html` | der komplette Fragebogen — CSS und Skript inline |
| `netlify.toml` | Veröffentlichungs-Ordner und Sicherheits-Header |
| `robots.txt` | hält Suchmaschinen fern |

## Auf Netlify veröffentlichen

**Weg A — GitHub-Repository verbinden (empfohlen, weil Änderungen dann
automatisch live gehen)**

1. Diese drei Dateien in ein neues GitHub-Repository legen, z. B. `fragebogen-trailer`.
2. Auf app.netlify.com: *Add new site → Import an existing project → GitHub*, das
   Repository auswählen.
3. Build-Einstellungen leer lassen — `netlify.toml` setzt bereits alles Nötige
   (`publish = "."`, kein Build-Befehl).
4. *Deploy*. Nach etwa einer halben Minute steht die Adresse bereit.

**Weg B — ohne Git, per Drag & Drop**

Auf app.netlify.com/drop den Ordner in das Browserfenster ziehen. Sofort online,
aber jede Änderung erfordert erneutes Ziehen.

### Adresse anpassen

Unter *Site configuration → Change site name* lässt sich aus dem zufälligen
Netlify-Namen etwas Vorzeigbares machen, etwa
`fragebogen-trailer.netlify.app`. Eine eigene Domain geht dort ebenfalls.

## Wie die Antworten zurückkommen

Die Seite verschickt nichts. Am Ende erzeugt sie einen Textblock, der sich
kopieren oder als `.txt` speichern lässt — der Rückweg läuft über E-Mail.

Das ist Absicht: keine Formular-Backends, keine Einwilligungen, keine
Datenschutz-Frage im Erstkontakt. Wer später doch einen automatischen Rücklauf
will, kann Netlify Forms nachrüsten — dann braucht die Seite ein `<form>` mit
`data-netlify="true"`, und es entsteht eine Datenverarbeitung, die im
Fragebogen erwähnt gehört.

## Zwischenspeichern

Eingaben landen laufend im `localStorage` des Browsers unter dem Schlüssel
`fragebogen-trailer-2-2` und werden beim erneuten Öffnen wiederhergestellt. Das
ist an Gerät und Browser gebunden — wer den Rechner wechselt, fängt dort neu an.
Ein Link am Seitenende löscht alles wieder.

**Wichtig beim Ändern von Fragen:** Der Schlüssel enthält die Fassungsnummer.
Wer Feldnamen ändert oder Fragen austauscht, sollte ihn hochzählen
(`…-2-3`), sonst werden alte Entwürfe in ein neues Formular geladen.

## Lokal ansehen

Ein Doppelklick auf `index.html` genügt. Wer die Header aus `netlify.toml`
mitprüfen will, nimmt `netlify dev`.
