import React, { useEffect, useState } from 'react';
import { endpoints } from './api/client';
import { SiteFooter } from './components/SiteFooter';

// Sprint User-Login 2026-07 — Login-Gate fuer die GESAMTE Website
// (Startseite + alle Wochen-Briefs; Wolf-Festlegung 2026-07-20).
// Gleiche Drei-Zustands-Mechanik wie AdminPage.jsx (checking /
// unauthenticated / authenticated), aber mit dem zweistufigen
// E-Mail→Code-Flow des Schwesterprojekts statt Passwort.
//
// Ausserhalb des Gates bleiben: /impressum + /datenschutz (Rechtsseiten
// muessen ohne Login erreichbar sein) und /admin (eigene, staerkere
// Passwort-Session — das Backend laesst Admin-Sessions auch auf den
// Daten-Endpoints durch, kein Doppel-Login).
//
// Wahrheit lebt im HttpOnly-Cookie: beim Mount fragt das Gate
// /api/auth/me; bei auth_enabled=false (Rollout-Phase, lokales Dev)
// antwortet das Backend "eingeloggt" und das Gate ist unsichtbar.

function extractDetail(err) {
  // api/client.js wirft Error(body) — body ist meist der JSON-String
  // {"detail": "..."} des Backends. Defensive Extraktion.
  try {
    const parsed = JSON.parse(err?.message || '');
    if (typeof parsed?.detail === 'string') return parsed.detail;
  } catch (_) { /* kein JSON — Rohtext verwenden */ }
  return err?.message || '';
}

function friendlyRequestError(err) {
  const detail = extractDetail(err);
  if (detail.includes('nicht freigeschaltet')) {
    return 'Diese E-Mail-Adresse ist nicht freigeschaltet. Bitte beim Creative-Radar-Team melden.';
  }
  if (detail.includes('Too many requests')) {
    return 'Zu viele Versuche. Bitte in ein paar Minuten erneut versuchen.';
  }
  if (detail.includes('gültige E-Mail')) {
    return 'Bitte eine gültige E-Mail-Adresse eingeben.';
  }
  if (detail.includes('E-Mail-Versand')) {
    return 'E-Mail-Versand momentan nicht möglich. Bitte später erneut versuchen.';
  }
  return 'Code-Versand fehlgeschlagen. Bitte später erneut versuchen.';
}

function friendlyLoginError(err) {
  const detail = extractDetail(err);
  if (detail.includes('ungültig') || detail.includes('abgelaufen')) {
    return 'Code ist ungültig oder abgelaufen. Bitte neu anfordern.';
  }
  if (detail.includes('Too many requests')) {
    return 'Zu viele Versuche. Bitte in ein paar Minuten erneut versuchen.';
  }
  return 'Anmeldung fehlgeschlagen. Bitte erneut versuchen.';
}

const EMAIL_STORAGE_KEY = 'cr_login_email';

