import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

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
  for (const path of PAGES) {
    test(`a11y: ${theme} ${path} — zero WCAG AA violations`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: theme });
      await page.goto(path);
      await page.waitForLoadState('networkidle');

      // Scope to the page content area only. The sidebar has a pre-existing
      // contrast ratio of ~4.43:1 on its muted foreground tokens (tracked in
      // a11y-exceptions.md) and is outside Plan P scope.
      const results = await new AxeBuilder({ page })
        .include('main')
        .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
        .analyze();

      expect(
        results.violations,
        `${theme} ${path} violations:\n${JSON.stringify(results.violations, null, 2)}`,
      ).toEqual([]);
    });
  }
}
