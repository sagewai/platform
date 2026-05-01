import { expect, test } from "@playwright/test";

test("/sealed/directives mounts with header", async ({ page }) => {
  await page.goto("/sealed/directives");
  await expect(
    page.getByRole("heading", { name: /Directive policies/i }),
  ).toBeVisible();
});
