import { test, expect } from '@playwright/test';

const VIEWPORTS = [
  { name: 'sm', width: 375, height: 812 },
  { name: 'md', width: 768, height: 1024 },
  { name: 'lg', width: 1280, height: 800 },
] as const;

const PAGES = [
  '/autopilot',
  '/autopilot/missions',
  '/autopilot/missions/new',
  '/autopilot/blueprints/preview',
  '/autopilot/blueprints/picker',
  '/autopilot/blueprints/graph',
  '/autopilot/cost',
  '/autopilot/settings',
] as const;

for (const theme of ['light', 'dark'] as const) {
  for (const vp of VIEWPORTS) {
    for (const path of PAGES) {
      test(`visual: ${theme} ${vp.name} ${path}`, async ({ page }) => {
        // Mock autopilot API endpoints with fixtures so snapshots are deterministic.
        await page.route('**/api/v1/autopilot/**', async (route) => {
          const url = route.request().url();
          const last = url.split('/').pop()?.split('?')[0] ?? 'unknown';
          try {
            await route.fulfill({ path: `e2e/fixtures/autopilot-${last}.json` });
          } catch {
            await route.fulfill({
              status: 200,
              contentType: 'application/json',
              body: JSON.stringify({ missions: [], workers: [], steps: [] }),
            });
          }
        });

        await page.setViewportSize({ width: vp.width, height: vp.height });
        await page.emulateMedia({ colorScheme: theme, reducedMotion: 'reduce' });
        await page.goto(path);
        await page.waitForLoadState('networkidle');

        // Freeze animations + cursor blink for deterministic snapshots.
        await page.addStyleTag({
          content:
            '*, *::before, *::after { animation: none !important; transition: none !important; caret-color: transparent !important; }',
        });

        await expect(page).toHaveScreenshot(
          `${theme}-${vp.name}-${path.replace(/\//g, '_')}.png`,
          { fullPage: true, maxDiffPixelRatio: 0.01 },
        );
      });
    }
  }
}
