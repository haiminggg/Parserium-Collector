import { expect, test, type Page } from "@playwright/test";
import { resolve } from "node:path";
import AxeBuilder from "@axe-core/playwright";

const timestamp = "2026-08-26T09:14:00Z";

const candidates = [
  ["Annual report 2026", "northbank.example", "pdf", 42],
  ["Investment outlook Q2 2026", "alphaam.example", "docx", 18],
  ["Fund factsheet - May 2026", "northbridge.example", "pdf", 11],
  ["Sustainable investing report 2025", "greenstone.example", "pdf", 27],
  ["Quarterly commentary Q1 2026", "claritycap.example", "docx", 15],
  ["ESG metrics update 2026", "pioneerfunds.example", "pdf", 9],
  ["Portfolio holdings - April 2026", "summitam.example", "pdf", 32],
].map(([title, host, documentType, tableCount], index) => ({
  id: `10000000-0000-4000-8000-${String(index + 1).padStart(12, "0")}`,
  ordinal: index,
  source_url: `https://${host}/documents/report-${index}.${documentType}`,
  title,
  description: `${title} source document`,
  document_type: documentType,
  status: "ready",
  public_state: "valid",
  attempt_count: 1,
  bytes_downloaded: 512_000 + index * 12_000,
  content_length: 512_000 + index * 12_000,
  page_count: 6 + index,
  analyzed_page_count: 6 + index,
  table_count: tableCount,
  table_count_lower_bound: false,
  preview_available: true,
  preview_page_num: 1,
  preview_width: 1275,
  preview_height: 1650,
  error_code: null,
  error_detail: null,
  error_retryable: null,
  tables:
    index === 2
      ? [
          {
            id: "20000000-0000-4000-8000-000000000003",
            page_num: 1,
            table_index: 0,
            bounding_box: { x: 55, y: 210, width: 500, height: 145 },
            cells: [
              ["Fund", "NAV", "Return"],
              ["Northbridge", "$42.1m", "8.4%"],
            ],
            markdown:
              "| Fund | NAV | Return |\n| --- | --- | --- |\n| Northbridge | $42.1m | 8.4% |",
          },
        ]
      : [],
  created_at: timestamp,
  updated_at: timestamp,
  completed_at: timestamp,
}));

const analysisSession = {
  id: "30000000-0000-4000-8000-000000000001",
  query: "investment documents with tables",
  document_types: ["pdf", "docx"],
  tables_required: true,
  firecrawl_connection_id: null,
  firecrawl_connection_name_snapshot: null,
  firecrawl_connection_type_snapshot: null,
  status: "completed",
  job_stage: "completed",
  error_code: null,
  candidate_count: candidates.length,
  bytes_downloaded: candidates.reduce((total, candidate) => total + candidate.bytes_downloaded, 0),
  session_byte_limit: 536_870_912,
  cancellation_requested: false,
  created_at: timestamp,
  updated_at: timestamp,
  expires_at: "2026-08-26T10:14:00Z",
  completed_at: timestamp,
  candidates,
};

const previewSvg = `
  <svg xmlns="http://www.w3.org/2000/svg" width="1275" height="1650" viewBox="0 0 1275 1650">
    <rect width="1275" height="1650" fill="#fff"/>
    <rect x="112" y="116" width="620" height="20" fill="#706c66"/>
    <rect x="112" y="164" width="940" height="8" fill="#c8c4bb"/>
    <rect x="112" y="194" width="820" height="8" fill="#d9d5cc"/>
    <g stroke="#bbb6ac" fill="none" stroke-width="2">
      <rect x="114" y="438" width="1042" height="304"/>
      <path d="M114 500h1042M114 564h1042M114 628h1042M114 692h1042"/>
      <path d="M340 438v304M690 438v304M910 438v304"/>
    </g>
    <g fill="#8d8982">
      <rect x="140" y="463" width="140" height="10"/>
      <rect x="370" y="463" width="180" height="10"/>
      <rect x="720" y="463" width="110" height="10"/>
      <rect x="140" y="529" width="170" height="8"/>
      <rect x="370" y="529" width="145" height="8"/>
    </g>
  </svg>`;

