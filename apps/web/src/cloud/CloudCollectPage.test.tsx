import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { parseriumTheme } from "../theme";
import { WorkspacePage } from "./WorkspacePage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

describe("Cloud Collect evidence wall", () => {
  it("keeps checkbox selection as the source of truth for the collection tray", async () => {
    window.history.replaceState(null, "", "/#collect");
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const body = url.includes("/documents?") ? { documents: [], has_more: false }
        : url.includes("/discovery/searches?") ? { searches: [{
          id: "search-1",
          query: "defibrillator outcomes",
          file_type: "pdf",
          status: "succeeded",
          error_code: null,
          created_at: 1_760_000_000,
          results: [
            { id: "result-1", title: "Clinical outcomes.pdf", description: "Peer-reviewed report", url: "https://research.example/outcomes.pdf", file_type: "pdf" },
            { id: "result-2", title: "Second report.pdf", description: "Supporting evidence", url: "https://research.example/second.pdf", file_type: "pdf" },
          ],
        }] }
        : url.includes("/discovery/connection?") ? { configured: true, can_manage: true, updated_at: null, storage_available: true }
        : { processing: "enabled" };
      return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
    }));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    render(<MantineProvider theme={parseriumTheme}><QueryClientProvider client={client}><WorkspacePage
      workspace={{ id: "workspace", name: "Research Library", role: "owner" }}
      accountSlot={null}
    /></QueryClientProvider></MantineProvider>);

    expect(await screen.findByRole("heading", { name: "Find documents" })).toBeVisible();
    expect(screen.getByRole("navigation", { name: "Research workflow" })).toBeVisible();
    expect(screen.getByText("Source field")).toBeVisible();
    const tray = screen.getByRole("complementary", { name: "Selected documents" });
    expect(within(tray).getByText("Nothing selected yet")).toBeVisible();

    const firstResult = (await screen.findByRole("link", { name: "Clinical outcomes.pdf" })).closest('[role="row"]');
    const secondResultLink = screen.getByRole("link", { name: "Second report.pdf" });
    expect(firstResult).toHaveAttribute("data-focused", "true");
    fireEvent.focus(secondResultLink);
    expect(secondResultLink.closest('[role="row"]')).toHaveAttribute("data-focused", "true");

    fireEvent.click(await screen.findByRole("checkbox", { name: "Select Clinical outcomes.pdf" }));
    await waitFor(() => expect(within(tray).getByText("Clinical outcomes.pdf")).toBeVisible());
    expect(screen.getByRole("checkbox", { name: "Select Clinical outcomes.pdf" })).toBeChecked();
  });
});
