import React, { useEffect, useState } from 'react';
import { endpoints } from './api/client';
import { AdminApp } from './AdminApp';
import { SiteFooter } from './components/SiteFooter';
import './styles.css';

// Sprint 28.05.2026 (Admin-Sektion) — Login-Gate + Logout fuer den
// Werkzeug-Bereich /admin. Drei Zustaende:
//
// 1. ``checking``: initial; /api/admin/me wird abgefragt, um zu sehen
//    ob ein Session-Cookie schon gueltig ist (Page-Reload-Pfad).
// 2. ``unauthenticated``: Login-Formular.
// 3. ``authenticated``: <AdminApp> mit Logout-Button im Hero.
//
// Backend-Konvention (admin_session.py): /me antwortet IMMER 200 mit
// ``{authenticated, auth_enabled}`` — kein Error-Toast bei "nicht
// eingeloggt". Bei ``auth_enabled=false`` (lokales dev) ist
// ``authenticated`` immer ``true``, der Login-Bildschirm wird nicht
// angezeigt.
//
// Sicherheits-Hinweis: das Cookie ist HttpOnly, kann nicht aus JS
// gelesen werden. Wir verlassen uns auf den Backend-/me-Check, nicht
// auf einen Frontend-State-Token. Der useLocalStorage-Helper bleibt
// fuer den Frontend-Convenience-State unbenutzt — die Wahrheit lebt
// im Cookie.

function AdminLogin({ onLoginSuccess }) {
  const [password, setPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  async function handleSubmit(event) {
    event.preventDefault();
    if (!password) return;
    setSubmitting(true);
    setError('');
    try {
      await endpoints.adminLogin(password);
      onLoginSuccess();
    } catch (err) {
      // Backend antwortet 401 bei falschem Passwort, 503 bei Server-
      // Misconfig. Wir unterscheiden im UI nicht — fuer den User
      // sieht beides aus wie "Login geht nicht".
      const message = err?.message || String(err);
      const friendly = message.includes('401') || message.toLowerCase().includes('invalid')
        ? 'Passwort falsch.'
        : 'Login zur Zeit nicht moeglich. Bitte spaeter erneut versuchen.';
      setError(friendly);
    } finally {
      setSubmitting(false);
      setPassword('');
    }
  }

  return (
    <main>
      <header className="hero" style={{ background: '#1f4d4d' }}>
        <div>
          <p className="eyebrow">Admin-Bereich</p>
          <h1>Creative Radar — Anmeldung</h1>
          <p>Bitte Passwort eingeben.</p>
        </div>
      </header>
      <section className="card" style={{ maxWidth: '420px', margin: '2rem auto' }}>
        <form onSubmit={handleSubmit}>
          <label style={{ display: 'block', marginBottom: '1rem' }}>
            Passwort
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoFocus
              autoComplete="current-password"
              style={{ marginTop: '0.5rem' }}
            />
          </label>
          {error && (
            <p className="error" style={{ marginBottom: '1rem' }}>{error}</p>
          )}
          <div className="section-actions">
            <button type="submit" className="primary" disabled={submitting || !password}>
              {submitting ? 'Prüfe …' : 'Anmelden'}
            </button>
            <a className="secondary" href="/" style={{ marginLeft: '0.75rem' }}>← zur Übersicht</a>
          </div>
        </form>
      </section>
      <SiteFooter />
    </main>
  );
}

export default function AdminPage() {
  // Drei moegliche Werte: 'checking' | 'unauthenticated' | 'authenticated'.
  const [authStatus, setAuthStatus] = useState('checking');

  async function checkSession() {
    try {
      const result = await endpoints.adminMe();
      setAuthStatus(result?.authenticated ? 'authenticated' : 'unauthenticated');
    } catch (err) {
      // /me sollte nie crashen (Backend-Konvention) — falls doch,
      // gehen wir defensiv auf "nicht eingeloggt".
      console.error('Failed to query /api/admin/me', err);
      setAuthStatus('unauthenticated');
    }
  }

  // Wartung 20.08.2026 — ``set-state-in-effect`` bewusst abgeschaltet.
  // Anders als bei den uebrigen Stellen liegt hier gar kein synchrones
  // setState vor: ``checkSession`` ist ``async`` und beginnt mit einem
  // ``await``, das erste ``setAuthStatus`` faellt also fruehestens im
  // naechsten Microtask. Die Regel sieht nur "Effect ruft Funktion, die
  // setState macht" und kann das nicht auseinanderhalten.
  //
  // Umbauen laesst sich das trotzdem nicht sinnvoll: der Startzustand
  // ist ``'checking'``, und ob eine Session besteht, weiss erst der
  // Server. Genau dafuer sind Effects da.
  //
  // Die Direktive steht INNERHALB des Effect-Bodys, direkt vor der
  // gemeldeten Zeile — vor dem ``useEffect`` deaktiviert sie nichts und
  // eslint meldet sie selbst als wirkungslos.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    checkSession();
  }, []);

  async function handleLogout() {
    try {
      await endpoints.adminLogout();
    } catch (_) {
      // Logout-Fehler ist unkritisch — Cookie ggf. noch da, aber der
      // naechste /me-Check klaert das.
    }
    setAuthStatus('unauthenticated');
  }

  if (authStatus === 'checking') {
    return (
      <main>
        <header className="hero" style={{ background: '#1f4d4d' }}>
          <div>
            <p className="eyebrow">Admin-Bereich</p>
            <h1>Creative Radar</h1>
          </div>
        </header>
        <p style={{ textAlign: 'center', padding: '2rem', color: '#6f675b' }}>
          Prüfe Anmeldung …
        </p>
      </main>
    );
  }

  if (authStatus === 'unauthenticated') {
    return <AdminLogin onLoginSuccess={() => setAuthStatus('authenticated')} />;
  }

  return <AdminApp onLogout={handleLogout} />;
}
