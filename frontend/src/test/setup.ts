import '@testing-library/jest-dom/vitest';

import { afterEach, vi } from 'vitest';
import { cleanup, configure } from '@testing-library/react';

// Testing Library's `waitFor`/`findBy*` give up after 1 s by default. That is
// longer than any of these screens needs on a quiet machine and shorter than a
// full parallel run needs on a busy one (PROGRESS issue 59). A failing
// assertion still fails, just after 5 s instead of 1; a passing one is not
// slowed, because `waitFor` returns the moment it holds.
configure({ asyncUtilTimeout: 5_000 });

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  document.documentElement.removeAttribute('data-theme');
  localStorage.clear();
});
