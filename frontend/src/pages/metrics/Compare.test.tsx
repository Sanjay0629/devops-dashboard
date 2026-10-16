import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { Compare } from '@/pages/metrics/Compare';

/**
 * §15.3 from the page's side.
 *
 * What the page must get right is *honesty about what was compared*: the
 * request body names exactly the selection (and nothing like a query), a
 * server the backend dropped is announced, an included server with no data is
 * listed with its reason rather than silently absent, and a colour stays with
 * its server when another is deselected.
 */

const T = (minutes: number) => new Date(Date.UTC(2026, 9, 16, 8, minutes)).toISOString();

const SERVERS = {
  items: [
    { id: 's1', display_name: 'Web 1', environment: 'staging' },
    { id: 's2', display_name: 'DB 1', environment: 'staging' },
    { id: 's3', display_name: 'Bare', environment: 'staging' },
  ],
  page: { limit: 50, next_cursor: null, has_more: false, total: null },
  demo_mode: false,
};

const TEMPLATES = {
  templates: [
    { template: 'cpu_utilisation', unit: 'percent', description: '', applies_to_os: ['LINUX'] },
    { template: 'load_average', unit: 'load', description: '', applies_to_os: ['LINUX'] },
  ],
  source: 'prometheus',
  demo_mode: false,
};

const points = (values: (number | null)[]) => values.map((v, i) => [T(i * 15), v]);

function comparison(overrides: Record<string, unknown> = {}) {
  return {
    metric: 'cpu_utilisation',
    unit: 'percent',
    start: T(0),
    end: T(60),
    step_seconds: 15,
    thresholds: { warning: 80, critical: 90 },
    excluded_count: 0,
    source: 'prometheus',
    demo_mode: false,
    series: [
      {
        server_id: 's1',
        display_name: 'Web 1',
        environment: 'staging',
        data_state: 'ok',
        points: points([10, 20, null, 40, 50]),
      },
      {
        server_id: 's3',
        display_name: 'Bare',
        environment: 'staging',
        data_state: 'not_configured',
        unavailable_reason: 'no_metrics_instance',
        points: null,
      },
    ],
    ...overrides,
  };
}

function ok(body: unknown) {
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
}

