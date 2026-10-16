import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ServerMetrics } from '@/pages/servers/ServerMetrics';

/**
 * The metrics page, against scripted §15.1/§15.2 answers.
 *
 * The assertion that matters most is negative: **a card with no number never
 * shows one.** Every reading below that lacks a value is checked for a reason
 * *and* for the absence of a digit-and-unit pair, because a zero rendered for
 * "unavailable" is exactly the failure PRD §4.4 names.
 */

const SERVER = { display_name: 'Web 01' };

const COLLECTED = '2026-10-16T08:59:45Z';

const CURRENT = {
  server_id: 's1',
  source: 'prometheus',
  demo_mode: false,
  thresholds: { cpu_utilisation: { warning: 80, critical: 90 } },
  metrics: [
    {
      template: 'cpu_utilisation',
      unit: 'percent',
      value: 91.5,
      data_state: 'ok',
      collected_at: COLLECTED,
      threshold_state: 'critical',
    },
    {
      template: 'memory_utilisation',
      unit: 'percent',
      value: null,
      data_state: 'stale',
      collected_at: '2026-10-16T08:50:00Z',
      stale_after_seconds: 60,
    },
    {
      template: 'disk_utilisation',
      unit: 'percent',
      value: null,
      data_state: 'unavailable',
      unavailable_reason: 'no_data',
      collected_at: null,
    },
    {
      template: 'load_average',
      unit: 'load',
      value: null,
      data_state: 'unsupported',
      unavailable_reason: 'unsupported_os',
      collected_at: null,
    },
    {
      template: 'agent_up',
      unit: 'boolean',
      value: 0,
      data_state: 'ok',
      collected_at: COLLECTED,
    },
  ],
};

const T = (minutes: number) => new Date(Date.UTC(2026, 9, 16, 8, minutes)).toISOString();

const RANGE = {
  server_id: 's1',
  start: T(0),
  end: T(60),
  step_seconds: 15,
  source: 'prometheus',
  demo_mode: false,
  thresholds: { cpu_utilisation: { warning: 80, critical: 90 } },
  series: [
    {
      template: 'cpu_utilisation',
      unit: 'percent',
      data_state: 'ok',
      // A hole in the middle: the page must pass it through untouched.
      points: [
        [T(0), 10],
        [T(15), 20],
        [T(30), null],
        [T(45), 40],
        [T(60), 50],
      ],
    },
    {
      template: 'memory_utilisation',
      unit: 'percent',
      data_state: 'unavailable',
      unavailable_reason: 'no_data',
      points: null,
    },
  ],
};

function ok(body: unknown) {
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
}

function mockApi(overrides: { current?: () => Promise<unknown>; status?: number } = {}) {
  const fetchMock = vi.fn().mockImplementation((url: string) => {
    if (overrides.status !== undefined) {
      return Promise.resolve({
        ok: false,
        status: overrides.status,
        json: () => Promise.resolve({}),
      });
    }
    if (url.includes('/metrics/range')) return ok(RANGE);
    if (url.includes('/metrics')) return overrides.current?.() ?? ok(CURRENT);
    return ok(SERVER);
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/servers/s1/metrics']}>
      <Routes>
        <Route path="/servers/:serverId/metrics" element={<ServerMetrics />} />
      </Routes>
    </MemoryRouter>,
  );
}

const card = (name: RegExp) => {
  const heading = screen.getByRole('heading', { name });
  const item = heading.closest('li');
  if (item === null) throw new Error('card not found');
  return within(item);
};

describe('current values', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it('shows a value with its unit, threshold state and UTC collection time', async () => {
    mockApi();
    renderPage();

    const cpu = await waitFor(() => card(/CPU utilisation/));
    expect(cpu.getByText('91.5 %')).toBeInTheDocument();
    expect(cpu.getByText('Critical')).toBeInTheDocument();
    expect(cpu.getByText(/Collected .*UTC/)).toBeInTheDocument();
  });

  it('gives a stale reading no number, and says so', async () => {
    mockApi();
    renderPage();

    const memory = await waitFor(() => card(/Memory utilisation/));
    expect(memory.getByText('Stale')).toBeInTheDocument();
    expect(memory.queryByText(/\d+(\.\d+)? %/)).not.toBeInTheDocument();
    // Still dated: when it was last collected is the useful part of "stale".
    expect(memory.getByText(/Collected .*UTC/)).toBeInTheDocument();
  });

  it('gives an unavailable reading a reason and never a zero', async () => {
    mockApi();
    renderPage();

    const disk = await waitFor(() => card(/Disk utilisation/));
    expect(disk.getByText(/Unavailable — No data was returned for this host/)).toBeInTheDocument();
    expect(disk.queryByText(/\d+(\.\d+)? %/)).not.toBeInTheDocument();
    expect(disk.getByText('Never collected')).toBeInTheDocument();
  });

  it('keeps unsupported distinct from unavailable', async () => {
    mockApi();
    renderPage();

    const load = await waitFor(() => card(/Load average/));
    expect(
      load.getByText(/Not supported — Not supported for this operating system/),
    ).toBeInTheDocument();
    expect(load.queryByText(/Unavailable/)).not.toBeInTheDocument();
  });

  it('reports a real 0 for the agent as "Down", not as an absence', async () => {
    mockApi();
    renderPage();

    const agent = await waitFor(() => card(/Monitoring agent/));
    expect(agent.getByText('Down')).toBeInTheDocument();
  });
});

describe('history', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it('draws the null point as a gap through the real chart', async () => {
    mockApi();
    const { container } = renderPage();

    await screen.findByRole('group', { name: /CPU utilisation/ });
    const d = container.querySelector('path[stroke="var(--series-1)"]')?.getAttribute('d') ?? '';
    // 10, 20 | hole | 40, 50 — two runs, so two subpaths and no baseline vertex.
    expect((d.match(/M/g) ?? []).length).toBe(2);
    expect(d).not.toMatch(/,180(?![\d.])/);
  });

  it('shows the window and the step, and says what a gap means', async () => {
    mockApi();
    renderPage();

    expect(await screen.findByText(/gaps mean no data was collected/)).toBeInTheDocument();
    expect(screen.getByText(/one point every/)).toBeInTheDocument();
  });

  it('replaces an unavailable chart with its reason instead of an empty plot', async () => {
    mockApi();
    renderPage();

    expect(
      await screen.findByText(/Memory utilisation: Unavailable — No data was returned/),
    ).toBeInTheDocument();
  });

  it('asks for a different step when the range changes', async () => {
    const fetchMock = mockApi();
    renderPage();
    await screen.findByRole('group', { name: /CPU utilisation/ });

    await userEvent.selectOptions(screen.getByLabelText('Time range'), '24h');

    await waitFor(() => {
      const urls = fetchMock.mock.calls.map((call) => String(call[0]));
      expect(urls.some((url) => url.includes('step_seconds=300'))).toBe(true);
    });
  });

  it('never sends a query: only template ids reach the API', async () => {
    const fetchMock = mockApi();
    renderPage();
    await screen.findByRole('group', { name: /CPU utilisation/ });

    for (const call of fetchMock.mock.calls) {
      const url = String(call[0]);
      expect(url).not.toMatch(/promql|query=|node_/i);
    }
  });
});

describe('failure', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it('renders a 404 as not found, not as an empty page of zeros', async () => {
    mockApi({ status: 404 });
    renderPage();

    const alerts = await screen.findAllByRole('alert');
    expect(alerts[0]).toHaveTextContent(/Not found/);
    expect(screen.queryByText(/\d+(\.\d+)? %/)).not.toBeInTheDocument();
  });
});
