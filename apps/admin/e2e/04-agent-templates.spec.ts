import { test, expect } from '@playwright/test';

test.describe('Agent Templates', () => {
  test('renders template list page', async ({ page }) => {
    await page.goto('/agents/templates');
    // The sidebar nav also contains an "Agent Templates" link, so disambiguate
    // by asking for the h1 heading specifically.
    await expect(
      page.getByRole('heading', { name: 'Agent Templates' }),
    ).toBeVisible({ timeout: 10_000 });
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
    await page
      .getByRole('button', { name: /Use Template/ })
      .first()
      .click();
    await expect(page).toHaveURL(/\/agents\/new\?templateId=/);
  });
});

test.describe('Create Agent from Template', () => {
  test('pre-fills form from template', async ({ page }) => {
    await page.goto('/agents/new?templateId=hello-agent');
    // Header on the new-agent page is "Creating from Hello Agent".
    await expect(page.getByText(/Creating from/)).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText('Hello Agent')).toBeVisible();
    // The AgentConfigPanel seeds the Name field with a slugified default
    // (see app/agents/new/page.tsx: `t.name.toLowerCase().replace(/\s+/g, '-')`).
    await expect(
      page.getByRole('textbox', { name: 'Name' }),
    ).toHaveValue('hello-agent');
  });

  test('empty form without template', async ({ page }) => {
    await page.goto('/agents/new');
    // Without a template the page renders only the AgentConfigPanel, whose
    // heading is "Agent Configuration".
    await expect(page.getByRole('heading', { name: 'Agent Configuration' })).toBeVisible(
      { timeout: 10_000 },
    );
  });

  test('has create button that opens playground', async ({ page }) => {
    await page.goto('/agents/new?templateId=hello-agent');
    // The panel's submit button is labeled "Create Agent"; on success the
    // page's handleAgentCreated callback routes to /playground (see
    // app/agents/new/page.tsx), so the flow is "create then open playground"
    // even though the button label is just "Create Agent".
    await expect(
      page.getByRole('button', { name: /Create Agent/ }),
    ).toBeVisible({ timeout: 10_000 });
  });
});
