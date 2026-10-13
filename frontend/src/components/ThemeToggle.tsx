import { useEffect, useState } from 'react';

export type Theme = 'light' | 'dark' | 'system';

const STORAGE_KEY = 'dcp.theme';

function readStoredTheme(): Theme {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    return stored === 'light' || stored === 'dark' ? stored : 'system';
  } catch {
    // Private windows and blocked site data both throw here. A remembered
    // theme is a convenience, never a requirement.
    return 'system';
  }
}

function applyTheme(theme: Theme): void {
  const root = document.documentElement;
  if (theme === 'system') {
    root.removeAttribute('data-theme');
  } else {
    root.setAttribute('data-theme', theme);
  }
}

/**
 * Light / dark / follow-the-OS selector (PRD §8).
 *
 * An explicit choice beats the OS preference, and is remembered per browser.
 */
export function ThemeToggle(): React.JSX.Element {
  const [theme, setTheme] = useState<Theme>(readStoredTheme);

  useEffect(() => {
    applyTheme(theme);
    try {
      if (theme === 'system') {
        localStorage.removeItem(STORAGE_KEY);
      } else {
        localStorage.setItem(STORAGE_KEY, theme);
      }
    } catch {
      // Non-fatal, as above.
    }
  }, [theme]);

  return (
    <fieldset className="flex items-center gap-2 border-0 p-0">
      <legend className="sr-only">Colour theme</legend>
      {(['light', 'dark', 'system'] as const).map((option) => (
        <label
          key={option}
          className="cursor-pointer rounded border px-2 py-1 text-sm capitalize"
          style={{
            borderColor: 'var(--c-border)',
            background: theme === option ? 'var(--c-bg)' : 'transparent',
            fontWeight: theme === option ? 600 : 400,
          }}
        >
          <input
            type="radio"
            name="theme"
            value={option}
            checked={theme === option}
            onChange={() => {
              setTheme(option);
            }}
            className="sr-only"
          />
          {option}
        </label>
      ))}
    </fieldset>
  );
}
