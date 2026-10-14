import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ServerForm } from '@/pages/servers/ServerForm';

/**
 * Registration form: §8 field errors land on the right inputs.
 *
 * The contract puts a per-field list in `details.fields` so a client never
 * has to parse prose (docs/API_CONTRACTS.md §8). These tests are what make
 * that worth having — including the case nobody writes by default: a
 * validation error naming a field the form does not render, which must still
 * be shown rather than silently dropped.
 */

function mockResponse(status: number, body: unknown) {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: status < 400,
    status,
    json: () => Promise.resolve(body),
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function renderForm() {
  return render(
    <MemoryRouter>
      <ServerForm />
    </MemoryRouter>,
  );
}

async function fillRequired() {
  await userEvent.type(screen.getByLabelText(/Cloud account id/), 'a1');
  await userEvent.type(screen.getByLabelText(/Provider resource id/), 'host-1');
  await userEvent.type(screen.getByLabelText(/Hostname/), 'web-01.internal.example');
  await userEvent.type(screen.getByLabelText(/Display name/), 'Web 01');
}

describe('ServerForm', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it('says a new server grants no operational permission', () => {
    renderForm();
    expect(screen.getByText(/grants no operational permission/)).toBeInTheDocument();
  });

  it('maps a field error onto its input', async () => {
    mockResponse(422, {
      error: {
        code: 'validation_failed',
        message: 'The request contains invalid fields.',
        details: {
          fields: [
            { loc: ['body', 'hostname'], code: 'pattern', message: 'must be a valid DNS name' },
          ],
        },
      },
    });
    renderForm();
    await fillRequired();
    await userEvent.click(screen.getByRole('button', { name: 'Register' }));

    const input = await screen.findByLabelText(/Hostname/);
    await waitFor(() => {
      expect(input).toHaveAttribute('aria-invalid', 'true');
    });
    expect(screen.getByRole('alert')).toHaveTextContent('must be a valid DNS name');
  });

  it('marks only the field the backend named', async () => {
    mockResponse(422, {
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
    });
    renderForm();
    await fillRequired();
    await userEvent.click(screen.getByRole('button', { name: 'Register' }));

    await waitFor(() => {
      expect(screen.getByLabelText(/Metrics instance/)).toHaveAttribute('aria-invalid', 'true');
    });
    expect(screen.getByLabelText(/Hostname/)).not.toHaveAttribute('aria-invalid', 'true');
  });

  it('shows a validation error that names no rendered field', async () => {
    // A header problem has nowhere to attach. Dropping it would make the
    // form look like it did nothing when the button was pressed.
    mockResponse(422, {
      error: {
        message: 'The request contains invalid fields.',
        details: {
          fields: [
            { loc: ['header', 'Idempotency-Key'], code: 'required', message: 'Field required' },
          ],
        },
      },
    });
    renderForm();
    await fillRequired();
    await userEvent.click(screen.getByRole('button', { name: 'Register' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('invalid fields');
  });

  it('reports a duplicate as a conflict rather than a field problem', async () => {
    mockResponse(409, {
      error: {
        code: 'duplicate_resource',
        message: 'A server with that provider resource id is already registered in this account.',
      },
    });
    renderForm();
    await fillRequired();
    await userEvent.click(screen.getByRole('button', { name: 'Register' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('already registered');
  });

  it('reports read-only mode as itself', async () => {
    mockResponse(423, {});
    renderForm();
    await fillRequired();
    await userEvent.click(screen.getByRole('button', { name: 'Register' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('read-only mode');
  });

  it('omits empty optional fields instead of sending blanks', async () => {
    const fetchMock = mockResponse(201, { id: 's1' });
    renderForm();
    await fillRequired();
    await userEvent.click(screen.getByRole('button', { name: 'Register' }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalled();
    });
    const init = fetchMock.mock.calls[0]?.[1] as { body: string };
    const body = JSON.parse(init.body) as Record<string, unknown>;
    expect(body).not.toHaveProperty('region');
    expect(body).not.toHaveProperty('private_ip');
    expect(body.hostname).toBe('web-01.internal.example');
  });

  it('sends an Idempotency-Key with every submission', async () => {
    const fetchMock = mockResponse(201, { id: 's1' });
    renderForm();
    await fillRequired();
    await userEvent.click(screen.getByRole('button', { name: 'Register' }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalled();
    });
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    const headers = new Headers(init.headers);
    expect(headers.get('Idempotency-Key')).toMatch(/^[0-9a-f-]{36}$/);
  });
});
