import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

async function openCompactQueue(page: Page) {
  const trigger = page.getByRole("button", { name: /^Open collection queue/ });
  if ((await trigger.count()) > 0 && (await trigger.isVisible())) {
    await trigger.click();
  }
}

async function closeCompactQueue(page: Page) {
  const closeButton = page.getByRole("button", { name: "Close collection queue" });
  if ((await closeButton.count()) > 0 && (await closeButton.isVisible())) {
    await closeButton.click();
  }
}

async function openStoredDocuments(page: Page) {
  await page.getByRole("button", { name: "Documents", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Stored documents" })).toBeVisible();
}

async function closeStoredDocuments(page: Page) {
  await page.getByRole("button", { name: "Close document library" }).click();
  await expect(page.getByRole("heading", { name: "Stored documents" })).toBeHidden();
}

test("pairs, analyzes, collects, downloads, exports, rejects, and logs out", async ({
  page,
}, testInfo) => {
  test.setTimeout(180_000);
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

  await expect(
    page.getByRole("heading", { name: "Find documents worth keeping." }),
  ).toBeVisible();
  const systemReady = page.getByText("System ready", { exact: true });
  if ((page.viewportSize()?.width ?? 0) < 1024) {
    await expect(systemReady).toBeHidden();
  } else {
    await expect(systemReady).toBeVisible();
  }
  await expect(
    page.getByRole("main", { name: "Document discovery console" }),
  ).toBeVisible();
  await expect(page.getByRole("region", { name: "Document discovery" })).toHaveAttribute(
    "aria-busy",
    "false",
  );

  await page.getByRole("button", { name: "Health", exact: true }).click();
  const healthDialog = page.getByRole("dialog", { name: "System health" });
  await expect(healthDialog).toBeVisible();
  await expect(healthDialog.getByText("database", { exact: true })).toBeVisible();
  await expect(healthDialog.getByText("worker", { exact: true })).toBeVisible();
  await healthDialog.getByRole("button", { name: "Close system health" }).click();

  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
    ),
  ).toBe(true);

  // Each browser project searches separately. An identical query would reuse the previous
  // project's cached discovery, whose candidate it already collected.
  await page
    .getByLabel("Search query")
    .fill(`deterministic investment table report ${testInfo.project.name}`);
  await page.getByRole("button", { name: "Discover", exact: true }).last().click();
  const results = page.getByRole("region", { name: "Discovered documents" });
  await expect(results).toBeVisible({ timeout: 60_000 });
  await expect(page.getByRole("columnheader", { name: "Document title" })).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "Source" })).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "Type" })).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "Tables" })).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "Validation" })).toBeVisible();

  const validSelect = page.getByRole("checkbox", {
    name: "Select Deterministic investment table",
  });
  await expect(validSelect).toBeEnabled({ timeout: 120_000 });
  const validRow = page
    .getByRole("link", { name: "Deterministic investment table" })
    .locator("xpath=ancestor::*[@role='row']");
  await expect(validRow.getByText("Valid", { exact: true })).toBeVisible();
  await expect(validRow.getByText("1", { exact: true })).toBeVisible();

  await validSelect.check();
  await page.getByRole("button", { name: "Collect selected" }).click();
  await openCompactQueue(page);

  const queue = page
    .getByRole("heading", { name: "Collection queue" })
    .locator("xpath=ancestor::section");
  const completedJob = queue
    .getByRole("article", { name: "Collection job Deterministic investment table" })
    .first();
  await expect(completedJob.getByText(/^(Stored|Already stored)$/)).toBeVisible({
    timeout: 60_000,
  });
  await closeCompactQueue(page);

  await openStoredDocuments(page);
  const storedDocument = page
    .getByRole("article", { name: "Stored document Deterministic investment table.pdf" })
    .first();
  await expect(storedDocument).toBeVisible({ timeout: 60_000 });
  await expect(
    storedDocument.getByRole("link", {
      name: "Download Deterministic investment table.pdf",
    }),
  ).toHaveAttribute("href", /\/api\/v1\/documents\/.+\/download$/);

  const exportSubfolder = `e2e/${testInfo.project.name}`;
  await storedDocument
    .getByLabel("Export subfolder for Deterministic investment table.pdf")
    .fill(exportSubfolder);
  await storedDocument
    .getByRole("button", { name: "Export Deterministic investment table.pdf" })
    .click();

  const completedExport = page
    .getByRole("group", { name: "Document export Deterministic investment table.pdf" })
    .first();
  await expect(completedExport.getByText("Completed", { exact: true })).toBeVisible({
    timeout: 60_000,
  });
  await expect(completedExport.getByText(new RegExp(`^${exportSubfolder}/`))).toBeVisible();
  await closeStoredDocuments(page);

  const blockedRow = page
    .getByRole("link", { name: "Blocked investment table" })
    .locator("xpath=ancestor::*[@role='row']");
  await expect(blockedRow.getByText("Failed", { exact: true })).toBeVisible({
    timeout: 120_000,
  });
  await expect(
    page.getByRole("checkbox", { name: "Select Blocked investment table" }),
  ).toBeDisabled();
  await page
    .getByRole("button", { name: "Show details for Blocked investment table" })
    .click();
  await expect(page.getByText("The document address is not permitted.")).toBeVisible();

  const afterPairing = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();
  expect(afterPairing.violations).toEqual([]);

  await page.getByRole("button", { name: "Health", exact: true }).click();
  await healthDialog.getByRole("button", { name: "Log out" }).click();
  await expect(page.getByRole("heading", { name: "Pair this browser" })).toBeVisible();
});
