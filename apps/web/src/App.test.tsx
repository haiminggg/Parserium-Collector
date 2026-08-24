import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("App", () => {
  it("shows reported components without claiming unavailable features", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            overall: "degraded",
            build_id: "test-build",
            components: [
              { name: "database", status: "available", detail: null },
              { name: "worker", status: "unavailable", detail: "No fresh worker heartbeat." },
            ],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    render(
      <MantineProvider>
        <QueryClientProvider client={queryClient}>
          <App />
        </QueryClientProvider>
      </MantineProvider>,
    );

    expect(screen.getByRole("heading", { name: "System health" })).toBeVisible();
    expect(await screen.findByText("database")).toBeVisible();
    expect(screen.getByText("worker")).toBeVisible();
    expect(screen.queryByText("Run collection")).not.toBeInTheDocument();
  });
});
