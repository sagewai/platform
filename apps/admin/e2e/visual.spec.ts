import { test, expect } from '@playwright/test';

import {
  mockCoordinatorApi,
  PAGES,
  selectProject,
  waitForPageReady,
  workPending,
  workRecord,
} from './coordinator-mocks';

const VIEWPORTS = [
  { name: 'sm', width: 375, height: 812 },
  { name: 'md', width: 768, height: 1024 },
  { name: 'lg', width: 1280, height: 800 },
] as const;

for (const theme of ['light', 'dark'] as const) {
  for (const vp of VIEWPORTS) {
    for (const path of PAGES) {
      test(`visual: ${theme} ${vp.name} ${path}`, async ({ page }) => {
        await selectProject(page);
        await mockCoordinatorApi(page, {
          '/api/v1/work': () => [workRecord],
          '/api/v1/work/pending': () => [workPending],
        });

        await page.setViewportSize({ width: vp.width, height: vp.height });
        await page.emulateMedia({ colorScheme: theme, reducedMotion: 'reduce' });
        await page.goto(path);
        await waitForPageReady(page, path);

        // Freeze animations + cursor blink for deterministic snapshots.
        await page.addStyleTag({
          content:
            '*, *::before, *::after { animation: none !important; transition: none !important; caret-color: transparent !important; }',
        });

        await expect(page).toHaveScreenshot(`${theme}-${vp.name}-${path}.png`, {
          fullPage: true,
          maxDiffPixelRatio: 0.01,
        });
      });
    }
  }
}
