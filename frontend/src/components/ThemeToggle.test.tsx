import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { ThemeToggle } from '@/components/ThemeToggle';

describe('ThemeToggle', () => {
  it('follows the OS preference by default', () => {
    render(<ThemeToggle />);
    expect(screen.getByRole('radio', { name: /system/i })).toBeChecked();
    expect(document.documentElement.hasAttribute('data-theme')).toBe(false);
  });

  it('an explicit choice overrides the OS preference', async () => {
    render(<ThemeToggle />);
    await userEvent.click(screen.getByRole('radio', { name: /dark/i }));
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark');

    await userEvent.click(screen.getByRole('radio', { name: /light/i }));
    expect(document.documentElement.getAttribute('data-theme')).toBe('light');
  });

  it('returning to system clears the override', async () => {
    render(<ThemeToggle />);
    await userEvent.click(screen.getByRole('radio', { name: /dark/i }));
    await userEvent.click(screen.getByRole('radio', { name: /system/i }));
    expect(document.documentElement.hasAttribute('data-theme')).toBe(false);
  });

  it('remembers the choice across mounts', async () => {
    const { unmount } = render(<ThemeToggle />);
    await userEvent.click(screen.getByRole('radio', { name: /dark/i }));
    unmount();
    render(<ThemeToggle />);
    expect(screen.getByRole('radio', { name: /dark/i })).toBeChecked();
  });

  it('still renders when storage is unavailable', () => {
    // Private windows and blocked site data both throw on access.
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    render(<ThemeToggle />);
    expect(screen.getByRole('radio', { name: /system/i })).toBeChecked();
  });

  it('is reachable by keyboard and labelled as a group', async () => {
    render(<ThemeToggle />);
    expect(screen.getByRole('group', { name: /colour theme/i })).toBeInTheDocument();
    await userEvent.tab();
    expect(screen.getByRole('radio', { name: /system/i })).toHaveFocus();
  });
});
