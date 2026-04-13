import { test, expect } from '@playwright/test';

test.describe('Agent Templates', () => {
  test('renders template list page', async ({ page }) => {
    await page.goto('/agents/templates');
    await expect(page.getByText('Agent Templates')).toBeVisible({ timeout: 10_000 });
  });

  test('shows at least one template card', async ({ page }) => {
    await page.goto('/agents/templates');
    await expect(page.getByText('Hello Agent')).toBeVisible({ timeout: 10_000 });
  });

  test('shows ReAct strategy label', async ({ page }) => {
    await page.goto('/agents/templates');
    await expect(page.getByText('ReAct').first()).toBeVisible({ timeout: 10_000 });
  });

  test('Use Template navigates to /agents/new', async ({ page }) => {
    await page.goto('/agents/templates');
    await page.getByText('Use Template').first().click();
    await expect(page).toHaveURL(/\/agents\/new\?templateId=/);
  });
});

test.describe('Create Agent from Template', () => {
  test('pre-fills form from template', async ({ page }) => {
    await page.goto('/agents/new?templateId=hello-agent');
    await expect(page.getByText('Create from: Hello Agent')).toBeVisible({ timeout: 10_000 });
    await expect(page.locator('#agent-name')).toHaveValue('Hello Agent');
  });

  test('empty form without template', async ({ page }) => {
    await page.goto('/agents/new');
    await expect(page.getByText('Create New Agent')).toBeVisible({ timeout: 10_000 });
  });

  test('has Create & Open in Playground button', async ({ page }) => {
    await page.goto('/agents/new?templateId=hello-agent');
    await expect(page.getByRole('button', { name: /Create.*Playground/ })).toBeVisible({ timeout: 10_000 });
  });
});