export function LoginPage({ onLoginSuccess }) {
  const [email, setEmail] = useState(() => {
    try { return localStorage.getItem(EMAIL_STORAGE_KEY) || ''; } catch (_) { return ''; }
  });
  const [code, setCode] = useState('');
  // 'email' (Schritt 1: Code anfordern) | 'code' (Schritt 2: einloesen)
  const [step, setStep] = useState('email');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  async function requestCode(event) {
    event.preventDefault();
    const normalized = email.trim().toLowerCase();
    if (!normalized || !normalized.includes('@')) {
      setError('Bitte eine gültige E-Mail-Adresse eingeben.');
      return;
    }
    setBusy(true); setError(''); setNotice('');
    try {
      await endpoints.authRequestCode(normalized);
      try { localStorage.setItem(EMAIL_STORAGE_KEY, normalized); } catch (_) { /* privacy mode */ }
      setStep('code');
      setNotice('Code gesendet — bitte Posteingang (ggf. Spam-Ordner) prüfen.');
    } catch (err) {
      setError(friendlyRequestError(err));
    } finally {
      setBusy(false);
    }
  }

  async function submitCode(event) {
    event.preventDefault();
    const trimmed = code.trim();
    if (!trimmed) return;
    setBusy(true); setError('');
    try {
      const result = await endpoints.authLogin(email.trim().toLowerCase(), trimmed);
      onLoginSuccess(result?.email || email.trim().toLowerCase());
    } catch (err) {
      setError(friendlyLoginError(err));
      setCode('');
    } finally {
      setBusy(false);
    }
  }

  return (
    <main>
      <header className="hero" style={{ background: '#1f4d4d' }}>
        <div>
          <p className="eyebrow">Creative Intelligence Workspace</p>
          <h1>Creative Radar — Anmeldung</h1>
          <p>Zugang für freigeschaltete Nutzer: E-Mail eingeben, Code aus der E-Mail bestätigen.</p>
        </div>
      </header>
      <section className="card login-card">
        {step === 'email' && (
          <form className="login-form" onSubmit={requestCode}>
            <label>
              E-Mail-Adresse
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoFocus
                autoComplete="email"
                placeholder="name@firma.de"
              />
            </label>
            {error && <p className="error">{error}</p>}
            <div className="login-actions">
              <button type="submit" className="primary" disabled={busy || !email.trim()}>
                {busy ? 'Sende Code …' : 'Anmeldecode anfordern'}
              </button>
            </div>
          </form>
        )}
        {step === 'code' && (
          <form className="login-form" onSubmit={submitCode}>
            <p style={{ marginTop: 0 }}>
              Code geschickt an <strong>{email.trim().toLowerCase()}</strong>.
            </p>
            {notice && <p className="info">{notice}</p>}
            <label>
              6-stelliger Code
              <input
                className="login-code-input"
                type="text"
                inputMode="numeric"
                autoComplete="one-time-code"
                pattern="[0-9]*"
                maxLength={6}
                value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))}
                autoFocus
                placeholder="123456"
              />
            </label>
            {error && <p className="error">{error}</p>}
            <div className="login-actions">
              <button type="submit" className="primary" disabled={busy || code.trim().length < 6}>
                {busy ? 'Prüfe …' : 'Anmelden'}
              </button>
              <button
                type="button"
                className="secondary"
                disabled={busy}
                onClick={() => { setStep('email'); setCode(''); setNotice(''); setError(''); }}
              >
                Code erneut anfordern
              </button>
            </div>
          </form>
        )}
        <p className="muted login-hint">
          Der Code ist 10 Minuten gültig. Kein Zugang? Bitte beim Creative-Radar-Team melden.
        </p>
      </section>
      <SiteFooter />
    </main>
  );
}

function UserBar({ email, onLogout }) {
  return (
    <div className="user-bar">
      <span className="user-bar__email" title={email}>Angemeldet als {email}</span>
      <button type="button" className="user-bar__logout" onClick={onLogout}>Abmelden</button>
    </div>
  );
}

export default function AuthGate({ children }) {
  // 'checking' | 'unauthenticated' | 'authenticated'
  const [status, setStatus] = useState('checking');
  const [email, setEmail] = useState(null);

  useEffect(() => {
    let cancelled = false;
    endpoints.authMe()
      .then((result) => {
        if (cancelled) return;
        setEmail(result?.email || null);
        setStatus(result?.authenticated ? 'authenticated' : 'unauthenticated');
      })
      .catch((err) => {
        if (cancelled) return;
        // /me sollte nie crashen (Backend-Konvention). Falls doch
        // (Netz weg, Deploy-Luecke), defensiv aufs Login-Formular.
        console.error('Failed to query /api/auth/me', err);
        setStatus('unauthenticated');
      });
    return () => { cancelled = true; };
  }, []);

  async function handleLogout() {
    try {
      await endpoints.authLogout();
    } catch (_) {
      // Unkritisch — der naechste /me-Check klaert den Zustand.
    }
    setEmail(null);
    setStatus('unauthenticated');
  }

  if (status === 'checking') {
    return (
      <main>
        <header className="hero" style={{ background: '#1f4d4d' }}>
          <div>
            <p className="eyebrow">Creative Intelligence Workspace</p>
            <h1>Creative Radar</h1>
          </div>
        </header>
        <p style={{ textAlign: 'center', padding: '2rem', color: '#6f675b' }}>
          Prüfe Anmeldung …
        </p>
      </main>
    );
  }

  if (status === 'unauthenticated') {
    return (
      <LoginPage
        onLoginSuccess={(loggedInEmail) => {
          setEmail(loggedInEmail);
          setStatus('authenticated');
        }}
      />
    );
  }

  return (
    <>
      {/* email ist null wenn auth_enabled=false (Rollout/lokal) —
          dann keine Leiste, die Seite sieht aus wie bisher. */}
      {email && <UserBar email={email} onLogout={handleLogout} />}
      {children}
    </>
  );
}
