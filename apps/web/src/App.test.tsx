import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function renderApp() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <MantineProvider>
      <QueryClientProvider client={queryClient}>
        <App />
      </QueryClientProvider>
    </MantineProvider>,
  );
}

const authenticatedSession = {
  status: "authenticated",
  csrf_token: "csrf-token",
  idle_expires_at: "2026-08-25T12:00:00Z",
};

const healthStatus = {
  overall: "ready",
  build_id: "test-build",
  components: [
    { name: "database", status: "available", detail: null, required: true },
    { name: "worker", status: "available", detail: null, required: true },
  ],
};

describe("App", () => {
  it("shows pairing before exposing the authenticated shell", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ status: "pairing_required" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    renderApp();

    expect(await screen.findByRole("heading", { name: "Pair this browser" })).toBeVisible();
    expect(screen.queryByRole("heading", { name: "System health" })).not.toBeInTheDocument();
  });

  it("pairs the browser and then shows reported components", async () => {
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith("/api/v1/session/status")) {
        return Promise.resolve(
          new Response(JSON.stringify({ status: "pairing_required" }), { status: 200 }),
        );
      }
      if (path.endsWith("/api/v1/session/pair")) {
        return Promise.resolve(
          new Response(JSON.stringify(authenticatedSession), { status: 200 }),
        );
      }
      if (path.endsWith("/api/v1/health/status")) {
        return Promise.resolve(new Response(JSON.stringify(healthStatus), { status: 200 }));
      }
      throw new Error(`Unexpected request: ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderApp();

    fireEvent.change(await screen.findByLabelText(/Pairing code/), {
      target: { value: "operator-code" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Pair browser" }));

    expect(await screen.findByRole("heading", { name: "System health" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Document discovery" })).toBeVisible();
    expect(await screen.findByText("database")).toBeVisible();
    expect(screen.queryByText("Run collection")).not.toBeInTheDocument();
  });

  it("sends the in-memory CSRF token when logging out", async () => {
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path.endsWith("/api/v1/session/status")) {
        return Promise.resolve(new Response(JSON.stringify(authenticatedSession), { status: 200 }));
      }
      if (path.endsWith("/api/v1/health/status")) {
        return Promise.resolve(new Response(JSON.stringify(healthStatus), { status: 200 }));
      }
      if (path.endsWith("/api/v1/session/logout")) {
        expect(new Headers(init?.headers).get("X-Parserium-CSRF")).toBe("csrf-token");
        return Promise.resolve(new Response(null, { status: 204 }));
      }
      throw new Error(`Unexpected request: ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderApp();

    fireEvent.click(await screen.findByRole("button", { name: "Log out" }));

    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Pair this browser" })).toBeVisible(),
    );
  });
});
