import { MantineProvider } from "@mantine/core";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DiscoveryResults } from "./DiscoveryResults";
import type { AnalysisCandidate } from "./api/discovery";

afterEach(cleanup);

const candidates: AnalysisCandidate[] = [
  {
    id: "10000000-0000-4000-8000-000000000001",
    ordinal: 0,
    source_url: "https://bank.example/annual-report.pdf",
    title: "Annual report 2026",
    description: "Audited investment tables",
    document_type: "pdf",
    status: "ready",
    public_state: "valid",
    attempt_count: 1,
    bytes_downloaded: 1024,
    content_length: 1024,
    page_count: 6,
    analyzed_page_count: 6,
    table_count: 42,
    table_count_lower_bound: false,
    preview_available: true,
    preview_page_num: 2,
    preview_width: 1275,
    preview_height: 1650,
    error_code: null,
    error_detail: null,
    error_retryable: null,
    tables: [
      {
        id: "20000000-0000-4000-8000-000000000001",
        page_num: 2,
        table_index: 0,
        bounding_box: { x: 60, y: 200, width: 480, height: 120 },
        cells: [
          ["Fund", "NAV"],
          ["Alpha", "$42"],
        ],
        markdown: "| Fund | NAV |\n| --- | --- |\n| Alpha | $42 |",
      },
    ],
    created_at: "2026-08-27T12:00:00Z",
    updated_at: "2026-08-27T12:00:02Z",
    completed_at: "2026-08-27T12:00:02Z",
  },
  {
    id: "10000000-0000-4000-8000-000000000002",
    ordinal: 1,
    source_url: "https://fund.example/outlook.docx",
    title: "Investment outlook",
    description: null,
    document_type: "docx",
    status: "parsing",
    public_state: null,
    attempt_count: 1,
    bytes_downloaded: 2048,
    content_length: 2048,
    page_count: null,
    analyzed_page_count: 0,
    table_count: 0,
    table_count_lower_bound: false,
    preview_available: false,
    preview_page_num: null,
    preview_width: null,
    preview_height: null,
    error_code: null,
    error_detail: null,
    error_retryable: null,
    tables: [],
    created_at: "2026-08-27T12:00:00Z",
    updated_at: "2026-08-27T12:00:01Z",
    completed_at: null,
  },
];

const failedCandidate = {
  ...candidates[0],
  id: "10000000-0000-4000-8000-000000000003",
  status: "failed",
  public_state: "failed",
  table_count: 0,
  tables: [],
  preview_available: false,
  completed_at: "2026-08-27T12:00:03Z",
} satisfies AnalysisCandidate;

function renderResults(
  overrides: Partial<React.ComponentProps<typeof DiscoveryResults>> = {},
) {
  const props: React.ComponentProps<typeof DiscoveryResults> = {
    candidates,
    selectedIds: [],
    collecting: false,
    tablesRequired: true,
    onToggle: vi.fn(),
    onSelectAll: vi.fn(),
    onClear: vi.fn(),
    onCollect: vi.fn(),
    ...overrides,
  };
  render(
    <MantineProvider>
      <DiscoveryResults {...props} />
    </MantineProvider>,
  );
  return props;
}

describe("DiscoveryResults", () => {
  it("renders real table counts and analysis validation states", async () => {
    renderResults();

    expect(screen.getByRole("columnheader", { name: "Document title" })).toBeVisible();
    expect(screen.getByRole("columnheader", { name: "Source" })).toBeVisible();
    expect(screen.getByRole("columnheader", { name: "Type" })).toBeVisible();
    expect(screen.getByRole("columnheader", { name: "Tables" })).toBeVisible();
    expect(screen.getByRole("columnheader", { name: "Validation" })).toBeVisible();
    // Rows re-render as analysis states arrive, so re-query until they settle.
    await waitFor(() => {
      expect(screen.getByText("42")).toBeVisible();
      expect(screen.getByText("Valid")).toBeVisible();
      expect(screen.getByText("Analyzing")).toBeVisible();
    });
  });

  it("uses a plain hyphen when a failed analysis has no table count", () => {
    renderResults({ candidates: [failedCandidate] });

    expect(screen.getByRole("cell", { name: "-" })).toHaveTextContent("-");
  });

  it("reveals a complete document title on hover and keyboard focus", async () => {
    renderResults();
    const title = screen.getByRole("link", { name: "Annual report 2026" });

    fireEvent.mouseEnter(title);
    expect(await screen.findByRole("tooltip")).toHaveTextContent("Annual report 2026");

    fireEvent.mouseLeave(title);
    await waitFor(() => expect(screen.queryByRole("tooltip")).not.toBeInTheDocument());
    fireEvent.focus(title);
    expect(await screen.findByRole("tooltip")).toHaveTextContent("Annual report 2026");
  });

  it("selects only candidates whose analysis is ready", () => {
    const empty = renderResults();
    fireEvent.click(
      screen.getByRole("checkbox", { name: "Select all analyzed documents" }),
    );
    expect(empty.onSelectAll).toHaveBeenCalledOnce();
    expect(
      screen.getByRole("checkbox", { name: "Select Investment outlook" }),
    ).toBeDisabled();

    cleanup();
    const selected = renderResults({ selectedIds: [candidates[0].id] });
    fireEvent.click(
      screen.getByRole("checkbox", { name: "Clear all analyzed documents" }),
    );
    expect(selected.onClear).toHaveBeenCalledOnce();
  });

  it("shows the generated preview, table overlay, and extracted markdown", async () => {
    renderResults();

    fireEvent.click(
      screen.getByRole("button", { name: "Show details for Annual report 2026" }),
    );
    await waitFor(() =>
      expect(screen.getByText("Audited investment tables")).toBeVisible(),
    );
    expect(screen.getByText("https://bank.example/annual-report.pdf")).toBeVisible();
    expect(
      screen.getByRole("img", { name: "Page 2 preview for Annual report 2026" }),
    ).toHaveAttribute(
      "src",
      "/api/v1/discovery/analyses/10000000-0000-4000-8000-000000000001/preview",
    );
    expect(
      screen.getByTestId("table-overlay-20000000-0000-4000-8000-000000000001"),
    ).toBeVisible();
    expect(screen.getByText(/\| Alpha \| \$42 \|/)).toBeVisible();
  });
});
