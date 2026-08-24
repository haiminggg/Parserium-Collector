import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

test("health page is responsive and has no detectable accessibility violations", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "System health" })).toBeVisible();
  await expect(page.locator('[aria-busy="false"]')).toBeVisible();
  await expect(page.getByText("database", { exact: true })).toBeVisible();
  await expect(page.getByText("worker", { exact: true })).toBeVisible();

  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();

  expect(results.violations).toEqual([]);
});