const jobs = [
  {
    id: "00000000-0000-4000-8000-000000000001",
    source_url: "https://northbank.example/documents/report-0.pdf",
    title: "Annual report 2026",
    expected_document_type: "pdf",
    status: "downloading",
    attempt_count: 1,
    available_at: timestamp,
    bytes_downloaded: 696_320,
    content_length: 1_024_000,
    document_id: null,
    error_code: null,
    error_detail: null,
    error_retryable: null,
    created_at: timestamp,
    updated_at: timestamp,
    started_at: timestamp,
    completed_at: null,
  },
  {
    id: "00000000-0000-4000-8000-000000000002",
    source_url: "https://alphaam.example/documents/report-1.docx",
    title: "Investment outlook Q2 2026",
    expected_document_type: "docx",
    status: "queued",
    attempt_count: 0,
    available_at: timestamp,
    bytes_downloaded: 0,
    content_length: null,
    document_id: null,
    error_code: null,
    error_detail: null,
    error_retryable: null,
    created_at: timestamp,
    updated_at: timestamp,
    started_at: null,
    completed_at: null,
  },
  {
    id: "00000000-0000-4000-8000-000000000003",
    source_url: "https://northbridge.example/documents/report-2.pdf",
    title: "Fund factsheet - May 2026",
    expected_document_type: "pdf",
    status: "completed",
    attempt_count: 1,
    available_at: timestamp,
    bytes_downloaded: 512_000,
    content_length: 512_000,
    document_id: "00000000-0000-4000-8000-000000000010",
    error_code: null,
    error_detail: null,
    error_retryable: null,
    created_at: timestamp,
    updated_at: timestamp,
    started_at: timestamp,
    completed_at: timestamp,
  },
];

const documents = [
  {
    id: "00000000-0000-4000-8000-000000000010",
    sha256: "a".repeat(64),
    document_type: "pdf",
    media_type: "application/pdf",
    size_bytes: 512_000,
    safe_filename: "fund-factsheet-may-2026.pdf",
    created_at: timestamp,
  },
  {
    id: "00000000-0000-4000-8000-000000000011",
    sha256: "b".repeat(64),
    document_type: "docx",
    media_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    size_bytes: 338_120,
    safe_filename: "investment-outlook-q2-2026.docx",
    created_at: timestamp,
  },
  {
    id: "00000000-0000-4000-8000-000000000012",
    sha256: "c".repeat(64),
    document_type: "pdf",
    media_type: "application/pdf",
    size_bytes: 840_120,
    safe_filename: "annual-report-2025.pdf",
    created_at: timestamp,
  },
];

async function installDeterministicApiTestDouble(page: Page) {
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    let body: unknown;

    if (path === "/api/v1/session/status") {
      body = {
        status: "authenticated",
        csrf_token: "visual-test-csrf",
        idle_expires_at: "2026-08-26T10:14:00Z",
        authentication_mode: "local",
        user: null,
        workspace: {
          id: "40000000-0000-4000-8000-000000000001",
          name: "Local workspace",
          role: "owner",
        },
        workspaces: [
          {
            id: "40000000-0000-4000-8000-000000000001",
            name: "Local workspace",
            role: "owner",
          },
        ],
      };
    } else if (path === "/api/v1/health/status") {
      body = {
        overall: "ready",
        build_id: "visual-test-build",
        components: [
          { name: "database", status: "available", detail: null, required: true },
          { name: "worker", status: "available", detail: null, required: true },
        ],
      };
    } else if (path === "/api/v1/collection/jobs") {
      body = { items: jobs, total: 38, next_cursor: "visual-next-page" };
    } else if (path === "/api/v1/documents") {
      body = { items: documents, total: 243, next_cursor: "visual-next-page" };
    } else if (path === "/api/v1/exports") {
      body = [];
    } else if (path === "/api/v1/discovery/searches") {
      body = analysisSession;
    } else if (path.startsWith("/api/v1/discovery/searches/")) {
      body = analysisSession;
    } else if (path.startsWith("/api/v1/discovery/analyses/") && path.endsWith("/preview")) {
      await route.fulfill({
        status: 200,
        contentType: "image/svg+xml",
        body: previewSvg,
      });
      return;
    } else {
      throw new Error(`Unhandled visual test request: ${request.method()} ${path}`);
    }

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(body),
    });
  });
}

