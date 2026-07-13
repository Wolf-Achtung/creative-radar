import { defineConfig } from 'vitest/config';

// Platin 5 (2026-07-13) — separates Config-File, da bislang kein
// vite.config.js existiert (das Projekt läuft mit Vite-Zero-Config über
// index.html). ``include`` schließt bewusst nur *.test.{js,jsx} ein —
// api/imageUrl.test.mjs bleibt auf ``node --test`` (node:test-API, nicht
// vitest-kompatibel), Doppel-Ausführung wird so vermieden.
export default defineConfig({
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.{js,jsx}'],
    globals: false,
  },
});