function mockApi(answer: () => unknown = () => comparison()) {
  const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
    if (init?.method === 'POST') return ok(answer());
    if (url.includes('/metrics/templates')) return ok(TEMPLATES);
    return ok(SERVERS);
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

const posted = (fetchMock: ReturnType<typeof vi.fn>) => {
  const call = fetchMock.mock.calls.find(
    (entry) => (entry[1] as RequestInit | undefined)?.method === 'POST',
  );
  return call === undefined
    ? null
    : (JSON.parse((call[1] as { body: string }).body) as Record<string, unknown>);
};

function renderPage() {
  return render(
    <MemoryRouter>
      <Compare />
    </MemoryRouter>,
  );
}

async function pick(...names: string[]) {
  for (const name of names) {
    await userEvent.click(await screen.findByRole('checkbox', { name: new RegExp(name) }));
  }
}

describe('Compare', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it('cannot compare nothing', async () => {
    mockApi();
    renderPage();
    await screen.findByRole('checkbox', { name: /Web 1/ });
    expect(screen.getByRole('button', { name: 'Compare' })).toBeDisabled();
  });

  it('posts exactly the selection and a window, with no query in it', async () => {
    const fetchMock = mockApi();
    renderPage();
    await pick('Web 1', 'Bare');
    await userEvent.click(screen.getByRole('button', { name: 'Compare' }));

    await screen.findByRole('group', { name: /CPU utilisation/ });
    const body = posted(fetchMock);
    expect(body).toMatchObject({
      metric: 'cpu_utilisation',
      server_ids: ['s1', 's3'],
      step_seconds: 15,
    });
    expect(Object.keys(body ?? {}).sort()).toEqual(
      ['from', 'metric', 'server_ids', 'step_seconds', 'to'].sort(),
    );
  });

  it('draws the hole as a gap and lists an included server that has no data', async () => {
    mockApi();
    const { container } = renderPage();
    await pick('Web 1', 'Bare');
    await userEvent.click(screen.getByRole('button', { name: 'Compare' }));

    await screen.findByRole('group', { name: /CPU utilisation/ });
    const d = container.querySelector('path[stroke="var(--series-1)"]')?.getAttribute('d') ?? '';
    expect((d.match(/M/g) ?? []).length).toBe(2);
    // Not drawn, but not dropped either — and with its reason.
    expect(
      screen.getByText(/Bare \(staging\) — Not configured — No metrics instance is registered/),
    ).toBeInTheDocument();
  });

  it('announces servers the backend left out', async () => {
    mockApi(() => comparison({ excluded_count: 2 }));
    renderPage();
    await pick('Web 1');
    await userEvent.click(screen.getByRole('button', { name: 'Compare' }));

    expect(await screen.findByRole('note')).toHaveTextContent(
      '2 requested servers were not included: not found, or outside your access.',
    );
  });

  it('sends environments when comparing by environment', async () => {
    const fetchMock = mockApi();
    renderPage();
    await userEvent.click(screen.getByRole('radio', { name: 'Environments' }));
    await userEvent.click(screen.getByRole('checkbox', { name: 'staging' }));
    await userEvent.click(screen.getByRole('checkbox', { name: 'production' }));
    await userEvent.click(screen.getByRole('button', { name: 'Compare' }));

    await waitFor(() => {
      expect(posted(fetchMock)).toMatchObject({ environments: ['staging', 'production'] });
    });
    expect(posted(fetchMock)).not.toHaveProperty('server_ids');
  });

  it('plots at most eight lines and lists the rest as not plotted', async () => {
    const many = Array.from({ length: 10 }, (_, i) => ({
      server_id: `n${String(i)}`,
      display_name: `Node ${String(i)}`,
      environment: 'staging',
      data_state: 'ok',
      points: points([1, 2, 3, 4, 5]),
    }));
    mockApi(() => comparison({ series: many }));
    const { container } = renderPage();
    await pick('Web 1');
    await userEvent.click(screen.getByRole('button', { name: 'Compare' }));

    await screen.findByRole('group', { name: /CPU utilisation/ });
    expect(container.querySelectorAll('path[stroke^="var(--series-"]')).toHaveLength(8);
    expect(
      screen.getByText(/Node 8 \(staging\) — more than 8 series; not plotted/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Node 9 \(staging\) — more than 8 series; not plotted/),
    ).toBeInTheDocument();
  });

  it('keeps a server on its colour when another is dropped from the next comparison', async () => {
    let call = 0;
    mockApi(() => {
      call += 1;
      const both = comparison().series.slice(0, 1);
      const second = {
        server_id: 's2',
        display_name: 'DB 1',
        environment: 'staging',
        data_state: 'ok',
        points: points([5, 6, 7, 8, 9]),
      };
      // First answer: Web 1 + DB 1. Second: DB 1 alone.
      return comparison({ series: call === 1 ? [...both, second] : [second] });
    });
    const { container } = renderPage();
    await pick('Web 1', 'DB 1');
    await userEvent.click(screen.getByRole('button', { name: 'Compare' }));
    await screen.findByRole('group', { name: /CPU utilisation/ });
    const first = (stroke: string) => container.querySelector(`path[stroke="${stroke}"]`);
    expect(first('var(--series-2)')).not.toBeNull(); // DB 1 took the second slot

    await userEvent.click(screen.getByRole('checkbox', { name: /Web 1/ }));
    await userEvent.click(screen.getByRole('button', { name: 'Compare' }));

    await waitFor(() => {
      expect(first('var(--series-1)')).toBeNull();
    });
    // Alone now, and still the colour it had — not repainted into slot 1.
    expect(first('var(--series-2)')).not.toBeNull();
  });

  it('shows a refusal rather than an empty chart', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((url: string, init?: RequestInit) =>
        init?.method === 'POST'
          ? Promise.resolve({
              ok: false,
              status: 422,
              json: () =>
                Promise.resolve({
                  error: { code: 'query_limit_exceeded', message: 'Too many servers.' },
                }),
            })
          : ok(url.includes('/metrics/templates') ? TEMPLATES : SERVERS),
      ),
    );
    renderPage();
    await pick('Web 1');
    await userEvent.click(screen.getByRole('button', { name: 'Compare' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Too many servers.');
    expect(screen.queryByRole('group', { name: /CPU utilisation/ })).not.toBeInTheDocument();
  });
});
