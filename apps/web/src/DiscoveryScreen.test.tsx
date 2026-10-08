import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DiscoveryScreen } from "./DiscoveryScreen";
import type { AnalysisCandidate, AnalysisSession } from "./api/discovery";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function renderDiscovery(
  firecrawl?: React.ComponentProps<typeof DiscoveryScreen>["firecrawl"],
) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <MantineProvider>
      <QueryClientProvider client={queryClient}>
        <DiscoveryScreen csrfToken="in-memory-csrf" firecrawl={firecrawl} />
      </QueryClientProvider>
    </MantineProvider>,
  );
}

function analysisCandidate(index = 0): AnalysisCandidate {
  return {
    id: `10000000-0000-4000-8000-${String(index + 1).padStart(12, "0")}`,
    ordinal: index,
    source_url: `https://bank.example/report-${index}.pdf`,
    title: `Investment report ${index}`,
    description: "Annual fund report",
    document_type: "pdf",
    status: "ready",
    public_state: "valid",
    attempt_count: 1,
    bytes_downloaded: 1024,
    content_length: 1024,
    page_count: 5,
    analyzed_page_count: 5,
    table_count: 8,
    table_count_lower_bound: false,
    preview_available: true,
    preview_page_num: 1,
    preview_width: 1275,
    preview_height: 1650,
    error_code: null,
    error_detail: null,
    error_retryable: null,
    tables: [],
    created_at: "2026-08-27T12:00:00Z",
    updated_at: "2026-08-27T12:00:02Z",
    completed_at: "2026-08-27T12:00:02Z",
  };
}

function analysisSession(candidates: AnalysisCandidate[]): AnalysisSession {
  return {
    id: "30000000-0000-4000-8000-000000000001",
    query: "bank investment tables",
    document_types: ["pdf"],
    tables_required: true,
    firecrawl_connection_id: null,
    firecrawl_connection_name_snapshot: null,
    firecrawl_connection_type_snapshot: null,
    status: "completed",
    job_stage: "completed",
    error_code: null,
    candidate_count: candidates.length,
    bytes_downloaded: candidates.reduce(
      (total, candidate) => total + candidate.bytes_downloaded,
      0,
    ),
    session_byte_limit: 536870912,
    cancellation_requested: false,
    created_at: "2026-08-27T12:00:00Z",
    updated_at: "2026-08-27T12:00:02Z",
    expires_at: "2026-08-27T13:00:00Z",
    completed_at: "2026-08-27T12:00:02Z",
    candidates,
  };
}

