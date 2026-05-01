import { expect, test } from "@playwright/test";

test("/sealed/directives/evaluations mounts", async ({ page }) => {
  await page.goto("/sealed/directives/evaluations");
  await expect(
    page.getByRole("heading", { name: /Directive evaluations/i }),
  ).toBeVisible();
});
