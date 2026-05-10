import { expect, test } from "@playwright/test";

test("/sealed/directives mounts with header", async ({ page }) => {
  // Mock the directives API so the page renders the heading even with a fresh backend.
  await page.route('**/api/v1/admin/directives/policies', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        system_policies: [],
        project_policies: {},
        workflow_policies: {},
      }),
    }),
  );
  await page.goto("/sealed/directives");
  await expect(
    page.getByRole("heading", { name: /Directive policies/i }),
  ).toBeVisible();
});
