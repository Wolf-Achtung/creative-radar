import React, { useEffect, useState } from 'react';
import { endpoints } from '../api/client';
import { formatDateTime } from '../format';
import { Section } from '../components/Section';

// Sprint User-Login 2026-07 — User-Verwaltung im Admin-Bereich.
// Wolf pflegt hier die ~15 Login-E-Mails (hinzufuegen, sperren,
// entfernen), ohne je in die Datenbank zu muessen. Die Tabelle mischt
// Stammdaten (app_user) mit Aktivitaets-Zahlen aus /api/admin/usage
// (30-Tage-Fenster) — "wer ist angelegt" und "wer nutzt es" in einem
// Blick; die tiefere Auswertung (Aktionen, Top-Briefs) lebt im
// Monitoring-Tab.
export function UsersPanel() {
  const [users, setUsers] = useState(null);
  const [usageByEmail, setUsageByEmail] = useState({});
  const [form, setForm] = useState({ email: '', display_name: '' });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');

  async function load() {
    setError('');
    try {
      const [userList, usage] = await Promise.all([
        endpoints.adminUsers(),
        endpoints.adminUsage(30).catch(() => null),
      ]);
      setUsers(Array.isArray(userList) ? userList : []);
      const byEmail = {};
      for (const row of usage?.users || []) byEmail[row.email] = row;
      setUsageByEmail(byEmail);
    } catch (err) {
      setError(err?.message || String(err));
      setUsers([]);
    }
  }

  // Wartung 20.08.2026 — ``set-state-in-effect`` bewusst abgeschaltet.
  // ``load()`` setzt synchron ``setError('')`` zurueck, bevor der Abruf
  // startet; eine alte Fehlermeldung soll nicht ueber dem neuen Ladevorgang
  // stehenbleiben. Kein abgeleiteter Zustand — das ist der Fall, den die
  // Regel nicht von einem Datenabruf beim Mounten unterscheiden kann.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { load(); }, []);

  async function run(fn) {
    setBusy(true); setError(''); setMessage('');
    try {
      await fn();
      await load();
    } catch (err) {
      const raw = err?.message || String(err);
      setError(raw.includes('bereits angelegt') ? 'Diese E-Mail-Adresse ist bereits angelegt.' : raw);
    } finally {
      setBusy(false);
    }
  }

  async function addUser(event) {
    event.preventDefault();
    const email = form.email.trim().toLowerCase();
    if (!email) return;
    await run(async () => {
      await endpoints.adminCreateUser({ email, display_name: form.display_name.trim() || null });
      setForm({ email: '', display_name: '' });
      setMessage(`${email} freigeschaltet.`);
    });
  }

  async function toggleUsageAccess(user) {
    await run(async () => {
      await endpoints.adminPatchUser(user.id, { can_view_usage: !user.can_view_usage });
      setMessage(user.can_view_usage
        ? `${user.email} sieht die Nutzungs-Auswertung nicht mehr.`
        : `${user.email} kann jetzt die Nutzungs-Auswertung unter /nutzung einsehen.`);
    });
  }

  async function toggleActive(user) {
    await run(async () => {
      await endpoints.adminPatchUser(user.id, { active: !user.active });
      setMessage(user.active
        ? `${user.email} gesperrt — bestehende Anmeldung wirkt ab sofort nicht mehr.`
        : `${user.email} wieder freigeschaltet.`);
    });
  }

  async function removeUser(user) {
    // Confirm im Browser reicht hier — destruktive Aktion, aber ohne
    // Datenverlust (Nutzungs-Events bleiben erhalten, User ist per
    // "Hinzufügen" in Sekunden wieder da).
    if (!window.confirm(`${user.email} wirklich entfernen? Die Nutzungs-Historie bleibt erhalten.`)) return;
    await run(async () => {
      await endpoints.adminDeleteUser(user.id);
      setMessage(`${user.email} entfernt.`);
    });
  }

  return (
    <>
      <Section title="Login-Nutzer" kicker="E-Mail-Allowlist für den Website-Zugang">
        <p className="muted" style={{ marginTop: 0 }}>
          Nur hier eingetragene E-Mail-Adressen können einen Anmeldecode anfordern.
          Sperren wirkt sofort — auch für bereits angemeldete Geräte.
        </p>
        {error && <div className="error">{error}</div>}
        {message && <div className="success">{message}</div>}
        {users === null && <p className="muted">Lade Nutzer …</p>}
        {users !== null && users.length === 0 && <p className="muted">Noch keine Nutzer angelegt.</p>}
        {users !== null && users.length > 0 && (
          <div style={{ overflowX: 'auto' }}>
            <table>
              <thead>
                <tr>
                  <th>E-Mail</th>
                  <th>Name</th>
                  <th>Status</th>
                  <th>Monitoring</th>
                  <th>Letzter Login</th>
                  <th>Zuletzt aktiv</th>
                  <th>Aktionen (30 T.)</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {users.map((user) => {
                  const usage = usageByEmail[user.email] || {};
                  return (
                    <tr key={user.id}>
                      <td>{user.email}</td>
                      <td>{user.display_name || '—'}</td>
                      <td>{user.active ? 'aktiv' : 'gesperrt'}</td>
                      <td>
                        {/* Monitoring-Freischaltung pro Person (2026-07-20):
                            User mit Haken sehen nach dem normalen Login
                            zusaetzlich /nutzung (nur die Auswertung, keine
                            weiteren Admin-Funktionen). */}
                        <label style={{ whiteSpace: 'nowrap', cursor: 'pointer' }}>
                          <input
                            type="checkbox"
                            checked={Boolean(user.can_view_usage)}
                            disabled={busy}
                            onChange={() => toggleUsageAccess(user)}
                          />
                          {' '}Nutzung
                        </label>
                      </td>
                      <td>{user.last_login_at ? formatDateTime(user.last_login_at) : '—'}</td>
                      <td>{usage.last_active ? formatDateTime(usage.last_active) : '—'}</td>
                      <td>{usage.events ?? 0}</td>
                      <td style={{ whiteSpace: 'nowrap' }}>
                        <button type="button" className="secondary" disabled={busy} onClick={() => toggleActive(user)}>
                          {user.active ? 'Sperren' : 'Freischalten'}
                        </button>
                        {' '}
                        <button type="button" className="secondary" disabled={busy} onClick={() => removeUser(user)}>
                          Entfernen
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      <Section title="Nutzer hinzufügen" kicker="Freischalten in einem Schritt">
        <form onSubmit={addUser} className="form-grid">
          <label>
            E-Mail-Adresse
            <input
              type="email"
              value={form.email}
              onChange={(e) => setForm({ ...form, email: e.target.value })}
              placeholder="name@firma.de"
              required
            />
          </label>
          <label>
            Name (optional)
            <input
              type="text"
              value={form.display_name}
              onChange={(e) => setForm({ ...form, display_name: e.target.value })}
              placeholder="Vor- und Nachname"
            />
          </label>
          <div className="section-actions">
            <button type="submit" className="primary" disabled={busy || !form.email.trim()}>
              {busy ? 'Speichere …' : 'Freischalten'}
            </button>
          </div>
        </form>
        <p className="muted" style={{ marginBottom: 0, fontSize: '0.85em' }}>
          Der neue Nutzer kann sich sofort mit dieser E-Mail-Adresse einen Anmeldecode schicken lassen.
        </p>
      </Section>
    </>
  );
}
