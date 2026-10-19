import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { StatusBadge } from '@/components/StatusBadge';

/**
 * The two rules `StatusBadge` exists to enforce (PRD §8, PRD §4.2).
 *
 * Both are the kind that regress silently: a default case that falls through
 * to "healthy" looks fine on a screen full of healthy servers, and dropping
 * the glyph looks fine to anyone who can see the colour.
 */

describe('StatusBadge', () => {
  it('renders an unknown status as unknown, never as healthy', () => {
    render(<StatusBadge value={null} />);
    expect(screen.getByText('Unknown')).toBeInTheDocument();
    expect(screen.queryByText('Healthy')).toBeNull();
  });

  it('conveys status by more than colour', () => {
    // PRD §8. A red dot is not a label: it reads as nothing to a screen
    // reader, to a monochrome print-out, or to a red/green deficiency. So
    // every badge carries a word, and a shape beside it.
    const { container } = render(<StatusBadge value="critical" />);
    expect(screen.getByText('Critical')).toBeInTheDocument();
    // The glyph is `aria-hidden`: a second *visual* channel, not a second
    // announcement. The word is the accessible name.
    const glyph = container.querySelector('[aria-hidden="true"]');
    expect(glyph?.textContent).toBeTruthy();
  });

  it('gives healthy and critical different shapes, not just different colours', () => {
    const healthy = render(<StatusBadge value="healthy" />).container.querySelector(
      '[aria-hidden="true"]',
    )?.textContent;
    const critical = render(<StatusBadge value="critical" />).container.querySelector(
      '[aria-hidden="true"]',
    )?.textContent;
    expect(healthy).not.toEqual(critical);
  });

  it('shows an unrecognised status rather than swallowing it', () => {
    // A status the UI has not been taught about is still a fact about the
    // host. Rendering nothing would be the quiet kind of wrong.
    render(<StatusBadge value="SOMETHING_NEW" />);
    expect(screen.getByText('SOMETHING_NEW')).toBeInTheDocument();
  });

  it('draws every service state with its own word and its own shape', () => {
    // PRD §4.5. A dead host must never look like stopped services, so UNKNOWN,
    // INACTIVE and UNCOLLECTED are three different pictures and three different
    // words — and none of them is the word for a healthy unit.
    const states = [
      'ACTIVE',
      'INACTIVE',
      'FAILED',
      'ACTIVATING',
      'DEACTIVATING',
      'UNCOLLECTED',
      'UNSUPPORTED',
      'UNKNOWN',
    ];
    const words = new Map<string, string>();
    const glyphs = new Map<string, string>();
    for (const state of states) {
      const { container, unmount } = render(<StatusBadge value={state} />);
      const glyph = container.querySelector('[aria-hidden="true"]')?.textContent ?? '';
      expect(glyph).toBeTruthy();
      glyphs.set(state, glyph);
      words.set(state, container.textContent.replace(glyph, ''));
      unmount();
    }
    expect(new Set(words.values()).size).toBe(states.length);
    // Shapes may repeat only where the meaning does not collide: the three
    // that must never be confused are pairwise different.
    const trio = ['INACTIVE', 'UNKNOWN', 'UNCOLLECTED'].map((state) => glyphs.get(state));
    expect(new Set(trio).size).toBe(3);
    expect(words.get('FAILED')).toBe('Failed');
    expect(words.get('INACTIVE')).toBe('Inactive');
    expect(words.get('UNKNOWN')).toBe('Unknown');
  });

  it('does not use the raw enum name for a service state', () => {
    render(<StatusBadge value="UNCOLLECTED" />);
    expect(screen.queryByText('UNCOLLECTED')).toBeNull();
    expect(screen.getByText('Not collected')).toBeInTheDocument();
  });

  it('draws a notification that gave up differently from one that failed for good', () => {
    const labels = new Map<string, string>();
    for (const state of ['PENDING', 'SENT', 'FAILED', 'ABANDONED']) {
      const { container, unmount } = render(<StatusBadge value={state} />);
      labels.set(state, container.textContent);
      unmount();
    }
    expect(new Set(labels.values()).size).toBe(4);
    expect(labels.get('SENT')).toContain('Sent');
    expect(labels.get('ABANDONED')).toContain('Gave up');
    expect(screen.queryByText('ABANDONED')).toBeNull();
  });
});
