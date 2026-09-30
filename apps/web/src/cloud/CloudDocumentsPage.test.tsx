import { MantineProvider } from "@mantine/core";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { parseriumTheme } from "../theme";
import type { DocumentRecord } from "./api";
import { CloudDocumentsPage } from "./CloudDocumentsPage";

afterEach(cleanup);

const document = (id: string, status: string | null, validationStatus = "valid"): DocumentRecord => ({
  id,
  filename: `${id}.pdf`,
  size_bytes: 2048,
  created_at: 1_760_000_000,
  source_url: `https://research.example/${id}.pdf`,
  validation_status: validationStatus,
  validation_error_code: validationStatus === "invalid" ? "invalid_pdf" : null,
  job_id: status ? `job-${id}` : null,
  job_status: status,
  job_error_code: status === "failed" ? "parser_failed" : null,
});

function renderPage(documents: DocumentRecord[]) {
  render(<MantineProvider theme={parseriumTheme}><CloudDocumentsPage
    workspace={{ id: "workspace", name: "Research Library", role: "owner" }}
    documents={documents}
    visibleDocuments={documents}
    alerts={null}
    error={null}
    isPending={false}
    isFetching={false}
    filter=""
    busy=""
    processingDisabled={false}
    offset={0}
    hasMore={false}
    onFilterChange={vi.fn()}
    onUpload={vi.fn()}
    onRefresh={vi.fn()}
    onInspect={vi.fn()}
    onParse={vi.fn()}
    onDelete={vi.fn()}
    onPrevious={vi.fn()}
    onNext={vi.fn()}
  /></MantineProvider>);
}

describe("CloudDocumentsPage", () => {
  it("presents a saved-document overview derived from the current page", () => {
    renderPage([document("ready-study", "succeeded"), document("unparsed-study", null)]);

    expect(screen.getByRole("heading", { name: "Documents" })).toBeVisible();
    const feature = screen.getByLabelText("Saved document");
    expect(feature).toBeVisible();
    expect(within(feature).getByText("ready-study.pdf")).toBeVisible();
    expect(within(feature).getByText("Ready")).toBeVisible();
    expect(screen.getByRole("navigation", { name: "Research workflow" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Parse" })).toBeEnabled();
    expect(screen.getAllByRole("button", { name: "View output" })).toHaveLength(2);
    expect(screen.getAllByLabelText(/^Download /)).toHaveLength(2);
    expect(screen.getAllByLabelText(/^Delete /)).toHaveLength(2);
  });

  it("hides the saved-document overview when the current page is empty", () => {
    renderPage([]);

    expect(screen.queryByLabelText("Saved document")).not.toBeInTheDocument();
  });

  it("labels status counts as page-scoped", () => {
    renderPage([
      document("running-study", "running"),
      document("failed-study", "failed"),
      document("invalid-study", null, "invalid"),
    ]);

    expect(screen.getByText("Current page")).toBeVisible();
    const metrics = screen.getByLabelText("Document status counts on this page");
    expect(within(metrics).getByText("Processing").previousElementSibling).toHaveTextContent("1");
    expect(within(metrics).getByText("Needs attention").previousElementSibling).toHaveTextContent("2");
  });
});
