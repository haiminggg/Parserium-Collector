import { MantineProvider } from "@mantine/core";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { parseriumTheme } from "../theme";
import type { DocumentRecord, SearchRecord } from "./api";
import { CloudActivityPage } from "./CloudActivityPage";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const parsedDocument: DocumentRecord = {
  id: "document-1",
  filename: "evidence-study.pdf",
  size_bytes: 4096,
  created_at: 1_760_000_000,
  source_url: "https://research.example/evidence-study.pdf",
  validation_status: "valid",
  validation_error_code: null,
  job_id: "job-1",
  job_status: "succeeded",
  job_error_code: null,
};

const search: SearchRecord = {
  id: "search-1",
  query: "implantable defibrillator outcomes",
  file_type: "pdf",
  status: "succeeded",
  error_code: null,
  created_at: 1_760_000_100,
  results: [],
};

describe("CloudActivityPage", () => {
  it("keeps parse and search records in independent trace lanes", () => {
    render(<MantineProvider theme={parseriumTheme}><CloudActivityPage
      workspace={{ id: "workspace", name: "Research Library", role: "owner" }}
      documents={[parsedDocument]}
      searches={[search]}
      alerts={null}
      documentsPending={false}
      searchesPending={false}
      searchesError={null}
      refreshing={false}
      onRefresh={vi.fn()}
      onRefreshSearches={vi.fn()}
      onInspect={vi.fn()}
      onOpenSearch={vi.fn()}
      onOpenDocuments={vi.fn()}
    /></MantineProvider>);

    expect(screen.getByRole("heading", { name: "Activity" })).toBeVisible();
    expect(screen.getByRole("region", { name: "Document processing" })).toBeVisible();
    expect(screen.getByRole("complementary", { name: "Search history" })).toBeVisible();
    expect(screen.getByText("Parsing activity reflects jobs attached to the current documents page.")).toBeVisible();
    expect(screen.getByRole("button", { name: /implantable defibrillator outcomes/i })).toBeEnabled();
    expect(document.querySelector("[data-search-document-relationship]")).not.toBeInTheDocument();
  });
});