test("matches the approved desktop dashboard geometry", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "visual-desktop", "Exact reference viewport only.");
  const pageErrors: string[] = [];
  page.on("pageerror", error => pageErrors.push(error.message));
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await installDeterministicApiTestDouble(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Find documents worth keeping." })).toBeVisible();
  await page.waitForFunction(() => [...document.querySelectorAll<HTMLElement>("[data-workspace-enter]")]
    .every(node => !node.style.opacity && !node.style.transform));

  await page.getByLabel("Search query").fill("investment documents with tables");
  await page.getByRole("button", { name: "Discover", exact: true }).last().click();
  await expect(page.getByRole("region", { name: "Discovered documents" })).toBeVisible();
  await page.getByRole("checkbox", { name: "Select Annual report 2026" }).check();
  await page.getByRole("checkbox", { name: "Select Investment outlook Q2 2026" }).check();
  await page.getByRole("checkbox", { name: "Select Fund factsheet - May 2026" }).check();
  await page.getByRole("button", { name: "Show details for Fund factsheet - May 2026" }).click();
  await expect(
    page.getByRole("img", { name: "Page 1 preview for Fund factsheet - May 2026" }),
  ).toBeVisible();
  await page.evaluate(() => document.fonts.ready);

  const header = await page.locator(".parserium-header").boundingBox();
  const workspace = await page.locator(".operations-grid").boundingBox();
  const hero = await page.locator(".discovery-hero").boundingBox();
  const command = await page.locator(".discovery-command-frame").boundingBox();
  const results = await page.locator(".discovery-results").boundingBox();
  const queue = await page.locator(".queue-panel").boundingBox();
  const utility = await page.locator(".documents-utility").boundingBox();

  expect(header?.height).toBeCloseTo(80, 0);
  expect(workspace?.x).toBeCloseTo(0, 0);
  expect(hero?.x).toBeCloseTo(32, 0);
  expect(hero?.height).toBeCloseTo(140, 0);
  expect(command?.height).toBeGreaterThan(120);
  expect(results?.height).toBeGreaterThanOrEqual(448);
  expect(await page.locator('.workspace-shell').evaluate(node => node.scrollHeight > node.clientHeight)).toBe(true);
  await page.locator('.workspace-shell').evaluate(node => { node.scrollTop = node.scrollHeight; });
  await expect(page.locator('.documents-utility')).toBeInViewport();
  await page.locator('.workspace-shell').evaluate(node => { node.scrollTop = 0; });
  expect((queue?.x ?? 0) - ((results?.x ?? 0) + (results?.width ?? 0))).toBeCloseTo(16, 0);
  expect(utility?.height).toBeCloseTo(66, 0);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
    ),
  ).toBe(true);

  expect((await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze()).violations).toEqual([]);
  expect(pageErrors).toEqual([]);

  await page.screenshot({
    path: resolve(process.cwd(), "../../.local/dashboard-current.png"),
    fullPage: false,
    animations: "disabled",
    caret: "hide",
  });
});

test("keeps Collect usable on narrow screens", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "visual-desktop", "Runs explicit viewport checks.");
  await page.emulateMedia({ reducedMotion: "reduce" });
  // Reuse the existing deterministic API test double. These are not live documents.
  await installDeterministicApiTestDouble(page);
  for (const width of [1366, 768, 390, 320]) {
    await page.setViewportSize({ width, height: width >= 1024 ? 768 : 844 });
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Find documents worth keeping." })).toBeVisible();
    expect(await page.locator("[data-workspace-enter]").evaluate(node => {
      const element = node as HTMLElement;
      return {
        reduced: matchMedia("(prefers-reduced-motion: reduce)").matches,
        transform: element.style.transform,
        opacity: element.style.opacity,
      };
    })).toEqual({ reduced: true, transform: "", opacity: "" });
    await expect(page.getByRole("button", { name: "Collect", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Activity", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Play demo", exact: true }).click();
    await expect(page.getByRole("button", { name: "Replay demo", exact: true })).toBeEnabled();
    expect(await page.locator(".dm-output").evaluate(node => getComputedStyle(node).opacity)).toBe("1");
    expect(await page.locator(".workspace-shell").evaluate(node => getComputedStyle(node).scrollbarWidth)).toBe("none");
    expect(await page.getByRole("button", { name: "Health", exact: true }).evaluate(node => getComputedStyle(node, "::after").content)).toBe("none");
    expect(await page.evaluate(() => {
      const shell = document.querySelector(".workspace-shell")!;
      const command = document.querySelector(".discovery-command-frame")!;
      return shell.scrollWidth <= innerWidth + 1 && command.getBoundingClientRect().right <= innerWidth;
    })).toBe(true);
    await page.getByRole("button", { name: "Collection settings" }).click();
    await page.getByLabel("Include domains").fill("example.org");
    await page.getByRole("button", { name: "Close filters" }).click();
    await expect(page.getByRole("dialog", { name: "Discovery filters" })).toBeHidden();
    await expect(page.getByText(/Included sources/)).toBeVisible();
    await page.locator(".workspace-shell").evaluate(node => node.scrollTop = 0);
    expect((await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze()).violations).toEqual([]);
    await page.screenshot({ path: resolve(process.cwd(), `../../.local/collect-${width}.png`), animations: "disabled" });
  }
});
