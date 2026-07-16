import js from '@eslint/js';
import react from 'eslint-plugin-react';
import reactHooks from 'eslint-plugin-react-hooks';
import globals from 'globals';

// Sicherheits-Audit 2026-07-01: kein Lint-Setup vorhanden. Opt-in Tooling,
// bewusst nicht in CI verdrahtet — run `npm run lint` lokal.
export default [
  // UX-Audit-Beifang (2026-07-14): das Build-Artefakt dist/ wurde
  // mitgelintet (~235 Fehler aus dem minifizierten Bundle) und liess die
  // Fehlerzahl mit jedem Build schwanken. Ignorieren macht `npm run lint`
  // zu einer ehrlichen Quelltext-Metrik.
  { ignores: ['dist/**'] },
  js.configs.recommended,
  {
    files: ['src/**/*.{js,jsx}'],
    plugins: {
      react,
      'react-hooks': reactHooks,
    },
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      parserOptions: {
        ecmaFeatures: { jsx: true },
      },
      globals: globals.browser,
    },
    settings: {
      react: { version: 'detect' },
    },
    rules: {
      ...react.configs.recommended.rules,
      ...reactHooks.configs.recommended.rules,
      'react/react-in-jsx-scope': 'off',
      'react/prop-types': 'off',
      // Established convention in this codebase: `catch (_) {}` /
      // `.catch((_) => ...)` for deliberately-ignored errors.
      'no-unused-vars': ['error', { varsIgnorePattern: '^_', caughtErrorsIgnorePattern: '^_', argsIgnorePattern: '^_' }],
    },
  },
];
