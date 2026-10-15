import { render, screen, waitFor } from '@testing-library/react';
import axe from 'axe-core';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { Session, SessionState } from '@/api/session';
import { SessionContext } from '@/auth/sessionContext';
import { ServerEditForm } from '@/pages/servers/ServerForm';

/**
 * The edit form (§14.4), and the three things about it that are not ordinary
 * form handling:
 *
 * 1. **Immutable fields are absent, not rejected.** `id`,
 *    `cloud_account_id`, `provider_resource_id` and `created_at` answer
 *    `422 not_permitted`, and `hostname` is immutable too — so none of them
 *    is rendered as an input. A test asserts their absence, because "the
 *    backend will refuse it" is not the same as "the form does not offer it".
 * 2. **An environment change is privilege-relevant**, so it is confirmed
 *    explicitly and the body is checked to carry it only when it moved.
 * 3. **A `409` is shown, never retried.** The assertion that matters is the
 *    `fetch` call count: a form that quietly re-sent without the precondition
 *    would look identical on screen.
 *
 * The permission tests are about *rendering*. The advisory `permissions`
 * array hides and labels controls for usability only (docs/SECURITY.md §6);
 * the last test here submits an environment change from a session that does
 * not list Administrator and watches the request go out anyway, which is what
 * "the frontend is never the control" has to mean in practice.
 */

const SERVER = {
  id: 's1',
  cloud_account_id: 'a1',
  provider_resource_id: 'i-0abc',
  hostname: 'web-01.internal.example',
  display_name: 'Web 01',
  operating_system: 'LINUX',
  os_version: 'Debian 12',
  environment: 'staging',
  region: 'eu-west-1',
  private_ip: '10.0.0.10',
  owner_team: 'platform',
  management_method: 'RESTRICTED_AGENT',
  monitoring_enabled: true,
  metrics_instance: 'web-01.internal.example:9100',
  agent_status: 'UNKNOWN',
  status: 'PENDING',
  last_seen_at: null,
  registered_at: '2026-10-14T00:00:00Z',
  created_at: '2026-10-14T00:00:00Z',
  updated_at: '2026-10-14T09:15:07Z',
  provider_metadata: {},
  stale: null,
  health: { data_state: 'not_configured', state: null, collected_at: null },
  applications: [],
  registered_service_count: 0,
  open_alerts: { data_state: 'not_configured', counts: null, collected_at: null },
  management_capabilities: {
    data_state: 'not_configured',
    capabilities: null,
    collected_at: null,
  },
};

function session(permissions: string[], readOnly = false): Session {
  return {
    user: { id: 'u1', email: 'a@example.invalid', display_name: 'A', status: 'ACTIVE' },
    roles: [{ name: 'OPERATOR', scope: {} }],
    permissions,
    session: { expires_at: '2026-10-16T00:00:00Z', idle_timeout_seconds: 1800 },
    demo_mode: false,
    read_only_mode: readOnly,
  };
}

const authenticated = (permissions: string[], readOnly = false): SessionState => ({
  kind: 'authenticated',
  session: session(permissions, readOnly),
});

/**
 * Reads always answer the row; the write answers what the test asked for.
 *
 * Branching on the method rather than on the call count, so a test that
 * re-reads after a failed save still gets a readable server — which is the
 * difference between testing the conflict path and testing a backend that
 * also broke the `GET`.
 */
