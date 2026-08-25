import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

test("pairing protects the responsive health page and logout revokes access", async ({ page }) => {
  const pairingCode = process.env.PLAYWRIGHT_PAIRING_CODE;
  if (!pairingCode) throw new Error("PLAYWRIGHT_PAIRING_CODE is required.");

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Pair this browser" })).toBeVisible();

  const beforePairing = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();
  expect(beforePairing.violations).toEqual([]);

  await page.getByLabel("Pairing code").fill(pairingCode);
  await page.getByRole("button", { name: "Pair browser" }).click();

  await expect(page.getByRole("heading", { name: "System health" })).toBeVisible();
  await expect(page.locator('[aria-busy="false"]')).toBeVisible();
  await expect(page.getByText("database", { exact: true })).toBeVisible();
  await expect(page.getByText("worker", { exact: true })).toBeVisible();

  await page.getByLabel(/Search query/).fill("site:example.com");
  await page.getByRole("checkbox", { name: "DOCX" }).uncheck();
  await page.getByRole("button", { name: "Search documents" }).click();
  await expect(page.getByText(/direct document links found/)).toBeVisible({ timeout: 60_000 });

  const afterPairing = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();
  expect(afterPairing.violations).toEqual([]);

  await page.getByRole("button", { name: "Log out" }).click();
  await expect(page.getByRole("heading", { name: "Pair this browser" })).toBeVisible();
});