describe("DiscoveryScreen", () => {
  it("introduces collection without exposing unsupported input methods", async () => {
    renderDiscovery();
    expect(screen.getByRole("heading", { name: "Find documents worth keeping." })).toBeVisible();
    expect(screen.getByPlaceholderText("Search reports, research, manuals...")).toBeVisible();
    expect(screen.getByText("Search online")).toBeVisible();
    expect(screen.getByRole("button", { name: "Discover" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Upload files" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Collection settings" }));
    await waitFor(() => expect(screen.getByRole("dialog", { name: "Discovery filters" })).toBeVisible());
    fireEvent.change(screen.getByLabelText("Include domains"), { target: { value: "example.org" } });
    fireEvent.click(screen.getByRole("button", { name: "Close filters" }));
    expect(screen.getByText(/Included sources/)).toBeVisible();
  });

  it("disables hosted discovery when no connection is usable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            connections: [
              {
                id: "30000000-0000-4000-8000-000000000073",
                name: "Needs validation",
                connection_type: "cloud",
                status: "never_validated",
                enabled: true,
                is_default: false,
                usable: false,
                last_validation_attempt_at: null,
                last_validation_success_at: null,
                last_failure_category: null,
              },
            ],
          }),
          { status: 200 },
        ),
      ),
    );
    renderDiscovery({
      workspaceId: "10000000-0000-4000-8000-000000000073",
      role: "member",
      onConfigure: vi.fn(),
    });

    fireEvent.change(screen.getByLabelText(/Search query/), {
      target: { value: "bank report" },
    });
    expect(await screen.findByText("No usable connection")).toBeVisible();
    expect(screen.getByRole("button", { name: "Discover" })).toBeDisabled();
    expect(screen.getByText("Ask a workspace owner to validate a connection.")).toBeVisible();
  });

  it("submits a healthy hosted connection override", async () => {
    const defaultConnection = {
      id: "30000000-0000-4000-8000-000000000071",
      name: "Primary Cloud",
      connection_type: "cloud",
      status: "healthy",
      enabled: true,
      is_default: true,
      usable: true,
      last_validation_attempt_at: "2026-09-02T12:00:00Z",
      last_validation_success_at: "2026-09-02T12:00:00Z",
      last_failure_category: null,
    };
    const overrideConnection = {
      ...defaultConnection,
      id: "30000000-0000-4000-8000-000000000072",
      name: "Research remote",
      connection_type: "remote",
      is_default: false,
    };
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith("/api/v1/firecrawl/connections")) {
        return Promise.resolve(
          new Response(JSON.stringify({ connections: [defaultConnection, overrideConnection] }), {
            status: 200,
          }),
        );
      }
      if (path.endsWith("/api/v1/discovery/searches")) {
        return Promise.resolve(
          new Response(JSON.stringify(analysisSession([])), { status: 202 }),
        );
      }
      throw new Error(`Unexpected request: ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderDiscovery({
      workspaceId: "10000000-0000-4000-8000-000000000071",
      role: "owner",
      onConfigure: vi.fn(),
    });

    const selector = await screen.findByRole("combobox", { name: "Firecrawl connection" });
    await waitFor(() => expect(selector).toHaveValue(defaultConnection.id));
    fireEvent.change(screen.getByLabelText(/Search query/), {
      target: { value: "bank report" },
    });
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Discover" })).toBeEnabled(),
    );
    fireEvent.change(selector, { target: { value: overrideConnection.id } });
    fireEvent.click(screen.getByRole("button", { name: "Discover" }));

    await screen.findByText("No direct PDF or DOCX links were found.");
    const [, init] = fetchMock.mock.calls.find(([input]) =>
      String(input).endsWith("/api/v1/discovery/searches"),
    ) as [string, RequestInit];
    expect(JSON.parse(String(init.body))).toMatchObject({
      firecrawl_connection_id: overrideConnection.id,
    });
  });

  it("keeps advanced filters collapsed until requested", async () => {
    vi.stubGlobal("fetch", vi.fn());
    renderDiscovery();

    expect(
      screen.getByRole("heading", {
        name: "Find documents worth keeping.",
      }),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "Document types PDF + DOCX" })).toBeVisible();
    expect(screen.getByRole("switch", { name: "Tables required" })).toBeChecked();
    expect(screen.queryByLabelText("Include domains")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Document types PDF + DOCX" }));
    const dialog = await screen.findByRole("dialog", { name: "Discovery filters" });
    await waitFor(() => expect(dialog).toBeVisible());
    expect(screen.getByLabelText("Include domains")).toBeVisible();
    expect(screen.getByLabelText("Exclude domains")).toBeVisible();
    expect(screen.getByLabelText("Result limit")).toBeVisible();
  });

  it("starts local analysis with the selected configuration and renders real results", async () => {
    const candidate = analysisCandidate();
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(analysisSession([candidate])), { status: 202 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    renderDiscovery();

    expect(
      screen.queryByRole("combobox", { name: "Firecrawl connection" }),
    ).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/Search query/), {
      target: { value: "bank investment tables" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Document types PDF + DOCX" }));
    await screen.findByRole("dialog", { name: "Discovery filters" });
    fireEvent.click(screen.getByRole("checkbox", { name: "DOCX" }));
    fireEvent.change(screen.getByLabelText("Result limit"), { target: { value: "5" } });
    fireEvent.change(screen.getByLabelText("Include domains"), {
      target: { value: "bank.example, reports.bank.example" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Close filters" }));
    fireEvent.click(screen.getByRole("button", { name: "Discover" }));

    const link = await screen.findByRole("link", { name: "Investment report 0" });
    expect(link).toHaveAttribute("href", candidate.source_url);
    const content = document.querySelector(".discovery-content");
    expect(content).not.toBeNull();
    expect(content).toContainElement(
      screen.getByRole("region", { name: "Discovered documents" }),
    );
    await waitFor(() => expect(screen.getByText("8")).toBeVisible());
    expect(screen.getByText("Valid")).toBeVisible();
    expect(fetchMock).toHaveBeenCalledOnce();
    const [path, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(path).toBe("/api/v1/discovery/searches");
    expect(new Headers(init.headers).get("X-Parserium-CSRF")).toBe("in-memory-csrf");
    expect(JSON.parse(String(init.body))).toEqual({
      query: "bank investment tables",
      limit: 5,
      document_types: ["pdf"],
      include_domains: ["bank.example", "reports.bank.example"],
      exclude_domains: [],
      tables_required: true,
      firecrawl_connection_id: null,
    });
  });

  it("rejects conflicting domain modes before sending a request", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    renderDiscovery();

    fireEvent.change(screen.getByLabelText(/Search query/), {
      target: { value: "bank report" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Document types PDF + DOCX" }));
    await screen.findByRole("dialog", { name: "Discovery filters" });
    fireEvent.change(screen.getByLabelText("Include domains"), {
      target: { value: "bank.example" },
    });
    fireEvent.change(screen.getByLabelText("Exclude domains"), {
      target: { value: "other.example" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Close filters" }));
    fireEvent.click(screen.getByRole("button", { name: "Discover" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Use either include domains or exclude domains",
    );
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("shows an explicit empty state", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify(analysisSession([])), { status: 202 }),
      ),
    );
    renderDiscovery();

    fireEvent.change(screen.getByLabelText(/Search query/), {
      target: { value: "bank report" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Discover" }));

    expect(await screen.findByText("No direct PDF or DOCX links were found.")).toBeVisible();
  });

  it("shows a Firecrawl-specific message when discovery cannot start", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            detail: {
              code: "firecrawl_discovery_unavailable",
              message: "Untrusted server copy must not be rendered.",
            },
          }),
          { status: 502 },
        ),
      ),
    );
    renderDiscovery();

    fireEvent.change(screen.getByLabelText(/Search query/), {
      target: { value: "bank report" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Discover" }));

    expect(await screen.findByText("Discovery failed")).toBeVisible();
    expect(
      screen.getByText(
        "Firecrawl document discovery is unavailable. Test the selected connection and try again.",
      ),
    ).toBeVisible();
    expect(screen.queryByText("Untrusted server copy must not be rendered.")).not.toBeInTheDocument();
  });

  it("shows a worker-specific message when analysis polling fails", async () => {
    const runningSession: AnalysisSession = {
      ...analysisSession([]),
      status: "running",
      completed_at: null,
    };
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith("/api/v1/discovery/searches")) {
        return Promise.resolve(
          new Response(JSON.stringify(runningSession), { status: 202 }),
        );
      }
      if (path.endsWith(`/api/v1/discovery/searches/${runningSession.id}`)) {
        return Promise.resolve(new Response(null, { status: 503 }));
      }
      throw new Error(`Unexpected request: ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderDiscovery();

    fireEvent.change(screen.getByLabelText(/Search query/), {
      target: { value: "bank report" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Discover" }));

    expect(await screen.findByText("Analysis unavailable", {}, { timeout: 5000 })).toBeVisible();
    expect(
      screen.getByText(
        "Local table analysis status is unavailable. Check the Parserium worker and try again.",
      ),
    ).toBeVisible();
  });

  it("collects selected analysis identifiers and preserves the results", async () => {
    const candidates = Array.from({ length: 30 }, (_, index) => analysisCandidate(index));
    const session = analysisSession(candidates);
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith("/api/v1/discovery/searches")) {
        return Promise.resolve(new Response(JSON.stringify(session), { status: 202 }));
      }
      if (path.endsWith("/api/v1/collection/jobs")) {
        return Promise.resolve(new Response(JSON.stringify([]), { status: 202 }));
      }
      if (path.includes("/api/v1/discovery/searches/")) {
        return Promise.resolve(new Response(JSON.stringify(session), { status: 200 }));
      }
      throw new Error(`Unexpected request: ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderDiscovery();

    fireEvent.change(screen.getByLabelText(/Search query/), {
      target: { value: "bank reports" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Discover" }));

    expect(await screen.findByText("30 results")).toBeVisible();
    fireEvent.click(screen.getByRole("checkbox", { name: "Select all analyzed documents" }));
    expect(screen.getByText("30 selected")).toBeVisible();
    fireEvent.click(screen.getByRole("checkbox", { name: "Clear all analyzed documents" }));
    expect(screen.getByText("0 selected")).toBeVisible();
    fireEvent.click(screen.getByRole("checkbox", { name: "Select Investment report 0" }));
    fireEvent.click(screen.getByRole("button", { name: "Collect selected" }));

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([input]) =>
          String(input).endsWith("/api/v1/collection/jobs"),
        ),
      ).toBe(true),
    );
    const [path, collectionInit] = fetchMock.mock.calls.find(([input]) =>
      String(input).endsWith("/api/v1/collection/jobs"),
    ) as [string, RequestInit];
    expect(path).toBe("/api/v1/collection/jobs");
    expect(JSON.parse(String(collectionInit.body))).toEqual({
      analysis_ids: [candidates[0].id],
    });
    expect(screen.getByText("30 results")).toBeVisible();
  });
});
