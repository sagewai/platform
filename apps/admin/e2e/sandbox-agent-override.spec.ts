import { test, expect } from '@playwright/test';

/**
 * Plan 3b-i — Task 15: E2E specs for the Sandbox agent override tab.
 *
 * The agent detail page (/agents/[name]) renders a "Sandbox" tab between
 * the "Config" and "Memory" tabs. Exercising this surface requires:
 *   1. A live backend with an agent registered (name is not predictable
 *      without a seed API).
 *   2. Admin auth (storageState fixture from auth.setup.ts).
 *
 * Because no test-fixture API exists in Plan 3b-i, all three tests are
 * gated. They will be unblocked in Plan 3b-ii once the fixture API lands
 * and can seed a known agent + optional project default.
 */
test.describe('Sandbox agent override tab', () => {
  test('Sandbox tab is in the agent navigation', async ({ page }) => {
    test.skip(
      true,
      'Requires authenticated agent route + seeded agent; gated until Plan 3b-ii fixture API',
    );
  });

  test('toggle override checkbox shows form', async ({ page }) => {
    test.skip(
      true,
      'Requires authenticated agent route + seeded agent; gated until Plan 3b-ii',
    );
  });

  test('cascade preview shows from-blueprint or from-project-default origin', async ({ page }) => {
    test.skip(
      true,
      'Requires admin auth + seeded blueprint or project default; gated until Plan 3b-ii',
    );
  });
});
