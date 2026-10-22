import { BrowserRouter, Link, Navigate, Route, Routes, useLocation } from 'react-router-dom';

import { BackendStatus } from '@/components/BackendStatus';
import { ThemeToggle } from '@/components/ThemeToggle';
import { SessionProvider } from '@/auth/SessionProvider';
import { PERMISSIONS, canRender } from '@/auth/permissions';
import { useIsReadOnly, useSession } from '@/auth/useSession';
import { Login } from '@/pages/Login';
import { ApprovalPolicies } from '@/pages/approvals/ApprovalPolicies';
import { ApprovalQueue } from '@/pages/approvals/ApprovalQueue';
import { AuditLog } from '@/pages/audit/AuditLog';
import { OperationDetailPage } from '@/pages/operations/OperationDetail';
import { OperationHistory, ServerOperations } from '@/pages/operations/OperationHistory';
import { AlertDetail } from '@/pages/alerts/AlertDetail';
import { AlertList } from '@/pages/alerts/AlertList';
import { Dashboard } from '@/pages/dashboard/Dashboard';
import { ApplicationDetail } from '@/pages/applications/ApplicationDetail';
import { ApplicationList } from '@/pages/applications/ApplicationList';
import { ServerDetail } from '@/pages/servers/ServerDetail';
import { ServerEditForm, ServerForm } from '@/pages/servers/ServerForm';
import { LogSearch } from '@/pages/logs/LogSearch';
import { Compare } from '@/pages/metrics/Compare';
import { ChannelList } from '@/pages/notifications/ChannelList';
import { ServerList } from '@/pages/servers/ServerList';
import { ServerMetrics } from '@/pages/servers/ServerMetrics';
import { ServerServices } from '@/pages/services/ServerServices';
import { ServiceDetailPage } from '@/pages/services/ServiceDetailPage';
import { ServiceList } from '@/pages/services/ServiceList';

/**
 * Composition root, session gate and routing shell.
 *
 * Three session states, rendered as three different things, because
 * conflating them is the bug this file exists to avoid:
 *
 * - `loading` — we have not asked yet. Not "signed out".
 * - `anonymous` — the server said `401`. Show the login page.
 * - `unreachable` — we could not ask. Show the login page *saying so*, rather
 *   than implying the user was logged out by a backend that is simply down.
 *
 * Routing arrived in M1.5 with the five inventory pages, which need
 * list→detail drill-through and deep links. The gate stays *outside* the
 * router: an unauthenticated visitor gets the login page whatever the URL
 * says, and the path they asked for is handed to `/auth/login` as `next` so
 * they land back on it. That is a convenience — the backend validates `next`
 * again and refuses an absolute URL (§12).
 */

function Navigation(): React.JSX.Element {
  const { pathname } = useLocation();
  const { state } = useSession();
  const links = [
    { to: '/dashboard', label: 'Dashboard' },
    { to: '/servers', label: 'Servers' },
    { to: '/services', label: 'Services' },
    { to: '/compare', label: 'Compare' },
    // Advisory, like every nav decision: the backend answers 403 on its own account.
    ...(canRender(state, PERMISSIONS.logsReadApplication) ? [{ to: '/logs', label: 'Logs' }] : []),
    ...(canRender(state, PERMISSIONS.alertRead) ? [{ to: '/alerts', label: 'Alerts' }] : []),
    { to: '/applications', label: 'Applications' },
    ...(canRender(state, PERMISSIONS.operationRead)
      ? [{ to: '/operations', label: 'Operations' }]
      : []),
    ...(canRender(state, PERMISSIONS.operationApprove)
      ? [{ to: '/approvals', label: 'Approvals' }]
      : []),
    ...(canRender(state, PERMISSIONS.auditRead) ? [{ to: '/audit', label: 'Audit log' }] : []),
    ...(canRender(state, PERMISSIONS.adminIntegrations)
      ? [{ to: '/notifications/channels', label: 'Notifications' }]
      : []),
    ...(canRender(state, PERMISSIONS.adminPolicies)
      ? [{ to: '/admin/approval-policies', label: 'Approval policies' }]
      : []),
  ];
  return (
    <nav
      aria-label="Main"
      className="mb-6 flex gap-4 border-b pb-2"
      style={{ borderColor: 'var(--c-border)' }}
    >
      {links.map((link) => {
        const active = pathname.startsWith(link.to);
        return (
          <Link
            key={link.to}
            to={link.to}
            aria-current={active ? 'page' : undefined}
            className="text-sm"
            style={{
              color: active ? 'var(--c-text)' : 'var(--c-text-muted)',
              fontWeight: active ? 600 : 400,
            }}
          >
            {link.label}
          </Link>
        );
      })}
    </nav>
  );
}

