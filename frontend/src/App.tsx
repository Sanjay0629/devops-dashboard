import { BackendStatus } from '@/components/BackendStatus';
import { ThemeToggle } from '@/components/ThemeToggle';
import { SessionProvider } from '@/auth/SessionProvider';
import { useSession } from '@/auth/useSession';
import { Login } from '@/pages/Login';

/**
 * Composition root and session gate.
 *
 * Three states, rendered as three different things, because conflating them is
 * the bug this file exists to avoid:
 *
 * - `loading` — we have not asked yet. Not "signed out".
 * - `anonymous` — the server said `401`. Show the login page.
 * - `unreachable` — we could not ask. Show the login page *saying so*, rather
 *   than implying the user was logged out by a backend that is simply down.
 *
 * The real routing shell is M1.6; until then "signed in" lands on the backend
 * status panel.
 */

function Shell(): React.JSX.Element {
  const { state, signOut } = useSession();

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
    <main className="mx-auto max-w-2xl px-4 py-10">
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

      <header className="mb-8 flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">DevOps Control Plane</h1>
          <p className="text-sm" style={{ color: 'var(--c-text-muted)' }}>
            Milestone M1.3 — authentication. No monitoring, inventory or integrations are
            implemented yet.
          </p>
        </div>
        <ThemeToggle />
      </header>

      <section
        className="mb-6 rounded-lg border"
        style={{ background: 'var(--c-surface)', borderColor: 'var(--c-border)' }}
        aria-labelledby="identity-heading"
      >
        <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
          <h2 id="identity-heading" className="font-medium">
            Signed in
          </h2>
          <button
            type="button"
            onClick={() => void signOut()}
            className="rounded border px-3 py-1 text-sm"
            style={{ borderColor: 'var(--c-border)', color: 'var(--c-text)' }}
          >
            Sign out
          </button>
        </div>
        <dl className="border-t px-4 py-3 text-sm" style={{ borderColor: 'var(--c-border)' }}>
          <div className="flex justify-between gap-4 py-1">
            <dt style={{ color: 'var(--c-text-muted)' }}>Name</dt>
            <dd>{session.user.display_name}</dd>
          </div>
          <div className="flex justify-between gap-4 py-1">
            <dt style={{ color: 'var(--c-text-muted)' }}>Email</dt>
            <dd>{session.user.email}</dd>
          </div>
          <div className="flex justify-between gap-4 py-1">
            <dt style={{ color: 'var(--c-text-muted)' }}>Roles</dt>
            <dd>
              {session.roles.length === 0 ? (
                // Not "none" dressed up as a neutral dash: a user with no role
                // can see nothing, and saying so is more useful than hiding it.
                <span style={{ color: 'var(--c-warning)' }}>
                  No roles granted — ask an administrator
                </span>
              ) : (
                session.roles.map((role) => role.name).join(', ')
              )}
            </dd>
          </div>
          <div className="flex justify-between gap-4 py-1">
            <dt style={{ color: 'var(--c-text-muted)' }}>Session expires</dt>
            <dd>{session.session.expires_at}</dd>
          </div>
        </dl>
      </section>

      <BackendStatus />
    </main>
  );
}

export function App(): React.JSX.Element {
  return (
    <SessionProvider>
      <Shell />
    </SessionProvider>
  );
}
