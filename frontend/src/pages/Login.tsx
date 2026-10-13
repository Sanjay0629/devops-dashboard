import { loginUrl } from '@/api/session';
import { ThemeToggle } from '@/components/ThemeToggle';

/**
 * Login page.
 *
 * There is no form here, and there never will be: authentication is entirely
 * external via OIDC, and no password exists anywhere in the system
 * (docs/SECURITY.md §2, docs/API_CONTRACTS.md §29). So this page's whole job
 * is to start a full-page navigation to the backend, which redirects to the
 * identity provider.
 *
 * It is an anchor rather than a button with `fetch`: the flow is a chain of
 * `302`s the browser must follow, and an XHR cannot complete it.
 */
export function Login({
  reason = 'anonymous',
}: {
  /** Why the user is here, so an outage is not rendered as a logout. */
  reason?: 'anonymous' | 'unreachable';
}): React.JSX.Element {
  return (
    <main className="mx-auto max-w-md px-4 py-16">
      <div className="mb-6 flex items-start justify-between gap-4">
        <h1 className="text-2xl font-semibold">DevOps Control Plane</h1>
        <ThemeToggle />
      </div>

      <section
        className="rounded-lg border p-6"
        style={{ background: 'var(--c-surface)', borderColor: 'var(--c-border)' }}
        aria-labelledby="sign-in-heading"
      >
        <h2 id="sign-in-heading" className="mb-2 text-lg font-medium">
          Sign in
        </h2>

        {reason === 'unreachable' ? (
          <p className="mb-4 text-sm" role="status" style={{ color: 'var(--c-critical)' }}>
            The backend could not be reached, so your sign-in state is <strong>unknown</strong> —
            you may already be signed in. Signing in again will not work until the backend responds.
          </p>
        ) : (
          <p className="mb-4 text-sm" style={{ color: 'var(--c-text-muted)' }}>
            You will be redirected to your organisation&rsquo;s identity provider. This application
            has no password of its own.
          </p>
        )}

        <a
          href={loginUrl()}
          className="inline-block rounded border px-4 py-2 font-medium"
          style={{ borderColor: 'var(--c-border)', color: 'var(--c-text)' }}
        >
          Continue to identity provider
        </a>

        <p className="mt-6 text-xs" style={{ color: 'var(--c-text-muted)' }}>
          Access is granted per role inside this application, not by your identity provider, so a
          new account starts with no permissions until an administrator grants a role.
        </p>
      </section>
    </main>
  );
}
