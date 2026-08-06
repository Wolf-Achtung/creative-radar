// Erzeugt dist/_headers + dist/robots.txt fuer die STAGING-Netlify-Site
// (Staging-Briefing 2026-08-06, Abschnitt 4 Schritt 3).
//
// Läuft NICHT im normalen Build: die Staging-Site setzt in ihren
// Site-Settings den Build-Command auf
//
//     npm run build && npm run postbuild:staging
//
// Damit bleibt die geteilte netlify.toml (und damit Prod) unberuehrt —
// _headers im Publish-Ordner gewinnt fuer genau diese Site. Der Runtime-
// Guertel dazu ist das noindex-Meta aus EnvironmentBanner.jsx.
import { writeFileSync } from 'node:fs';

writeFileSync('dist/_headers', '/*\n  X-Robots-Tag: noindex, nofollow\n');
writeFileSync('dist/robots.txt', 'User-agent: *\nDisallow: /\n');
console.log('staging-headers: dist/_headers + dist/robots.txt geschrieben (noindex)');
