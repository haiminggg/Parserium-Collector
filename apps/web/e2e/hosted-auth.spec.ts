import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { readFile } from "node:fs/promises";

interface AnalysisCandidateSummary {
  id: string;
}

interface AnalysisSummary {
  id: string;
  candidates: AnalysisCandidateSummary[];
}

interface CollectionPage {
  items: Array<{ id: string }>;
  total: number;
}

interface DocumentPage {
  items: Array<{ id: string; safe_filename: string }>;
  total: number;
}

interface FirecrawlConnectionSummary {
  id: string;
  name: string;
  status: string;
}

async function openConnections(page: Page) {
  await page.getByRole("button", { name: "Connections", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Firecrawl connections" }),
  ).toBeVisible();
}

async function createRemoteConnection(
  page: Page,
  name: string,
  credential: string,
): Promise<FirecrawlConnectionSummary> {
  const responsePromise = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/v1/firecrawl/connections") &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Add connection" }).click();
  const editor = page.getByRole("dialog", { name: "Add Firecrawl connection" });
  await expect
    .poll(async () => {
      const viewport = page.viewportSize();
      const bounds = await editor.boundingBox();
      return (
        viewport !== null &&
        bounds !== null &&
        bounds.x >= 0 &&
        bounds.x + bounds.width <= viewport.width + 1
      );
    })
    .toBe(true);
  await editor.getByLabel(/^Name/).fill(name);
  const remoteChoice = editor.getByRole("radio", { name: "Remote" });
  await editor.getByText("Remote", { exact: true }).click();
  await expect(remoteChoice).toBeChecked();
  await editor.getByLabel(/^Remote HTTPS origin/).fill("https://test-firecrawl:9444");
  await editor.getByLabel(/^API token/).fill(credential);
  const defaultChoice = editor.getByRole("switch", { name: "Make default" });
  await defaultChoice.click();
  await expect(defaultChoice).toBeChecked();
  await editor.getByRole("button", { name: "Save connection" }).click();

  const response = await responsePromise;
  expect(response.status()).toBe(201);
  const connection = (await response.json()) as FirecrawlConnectionSummary;
  expect(connection.status).toBe("healthy");
  await expect(editor).not.toBeVisible();
  const row = page.getByRole("article", { name });
  await expect(row.getByText("Healthy", { exact: true })).toBeVisible();
  return connection;
}

async function expectCredentialAbsentFromBrowser(page: Page, credential: string) {
  const persisted = await page.evaluate((candidate) => {
    const storageValues = [localStorage, sessionStorage].flatMap((storage) =>
      Array.from({ length: storage.length }, (_, index) => {
        const key = storage.key(index) ?? "";
        return `${key}:${storage.getItem(key) ?? ""}`;
      }),
    );
    const inputValues = Array.from(document.querySelectorAll("input"), (input) =>
      input.value,
    );
    return [...storageValues, ...inputValues, document.body.innerText].some((value) =>
      value.includes(candidate),
    );
  }, credential);
  expect(persisted).toBe(false);
}

async function signInWithInvitation(
  page: Page,
  invitationUrl: string,
  identityButton: string,
) {
  await page.goto(invitationUrl);
  await expect(page.getByRole("heading", { name: "Welcome to Parserium" })).toBeVisible();
  await page.getByRole("button", { name: "Continue with Google" }).click();
  await expect(
    page.getByRole("heading", { name: "Choose a verification identity" }),
  ).toBeVisible();
  await page.getByRole("button", { name: identityButton }).click();
  await expect(
    page.getByRole("heading", { name: "Find documents worth keeping." }),
  ).toBeVisible();
}