function mockServer(onWrite: { status: number; body?: unknown } = { status: 200 }) {
  const fetchMock = vi.fn().mockImplementation((_url: string, init?: RequestInit) => {
    if (init?.method === undefined) {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(SERVER) });
    }
    return Promise.resolve({
      ok: onWrite.status < 400,
      status: onWrite.status,
      json: () => Promise.resolve(onWrite.body ?? SERVER),
    });
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function renderEdit(state: SessionState = authenticated(['server:read', 'server:write'])) {
  return render(
    <SessionContext.Provider
      value={{ state, refresh: () => Promise.resolve(), signOut: () => Promise.resolve() }}
    >
      <MemoryRouter initialEntries={['/servers/s1/edit']}>
        <Routes>
          <Route path="/servers/:serverId/edit" element={<ServerEditForm />} />
          <Route path="/servers/:serverId" element={<p>detail page</p>} />
        </Routes>
      </MemoryRouter>
    </SessionContext.Provider>,
  );
}

/** The body of the `PATCH`, which is the second call in every save test. */
function patchBody(fetchMock: ReturnType<typeof vi.fn>): Record<string, unknown> {
  const call = fetchMock.mock.calls.find(
    (entry) => (entry[1] as RequestInit | undefined)?.method === 'PATCH',
  );
  const init = call?.[1] as { body: string };
  return JSON.parse(init.body) as Record<string, unknown>;
}

const save = async () => {
  await userEvent.click(screen.getByRole('button', { name: 'Save changes' }));
};

describe('ServerEditForm', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it('prefills the mutable fields from the loaded row', async () => {
    mockServer();
    renderEdit();
    expect(await screen.findByLabelText(/Display name/)).toHaveValue('Web 01');
    expect(screen.getByLabelText(/Owner team/)).toHaveValue('platform');
    expect(screen.getByLabelText(/Private IP/)).toHaveValue('10.0.0.10');
    expect(screen.getByLabelText(/^Status/)).toHaveValue('PENDING');
    expect(screen.getByLabelText('Monitoring enabled')).toBeChecked();
  });

  it('does not offer an immutable field for editing', async () => {
    mockServer();
    renderEdit();
    await screen.findByLabelText(/Display name/);
    // Not "renders it disabled": §14.4 answers 422 not_permitted, so there is
    // nothing here for the operator to type into in the first place.
    expect(screen.queryByLabelText(/Cloud account id/)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/Provider resource id/)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/Hostname/)).not.toBeInTheDocument();
    // The hostname is still shown, as text, because it identifies the host.
    expect(screen.getByText(/web-01\.internal\.example/)).toBeInTheDocument();
  });

  it('sends only the fields that moved', async () => {
    const fetchMock = mockServer();
    renderEdit();
    const owner = await screen.findByLabelText(/Owner team/);
    await userEvent.clear(owner);
    await userEvent.type(owner, 'sre');
    await save();

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(2);
    });
    expect(patchBody(fetchMock)).toEqual({ owner_team: 'sre' });
  });

  it('sends an emptied optional field as null, so it can be cleared', async () => {
    const fetchMock = mockServer();
    renderEdit();
    await userEvent.clear(await screen.findByLabelText(/Region/));
    await save();

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(2);
    });
    // Omitting it would mean "leave it alone" — the backend dumps the body
    // with exclude_unset, so absent and null are two different instructions.
    expect(patchBody(fetchMock)).toEqual({ region: null });
  });

  it('sends If-Unmodified-Since from the row it was filled from', async () => {
    const fetchMock = mockServer();
    renderEdit();
    await userEvent.clear(await screen.findByLabelText(/Owner team/));
    await save();

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(2);
    });
    const init = fetchMock.mock.calls[1]?.[1] as RequestInit;
    const headers = new Headers(init.headers);
    expect(headers.get('If-Unmodified-Since')).toBe('Wed, 14 Oct 2026 09:15:07 GMT');
  });

  it('sends nothing at all when nothing changed', async () => {
    const fetchMock = mockServer();
    renderEdit();
    await screen.findByLabelText(/Display name/);
    await save();

    expect(await screen.findByText(/Nothing changed/)).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('says an environment change is not an ordinary field edit', async () => {
    mockServer();
    renderEdit();
    await screen.findByLabelText(/Display name/);
    expect(screen.getByText(/authorisation boundary/)).toBeInTheDocument();
    expect(screen.getByText('SERVER_ENVIRONMENT_CHANGED')).toBeInTheDocument();
  });

  it('will not save an environment change until it is confirmed', async () => {
    const fetchMock = mockServer();
    renderEdit();
    await userEvent.selectOptions(await screen.findByLabelText(/^Environment/), 'production');

    expect(screen.getByRole('button', { name: 'Save changes' })).toBeDisabled();
    await userEvent.click(screen.getByRole('checkbox', { name: /Move this server from/ }));
    await save();

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(2);
    });
    expect(patchBody(fetchMock)).toEqual({ environment: 'production' });
  });

  it('shows a conflict and does not retry it', async () => {
    const fetchMock = mockServer({
      status: 409,
      body: {
        error: {
          code: 'conflict',
          message: 'The server was modified after the time given in If-Unmodified-Since.',
        },
      },
    });
    renderEdit();
    await userEvent.clear(await screen.findByLabelText(/Owner team/));
    await save();

    expect(await screen.findByRole('alert')).toHaveTextContent('modified after the time given');
    expect(screen.getByRole('button', { name: /Discard my changes and reload/ })).toBeVisible();
    // One GET, one PATCH. A silent re-send without the precondition would be
    // invisible on screen and is exactly what this count rules out.
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('reloads the row when the conflict is acknowledged', async () => {
    const fetchMock = mockServer({
      status: 409,
      body: { error: { message: 'The server was modified.' } },
    });
    renderEdit();
    await userEvent.clear(await screen.findByLabelText(/Owner team/));
    await save();
    await screen.findByRole('alert');

    await userEvent.click(screen.getByRole('button', { name: /Discard my changes and reload/ }));
    await waitFor(() => {
      expect(screen.getByLabelText(/Owner team/)).toHaveValue('platform');
    });
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it('maps a field error onto its input', async () => {
    const fetchMock = mockServer({
      status: 422,
      body: {
        error: {
          message: 'The request contains invalid fields.',
          details: {
            fields: [
              {
                loc: ['body', 'metrics_instance'],
                code: 'pattern',
                message: 'must be host:port, matching the Prometheus instance label',
              },
            ],
          },
        },
      },
    });
    renderEdit();
    await userEvent.type(await screen.findByLabelText(/Metrics instance/), '-bad');
    await save();

    await waitFor(() => {
      expect(screen.getByLabelText(/Metrics instance/)).toHaveAttribute('aria-invalid', 'true');
    });
    expect(screen.getByRole('alert')).toHaveTextContent('must be host:port');
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('explains a 404 that followed an environment change', async () => {
    // §2 answers 404 rather than 403 for anything outside the caller's scope,
    // and a non-Administrator environment change takes the same path — so the
    // page names both possibilities instead of guessing one.
    mockServer({ status: 404 });
    renderEdit(authenticated(['server:read', 'server:write']));
    await userEvent.selectOptions(await screen.findByLabelText(/^Environment/), 'production');
    await userEvent.click(screen.getByRole('checkbox', { name: /Move this server from/ }));
    await save();

    expect(await screen.findByText(/needs Administrator/)).toBeInTheDocument();
  });

  it('warns a non-Administrator before they try an environment change', async () => {
    mockServer();
    renderEdit(authenticated(['server:read', 'server:write']));
    await screen.findByLabelText(/Display name/);
    expect(screen.getByText(/does not list Administrator/)).toBeInTheDocument();
  });

  it('drops that warning for an Administrator', async () => {
    mockServer();
    renderEdit(authenticated(['server:read', 'server:write', 'admin:users']));
    await screen.findByLabelText(/Display name/);
    expect(screen.queryByText(/does not list Administrator/)).not.toBeInTheDocument();
  });

  it('still sends an environment change the advisory list does not cover', async () => {
    // The warning above is usability, not authorisation: the backend decides,
    // and a UI that refused to send would be making the decision instead.
    const fetchMock = mockServer();
    renderEdit(authenticated(['server:read', 'server:write']));
    await userEvent.selectOptions(await screen.findByLabelText(/^Environment/), 'production');
    await userEvent.click(screen.getByRole('checkbox', { name: /Move this server from/ }));
    await save();

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(2);
    });
    expect(patchBody(fetchMock)).toEqual({ environment: 'production' });
  });

  it('disables saving in read-only mode', async () => {
    mockServer();
    renderEdit(authenticated(['server:read', 'server:write'], true));
    await screen.findByLabelText(/Display name/);
    expect(screen.getByRole('button', { name: 'Save changes' })).toBeDisabled();
  });

  it('reports a server outside the caller’s scope rather than an empty form', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: false, status: 404, json: () => Promise.resolve({}) }),
    );
    renderEdit();
    expect(await screen.findByRole('alert')).toHaveTextContent('Not found');
    expect(screen.queryByRole('button', { name: 'Save changes' })).not.toBeInTheDocument();
  });
  // --- accessibility ----------------------------------------------------------

  it('has no axe violations', async () => {
    mockServer();
    const { container } = renderEdit();
    await screen.findByLabelText(/Display name/);

    const results = await axe.run(container, {
      // As in Dashboard.test.tsx: landmark and page-level rules need the whole
      // document, and jsdom cannot compute a contrast ratio.
      runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa'] },
      rules: { 'color-contrast': { enabled: false } },
    });
    expect(results.violations.map((violation) => violation.id)).toEqual([]);
  });
});