function Shell(): React.JSX.Element {
  const { state, signOut } = useSession();
  // Advisory, like every permission the UI reads: the backend answers 423 on a
  // state-changing request whatever this says (docs/SECURITY.md §27).
  const readOnly = useIsReadOnly();

  if (state.kind === 'loading') {
    return (
      <main className="mx-auto max-w-2xl px-4 py-16">
        <p role="status" style={{ color: 'var(--c-text-muted)' }}>
          Checking your session…
        </p>
      </main>
    );
  }

  if (state.kind === 'anonymous') return <Login />;
  if (state.kind === 'unreachable') return <Login reason="unreachable" />;

  const { session } = state;

  return (
    <main className="mx-auto max-w-5xl px-4 py-10">
      {session.demo_mode && (
        // AD-15: demonstration mode is never silent. A persistent banner, not
        // a one-time toast, because the point is that nobody mistakes
        // synthetic data for a live integration.
        <div
          role="status"
          className="mb-6 rounded border px-4 py-2 text-sm"
          style={{ borderColor: 'var(--c-warning)', color: 'var(--c-warning)' }}
        >
          <strong>Demonstration mode.</strong> Data shown may be synthetic and integrations are not
          operational.
        </div>
      )}

      {readOnly && (
        // The first containment step in an incident involving this portal, so
        // it says so plainly rather than letting writes fail unexplained.
        <div
          role="status"
          className="mb-6 rounded border px-4 py-2 text-sm"
          style={{ borderColor: 'var(--c-warning)', color: 'var(--c-warning)' }}
        >
          <strong>Read-only mode.</strong> State-changing actions are disabled. Monitoring and
          history remain available.
        </div>
      )}

      <header className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">DevOps Control Plane</h1>
          <p className="text-sm" style={{ color: 'var(--c-text-muted)' }}>
            Milestone M4.6 — inventory, a PostgreSQL dashboard, per-server host metrics, systemd
            service state, host health from Prometheus, log search, alerts (Prometheus rules through
            Alertmanager, with acknowledge and assign) and notifications (channels, delivery status
            and retry), service operations (start, stop and restart a registered service: the
            request, an approval where a policy requires one, its state machine and its verified
            result), and the operations history and audit log (who did what, with the result, an
            execution id and an approval reference; reading the trail is itself recorded). An AWS
            adapter exists but has been exercised only against test providers, so nothing here has
            yet been shown to act on a real server; deployments are not implemented, and every
            section that depends on them says so rather than showing a zero.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-sm" style={{ color: 'var(--c-text-muted)' }}>
            {session.user.display_name}
          </span>
          <ThemeToggle />
          <button
            type="button"
            onClick={() => void signOut()}
            className="rounded border px-3 py-1 text-sm"
            style={{ borderColor: 'var(--c-border)', color: 'var(--c-text)' }}
          >
            Sign out
          </button>
        </div>
      </header>

      {session.roles.length === 0 && (
        // Not "none" dressed up as a neutral dash: a user with no role can see
        // nothing, and saying so beats an empty list they cannot explain.
        <p
          role="status"
          className="mb-6 rounded border px-4 py-2 text-sm"
          style={{ borderColor: 'var(--c-warning)', color: 'var(--c-warning)' }}
        >
          No roles granted — ask an administrator. Inventory will appear empty until then.
        </p>
      )}

      <section
        className="mb-6 rounded-lg border text-sm"
        style={{ background: 'var(--c-surface)', borderColor: 'var(--c-border)' }}
        aria-labelledby="identity-heading"
      >
        <h2 id="identity-heading" className="px-4 py-2 font-medium">
          Signed in
        </h2>
        <dl className="border-t px-4 py-2" style={{ borderColor: 'var(--c-border)' }}>
          <div className="flex justify-between gap-4 py-1">
            <dt style={{ color: 'var(--c-text-muted)' }}>Email</dt>
            <dd>{session.user.email}</dd>
          </div>
          <div className="flex justify-between gap-4 py-1">
            <dt style={{ color: 'var(--c-text-muted)' }}>Roles</dt>
            <dd>
              {session.roles.length === 0
                ? 'None'
                : session.roles.map((role) => role.name).join(', ')}
            </dd>
          </div>
          <div className="flex justify-between gap-4 py-1">
            <dt style={{ color: 'var(--c-text-muted)' }}>Session expires</dt>
            <dd>{session.session.expires_at}</dd>
          </div>
        </dl>
      </section>

      <Navigation />

      <Routes>
        {/* M1.6 makes the dashboard the landing page, in place of M1.5's
            redirect to /servers. */}
        <Route path="/" element={<Navigate to="/dashboard" replace />} />
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/servers" element={<ServerList />} />
        {/* Before the :serverId route, or "new" matches as an id. */}
        <Route path="/servers/new" element={<ServerForm />} />
        <Route path="/servers/:serverId" element={<ServerDetail />} />
        <Route path="/servers/:serverId/edit" element={<ServerEditForm />} />
        <Route path="/servers/:serverId/metrics" element={<ServerMetrics />} />
        <Route path="/servers/:serverId/services" element={<ServerServices />} />
        <Route path="/servers/:serverId/services/:serviceName" element={<ServiceDetailPage />} />
        <Route path="/services" element={<ServiceList />} />
        <Route path="/servers/:serverId/operations" element={<ServerOperations />} />
        <Route path="/operations" element={<OperationHistory />} />
        <Route path="/operations/:operationId" element={<OperationDetailPage />} />
        <Route path="/audit" element={<AuditLog />} />
        <Route path="/approvals" element={<ApprovalQueue />} />
        <Route path="/admin/approval-policies" element={<ApprovalPolicies />} />
        <Route path="/compare" element={<Compare />} />
        <Route path="/logs" element={<LogSearch />} />
        <Route path="/alerts" element={<AlertList />} />
        <Route path="/alerts/:alertId" element={<AlertDetail />} />
        <Route path="/notifications/channels" element={<ChannelList />} />
        <Route path="/applications" element={<ApplicationList />} />
        <Route path="/applications/:applicationId" element={<ApplicationDetail />} />
        <Route
          path="*"
          element={
            <p role="alert" style={{ color: 'var(--c-text-muted)' }}>
              No such page.
            </p>
          }
        />
      </Routes>

      <div className="mt-10">
        <BackendStatus />
      </div>
    </main>
  );
}

export function App(): React.JSX.Element {
  return (
    <BrowserRouter>
      <SessionProvider>
        <Shell />
      </SessionProvider>
    </BrowserRouter>
  );
}