async function openStoredDocuments(page: Page) {
  await page.getByRole("button", { name: "Documents", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Stored documents" })).toBeVisible();
}

test("hosted invitations isolate each workspace", async ({ page }, testInfo) => {
  test.skip(
    !["hosted-chromium", "hosted-mobile-chromium"].includes(testInfo.project.name),
  );
  test.setTimeout(240_000);
  const invitationA = process.env.PLAYWRIGHT_INVITE_A;
  const invitationB = process.env.PLAYWRIGHT_INVITE_B;
  const credentialFile = process.env.PLAYWRIGHT_FIRECRAWL_TOKEN_FILE;
  const workspaceA = process.env.PLAYWRIGHT_WORKSPACE_A ?? "Workspace A";
  const workspaceB = process.env.PLAYWRIGHT_WORKSPACE_B ?? "Workspace B";
  if (!invitationA || !invitationB || !credentialFile) {
    throw new Error(
      "Hosted invitation URLs and the Firecrawl token file are required.",
    );
  }
  const firecrawlCredential = (await readFile(credentialFile, "utf-8")).trim();
  expect(firecrawlCredential.length).toBeGreaterThan(20);

  await page.goto(invitationA);
  await expect(page.getByRole("heading", { name: "Welcome to Parserium" })).toBeVisible();
  const loginAccessibility = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();
  expect(loginAccessibility.violations).toEqual([]);

  await page.getByRole("button", { name: "Continue with Google" }).click();
  await page
    .getByRole("button", { name: "Continue as Workspace A owner" })
    .click();
  await expect(
    page.getByRole("heading", { name: "Find documents worth keeping." }),
  ).toBeVisible();
  await expect(page.getByText(workspaceA, { exact: true })).toBeVisible();

  await openConnections(page);
  const connectionA = await createRemoteConnection(
    page,
    "Workspace A remote",
    firecrawlCredential,
  );
  await expect(page.getByRole("article")).toHaveCount(1);
  await page.getByRole("button", { name: "Discover", exact: true }).click();

  const searchResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/v1/discovery/searches") &&
      response.request().method() === "POST",
  );
  await page.getByLabel("Search query").fill("deterministic investment table report");
  await page.getByRole("button", { name: "Discover", exact: true }).last().click();
  const startedResponse = await searchResponse;
  expect(startedResponse.request().postDataJSON()).toMatchObject({
    firecrawl_connection_id: connectionA.id,
  });
  const startedAnalysis = (await startedResponse.json()) as AnalysisSummary;
  const validSelect = page.getByRole("checkbox", {
    name: "Select Deterministic investment table",
  });
  await expect(validSelect).toBeEnabled({ timeout: 120_000 });

  const analysisResponse = await page.request.get(
    `/api/v1/discovery/searches/${startedAnalysis.id}`,
  );
  expect(analysisResponse.status()).toBe(200);
  const analysisA = (await analysisResponse.json()) as AnalysisSummary;
  expect(analysisA.candidates.length).toBeGreaterThan(0);
  const candidateAId = analysisA.candidates[0].id;

  await validSelect.check();
  await page.getByRole("button", { name: "Collect selected" }).click();
  if (testInfo.project.name === "hosted-mobile-chromium") {
    await page.getByRole("button", { name: "Queue", exact: true }).click();
    await expect(page.getByRole("dialog", { name: "Collection activity" })).toBeVisible();
  }
  const completedJob = page
    .getByRole("article", { name: "Collection job Deterministic investment table" })
    .first();
  await expect(completedJob.getByText(/^(Stored|Already stored)$/)).toBeVisible({
    timeout: 60_000,
  });
  if (testInfo.project.name === "hosted-mobile-chromium") {
    const queueDrawer = page.getByRole("dialog", { name: "Collection activity" });
    await queueDrawer.getByRole("button", { name: "Close collection queue" }).click();
    await expect(queueDrawer).not.toBeVisible();
  }

  const jobsAResponse = await page.request.get("/api/v1/collection/jobs?limit=50");
  const jobsA = (await jobsAResponse.json()) as CollectionPage;
  expect(jobsA.total).toBeGreaterThan(0);
  const jobAId = jobsA.items[0].id;

  await openStoredDocuments(page);
  const storedDocument = page
    .getByRole("article", { name: "Stored document Deterministic investment table.pdf" })
    .first();
  await expect(storedDocument).toBeVisible({ timeout: 60_000 });
  await storedDocument
    .getByLabel("Export subfolder for Deterministic investment table.pdf")
    .fill("hosted/workspace-a");
  const exportResponse = page.waitForResponse(
    (response) => {
      const pathname = new URL(response.url()).pathname;
      return (
        pathname.startsWith("/api/v1/documents/") &&
        pathname.endsWith("/exports") &&
        response.request().method() === "POST"
      );
    },
  );
  await storedDocument
    .getByRole("button", { name: "Export Deterministic investment table.pdf" })
    .click();
  expect((await exportResponse).status()).toBe(503);
  await expect(page.getByText("The document export could not be queued.")).toBeVisible();

  const documentsAResponse = await page.request.get("/api/v1/documents?limit=50");
  const documentsA = (await documentsAResponse.json()) as DocumentPage;
  expect(documentsA.total).toBeGreaterThan(0);
  const documentAId = documentsA.items[0].id;
  const exportsAResponse = await page.request.get("/api/v1/exports?limit=50");
  expect((await exportsAResponse.json()) as unknown[]).toEqual([]);

  const dashboardAccessibility = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();
  expect(dashboardAccessibility.violations).toEqual([]);

  await page.getByRole("button", { name: "Close document library" }).click();
  await openConnections(page);
  const connectionARow = page.getByRole("article", { name: "Workspace A remote" });
  await connectionARow.getByRole("button", { name: "Rotate token" }).click();
  const rotation = page.getByRole("dialog", { name: "Rotate API token" });
  await rotation.getByLabel(/^New API token/).fill(firecrawlCredential);
  await rotation.getByRole("button", { name: "Replace token" }).click();
  await expect(rotation).not.toBeVisible();
  await expect(connectionARow.getByText("Healthy", { exact: true })).toBeVisible();

  await connectionARow.getByRole("button", { name: "Disable connection" }).click();
  await expect(connectionARow.getByText("Disabled", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Discover", exact: true }).click();
  await expect(page.getByText("No usable connection", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Discover", exact: true }).last(),
  ).toBeDisabled();
  await page.getByRole("button", { name: "Configure connections" }).click();
  await connectionARow.getByRole("button", { name: "Enable connection" }).click();
  await expect(connectionARow.getByText("Healthy", { exact: true })).toBeVisible();
  const testConnectionResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith(`/api/v1/firecrawl/connections/${connectionA.id}/test`) &&
      response.request().method() === "POST",
  );
  await connectionARow.getByRole("button", { name: "Test connection" }).click();
  expect((await testConnectionResponse).status()).toBe(200);
  await expect(connectionARow.getByText("Healthy", { exact: true })).toBeVisible();
  await expectCredentialAbsentFromBrowser(page, firecrawlCredential);

  await page.getByRole("button", { name: "Health", exact: true }).click();
  await page
    .getByRole("dialog", { name: "System health" })
    .getByRole("button", { name: "Log out" })
    .click();
  await expect(page.getByRole("heading", { name: "Welcome to Parserium" })).toBeVisible();

  await signInWithInvitation(page, invitationB, "Continue as Workspace B owner");
  await expect(page.getByText(workspaceB, { exact: true })).toBeVisible();

  await openConnections(page);
  await expect(page.getByRole("article", { name: "Workspace A remote" })).toHaveCount(0);
  const connectionB = await createRemoteConnection(
    page,
    "Workspace B remote",
    firecrawlCredential,
  );
  await expect(page.getByRole("article")).toHaveCount(1);

  const jobsB = (await (
    await page.request.get("/api/v1/collection/jobs?limit=50")
  ).json()) as CollectionPage;
  const documentsB = (await (
    await page.request.get("/api/v1/documents?limit=50")
  ).json()) as DocumentPage;
  const exportsB = (await (
    await page.request.get("/api/v1/exports?limit=50")
  ).json()) as unknown[];
  expect(jobsB.total).toBe(0);
  expect(documentsB.total).toBe(0);
  expect(exportsB).toEqual([]);

  expect(
    (await page.request.get(`/api/v1/discovery/searches/${analysisA.id}`)).status(),
  ).toBe(404);
  expect(
    (
      await page.request.get(
        `/api/v1/discovery/analyses/${candidateAId}/preview`,
      )
    ).status(),
  ).toBe(404);
  expect(
    (await page.request.get(`/api/v1/documents/${documentAId}/download`)).status(),
  ).toBe(404);

  const sessionResponse = await page.request.get("/api/v1/session");
  expect(sessionResponse.status()).toBe(200);
  const sessionB = (await sessionResponse.json()) as {
    csrf_token: string;
    status: string;
  };
  expect(sessionB.status).toBe("authenticated");
  expect(typeof sessionB.csrf_token).toBe("string");
  const crossWorkspaceRetryStatus = await page.evaluate(
    async ({ csrfToken, jobId }) =>
      (
        await fetch(`/api/v1/collection/jobs/${jobId}/retry`, {
          method: "POST",
          headers: { "X-Parserium-CSRF": csrfToken },
        })
      ).status,
    { csrfToken: sessionB.csrf_token, jobId: jobAId },
  );
  expect(crossWorkspaceRetryStatus).toBe(404);

  const crossWorkspaceConnectionStatus = await page.evaluate(
    async ({ connectionId, csrfToken }) =>
      (
        await fetch(`/api/v1/firecrawl/connections/${connectionId}/test`, {
          method: "POST",
          headers: { "X-Parserium-CSRF": csrfToken },
        })
      ).status,
    { connectionId: connectionA.id, csrfToken: sessionB.csrf_token },
  );
  expect(crossWorkspaceConnectionStatus).toBe(404);

  const connectionBRow = page.getByRole("article", { name: "Workspace B remote" });
  await connectionBRow.getByRole("button", { name: "Delete connection" }).click();
  const deleteConfirmation = page.getByRole("dialog", {
    name: "Delete Firecrawl connection?",
  });
  await deleteConfirmation.getByRole("button", { name: "Delete connection" }).click();
  await expect(page.getByRole("article", { name: "Workspace B remote" })).toHaveCount(0);
  expect(connectionB.id).not.toBe(connectionA.id);
  await expectCredentialAbsentFromBrowser(page, firecrawlCredential);
});
