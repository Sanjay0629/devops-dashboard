import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { BackendStatus } from '@/components/BackendStatus';

function mockReadyz(body: unknown, { ok = true, status = 200 } = {}): void {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue({
      ok,
      status,
      json: () => Promise.resolve(body),
    }),
  );
}

describe('BackendStatus', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it('reports a ready backend', async () => {
    mockReadyz({ status: 'ok', checks: { database: true, broker: true } });
    render(<BackendStatus />);
    expect(screen.getByRole('heading', { level: 2 })).toHaveTextContent('Backend status');
    await waitFor(() => {
      expect(screen.getByRole('status')).toHaveTextContent('Ready');
    });
  });

  it('shows each dependency check with a pass or fail label', async () => {
    mockReadyz(
      { status: 'unavailable', checks: { database: true, broker: false } },
      {
        ok: false,
        status: 503,
      },
    );
    render(<BackendStatus />);
    await waitFor(() => {
      expect(screen.getByRole('status')).toHaveTextContent('Not ready');
    });
    expect(screen.getByText('database')).toBeInTheDocument();
    expect(screen.getByText('broker')).toBeInTheDocument();
    expect(screen.getAllByText('pass')).toHaveLength(1);
    expect(screen.getAllByText('fail')).toHaveLength(1);
  });

  it('reports an unreachable backend as unknown, never as healthy', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('network down')));
    render(<BackendStatus />);
    await waitFor(() => {
      expect(screen.getByRole('status')).toHaveTextContent('Unreachable');
    });
    // The regression that PRD §4.2 explicitly warns about.
    expect(screen.getByText(/unknown/i)).toBeInTheDocument();
    expect(screen.queryByText('Ready')).not.toBeInTheDocument();
    expect(screen.queryByText('pass')).not.toBeInTheDocument();
    expect(screen.queryByText('0')).not.toBeInTheDocument();
  });

  it('treats a malformed response as unreachable rather than trusting it', async () => {
    mockReadyz({ unexpected: 'shape' });
    render(<BackendStatus />);
    await waitFor(() => {
      expect(screen.getByRole('status')).toHaveTextContent('Unreachable');
    });
  });

  it('does not claim a check time before any check has run', () => {
    vi.stubGlobal('fetch', vi.fn().mockReturnValue(new Promise(() => undefined)));
    render(<BackendStatus />);
    expect(screen.getByText('Not yet checked')).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('Checking');
  });

  it('refreshes on demand', async () => {
    const body = { status: 'ok', checks: { database: true } };
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve(body),
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<BackendStatus />);
    await waitFor(() => {
      expect(screen.getByRole('status')).toHaveTextContent('Ready');
    });
    await userEvent.click(screen.getByRole('button', { name: /refresh/i }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(2);
    });
  });

  it('conveys status with text, not colour alone', async () => {
    mockReadyz({ status: 'ok', checks: { database: true } });
    render(<BackendStatus />);

    // PRD §8: the state must survive with colour removed, so it has to be in
    // the text a screen reader reads out — not only in a style attribute.
    const status = await waitFor(() => {
      const element = screen.getByRole('status');
      expect(element).toHaveTextContent(/ready/i);
      return element;
    });

    // The decorative glyph is hidden, so the announcement is "Ready" and not
    // "check mark Ready".
    expect(status.querySelector('[aria-hidden="true"]')).not.toBeNull();
    expect(status).toHaveTextContent('Ready');
  });
});
