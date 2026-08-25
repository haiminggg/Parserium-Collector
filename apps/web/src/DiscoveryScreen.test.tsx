import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DiscoveryScreen } from "./DiscoveryScreen";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function renderDiscovery() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <MantineProvider>
      <QueryClientProvider client={queryClient}>
        <DiscoveryScreen csrfToken="in-memory-csrf" />
      </QueryClientProvider>
    </MantineProvider>,
  );
}

describe("DiscoveryScreen", () => {
  it("sends the selected document configuration and renders direct links", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          provider_search_ids: ["search-1"],
          candidates: [
            {
              url: "https://bank.example/report.pdf",
              title: "Investment report",
              description: "Annual fund report",
              document_type: "pdf",
              source: "firecrawl",
            },
          ],
          rejected_non_document_results: 2,
        }),
        { status: 200 },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    renderDiscovery();

    fireEvent.change(screen.getByLabelText(/Search query/), {
      target: { value: "bank investment tables" },
    });
    fireEvent.click(screen.getByRole("checkbox", { name: "DOCX" }));
    fireEvent.change(screen.getByLabelText("Result limit"), { target: { value: "5" } });
    fireEvent.change(screen.getByLabelText("Include domains"), {
      target: { value: "bank.example, reports.bank.example" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Search documents" }));

    const link = await screen.findByRole("link", { name: "Investment report" });
    expect(link).toHaveAttribute("href", "https://bank.example/report.pdf");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(fetchMock).toHaveBeenCalledOnce();
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(new Headers(init.headers).get("X-Parserium-CSRF")).toBe("in-memory-csrf");
    expect(JSON.parse(String(init.body))).toEqual({
      query: "bank investment tables",
      limit: 5,
      document_types: ["pdf"],
      include_domains: ["bank.example", "reports.bank.example"],
      exclude_domains: [],
    });
  });

  it("rejects conflicting domain modes before sending a request", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    renderDiscovery();

    fireEvent.change(screen.getByLabelText(/Search query/), {
      target: { value: "bank report" },
    });
    fireEvent.change(screen.getByLabelText("Include domains"), {
      target: { value: "bank.example" },
    });
    fireEvent.change(screen.getByLabelText("Exclude domains"), {
      target: { value: "other.example" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Search documents" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Use either include domains or exclude domains",
    );
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("shows an explicit empty state", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            provider_search_ids: ["search-1", "search-2"],
            candidates: [],
            rejected_non_document_results: 4,
          }),
          { status: 200 },
        ),
      ),
    );
    renderDiscovery();

    fireEvent.change(screen.getByLabelText(/Search query/), {
      target: { value: "bank report" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Search documents" }));

    expect(await screen.findByText("No direct PDF or DOCX links were found.")).toBeVisible();
  });

  it("shows a generic upstream failure", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "Document discovery is unavailable." }), {
          status: 502,
        }),
      ),
    );
    renderDiscovery();

    fireEvent.change(screen.getByLabelText(/Search query/), {
      target: { value: "bank report" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Search documents" }));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("Document search is unavailable"),
    );
  });
});
