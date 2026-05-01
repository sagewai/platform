import { expect, test } from "@playwright/test";

test("/sealed/directives/approvals mounts with empty-state", async ({ page }) => {
  await page.goto("/sealed/directives/approvals");
  await expect(
    page.getByRole("heading", { name: /Pending directive approvals/i }),
  ).toBeVisible();
});
