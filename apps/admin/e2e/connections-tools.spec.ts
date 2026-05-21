import { test, expect } from '@playwright/test';

test.describe('Connections — Tools tab CRUD', () => {
  test('Add modal renders the credential field from the catalog', async ({ page }) => {
    await page.goto('/connections');
    await page.getByRole('tab', { name: /tools/i }).click();
    await expect(page.getByText(/no tool connections yet/i)).toBeVisible({ timeout: 10_000 });

    await page.getByRole('button', { name: /add tool connection/i }).click();
    await expect(page.getByText(/choose a tool/i)).toBeVisible();

    // Pick Slack — the credential form should render its declared field
    await page
      .getByRole('combobox')
      .selectOption({ label: 'Slack — Post message (communication)' });
    await expect(page.getByLabel(/bot user oauth token/i)).toBeVisible();
  });

  test('Submitting credentials adds a row to the list', async ({ page }) => {
    await page.goto('/connections');
    await page.getByRole('tab', { name: /tools/i }).click();
    await expect(page.getByText(/no tool connections yet/i)).toBeVisible({ timeout: 10_000 });

    await page.getByRole('button', { name: /add tool connection/i }).click();
    await page
      .getByRole('combobox')
      .selectOption({ label: 'Slack — Post message (communication)' });
    await page.getByLabel(/bot user oauth token/i).fill('xoxb-fake-test-token');
    await page.getByRole('button', { name: /^add$/i }).click();

    await expect(
      page.getByTestId('tool-connections-list'),
    ).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText(/slack — post message/i)).toBeVisible();
  });

  test('Delete button removes the connection row', async ({ page }) => {
    page.on('dialog', (dialog) => dialog.accept());

    await page.goto('/connections');
    await page.getByRole('tab', { name: /tools/i }).click();

    const deleteButtons = page.getByRole('button', { name: /delete/i });
    const initial = await deleteButtons.count();
    if (initial === 0) {
      test.skip();
      return;
    }

    await deleteButtons.first().click();
    await expect(async () => {
      const remaining = await page.getByRole('button', { name: /delete/i }).count();
      expect(remaining).toBeLessThan(initial);
    }).toPass({ timeout: 10_000 });
  });
});
