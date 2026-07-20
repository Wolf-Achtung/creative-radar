import React, { useEffect, useState } from 'react';
import { endpoints } from '../api/client';
import { formatDateTime, formatNumber } from '../format';

// Nutzungs-Auswertung als eigenstaendige, selbst-ladende Komponente
// (Wolf-Festlegung 2026-07-20). Zwei Einbau-Orte, EIN Code-Pfad:
// 1. Admin-Bereich, Monitoring-Tab (Voll-Admin-Session)
// 2. /nutzung — Seite fuer einzeln freigeschaltete Login-User
//    (app_user.can_view_usage; Backend-Check require_usage_access)
// Features: Uebersichtstabelle mit Drill-down pro Nutzer (aufklappbare
// Event-Liste), meistgeoeffnete Briefs, Aktions-Summen, HTML-/CSV-Export.

export const USAGE_ACTION_LABELS = {
  login: 'Anmeldung',
  landing_view: 'Startseite geöffnet',
  brief_view: 'Studio-Brief geöffnet',
  title_view: 'Film-Detailseite geöffnet',
  forecast_view: 'ER-Prognose abgerufen',
  report_download: 'Report heruntergeladen',
};

function friendlyError(err) {
  const raw = err?.message || String(err);
  if (raw.includes('Berechtigung') || raw.includes('401')) {
    return 'Kein Zugriff auf die Nutzungs-Auswertung. Falls du sie brauchst, bitte beim Creative-Radar-Team freischalten lassen.';
  }
  return raw;
}

function UserDetailRow({ email, days }) {
  const [state, setState] = useState({ status: 'loading', events: [] });

  useEffect(() => {
    let cancelled = false;
    endpoints.adminUsageUserEvents(email, days)
      .then((data) => {
        if (cancelled) return;
        setState({ status: 'done', events: data?.events || [] });
      })
      .catch(() => {
        if (cancelled) return;
        setState({ status: 'error', events: [] });
      });
    return () => { cancelled = true; };
  }, [email, days]);

  if (state.status === 'loading') {
    return <p className="muted small" style={{ margin: '0.5rem 0' }}>Lade Ereignisse …</p>;
  }
  if (state.status === 'error') {
    return <p className="muted small" style={{ margin: '0.5rem 0' }}>Ereignisse momentan nicht verfügbar.</p>;
  }
  if (state.events.length === 0) {
    return <p className="muted small" style={{ margin: '0.5rem 0' }}>Keine Ereignisse im Zeitraum.</p>;
  }
  return (
    <table className="ops-cost-table usage-detail-table">
      <thead>
        <tr><th>Zeitpunkt</th><th>Aktion</th><th>Studio</th></tr>
      </thead>
      <tbody>
        {state.events.map((event, index) => (
          <tr key={index}>
            <td>{event.created_at}</td>
            <td>{event.action_label || event.action}</td>
            <td>{event.pair || '—'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function UsageSection({ reloadKey = 0, days = 30 }) {
  const [usage, setUsage] = useState(null);
  const [status, setStatus] = useState('loading'); // 'loading' | 'done' | 'error'
  const [error, setError] = useState('');
  const [expandedEmail, setExpandedEmail] = useState(null);
  const [exportBusy, setExportBusy] = useState(false);
  const [exportError, setExportError] = useState('');

  useEffect(() => {
    let cancelled = false;
    setStatus('loading');
    endpoints.adminUsage(days)
      .then((data) => {
        if (cancelled) return;
        setUsage(data);
        setStatus('done');
      })
      .catch((err) => {
        if (cancelled) return;
        setError(friendlyError(err));
        setStatus('error');
      });
    return () => { cancelled = true; };
  }, [reloadKey, days]);

  async function runExport(fn) {
    setExportBusy(true);
    setExportError('');
    try {
      await fn();
    } catch (err) {
      setExportError(friendlyError(err));
    } finally {
      setExportBusy(false);
    }
  }

  if (status === 'loading') return <p className="muted small">Lädt …</p>;
  if (status === 'error') return <p className="error">{error}</p>;
  if (!usage) return <p className="muted small">Nutzungsdaten momentan nicht verfügbar.</p>;

  return (
    <>
      <p className="muted small" style={{ marginTop: 0 }}>
        {formatNumber(usage.events_total)} Ereignisse von eingeloggten Nutzern in den letzten {usage.days} Tagen.
        Gezählt wird nur Nutzung hinter dem Login — Admin-Zugriffe fließen nicht ein.
        Klick auf eine Zeile zeigt die Ereignisse des Nutzers.
      </p>
      <div className="section-actions">
        <button
          type="button"
          className="secondary"
          disabled={exportBusy}
          onClick={() => runExport(() => endpoints.adminUsageExportHtml(days))}
        >
          Bericht herunterladen (HTML)
        </button>
        {' '}
        <button
          type="button"
          className="secondary"
          disabled={exportBusy}
          onClick={() => runExport(() => endpoints.adminUsageExportCsv(days))}
        >
          Rohdaten (CSV)
        </button>
      </div>
      {exportError && <p className="error">{exportError}</p>}
      {usage.users && usage.users.length > 0 && (
        <table className="ops-cost-table">
          <thead>
            <tr><th>Nutzer</th><th>Status</th><th>Zuletzt aktiv</th><th>Logins</th><th>Brief-Ansichten</th><th>Ereignisse</th></tr>
          </thead>
          <tbody>
            {usage.users.map((user) => (
              <React.Fragment key={user.email}>
                <tr
                  className="usage-user-row"
                  onClick={() => setExpandedEmail(expandedEmail === user.email ? null : user.email)}
                >
                  <td>
                    <span aria-hidden="true">{expandedEmail === user.email ? '▾ ' : '▸ '}</span>
                    {user.display_name ? `${user.display_name} (${user.email})` : user.email}
                  </td>
                  <td>{user.deleted ? 'entfernt' : user.active ? 'aktiv' : 'gesperrt'}</td>
                  <td>{user.last_active ? formatDateTime(user.last_active) : (user.last_login_at ? `Login: ${formatDateTime(user.last_login_at)}` : '—')}</td>
                  <td>{formatNumber(user.logins || 0)}</td>
                  <td>{formatNumber(user.actions?.brief_view || 0)}</td>
                  <td>{formatNumber(user.events || 0)}</td>
                </tr>
                {expandedEmail === user.email && (
                  <tr className="usage-detail-row">
                    <td colSpan={6}>
                      <UserDetailRow email={user.email} days={days} />
                    </td>
                  </tr>
                )}
              </React.Fragment>
            ))}
          </tbody>
        </table>
      )}
      {usage.top_briefs && usage.top_briefs.length > 0 && (
        <>
          <h4 style={{ marginBottom: '0.5rem' }}>Meistgeöffnete Studio-Briefs</h4>
          <table className="ops-cost-table">
            <thead>
              <tr><th>Studio (Pair)</th><th>Ansichten</th></tr>
            </thead>
            <tbody>
              {usage.top_briefs.map((brief) => (
                <tr key={brief.pair}>
                  <td>{brief.pair}</td>
                  <td>{formatNumber(brief.count)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      {usage.actions && usage.actions.length > 0 && (
        <>
          <h4 style={{ marginBottom: '0.5rem' }}>Aktionen gesamt</h4>
          <table className="ops-cost-table">
            <thead>
              <tr><th>Aktion</th><th>Anzahl</th></tr>
            </thead>
            <tbody>
              {usage.actions.map((action) => (
                <tr key={action.action}>
                  <td>{USAGE_ACTION_LABELS[action.action] || action.action}</td>
                  <td>{formatNumber(action.count)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </>
  );
}
