import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function renderApp(
  queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  }),
) {
  return {
    queryClient,
    ...render(
    <MantineProvider>
      <QueryClientProvider client={queryClient}>
        <App />
      </QueryClientProvider>
    </MantineProvider>,
    ),
  };
}

const authenticatedSession = {
  status: "authenticated",
  csrf_token: "csrf-token",
  idle_expires_at: "2026-08-25T12:00:00Z",
  authentication_mode: "local",
  user: null,
  workspace: {
    id: "00000000-0000-4000-8000-000000000001",
    name: "Local workspace",
    role: "owner",
  },
  workspaces: [
    {
      id: "00000000-0000-4000-8000-000000000001",
      name: "Local workspace",
      role: "owner",
    },
  ],
};

const healthStatus = {
  overall: "ready",
  build_id: "test-build",
  components: [
    { name: "database", status: "available", detail: null, required: true },
    { name: "worker", status: "available", detail: null, required: true },
  ],
};

const emptyPage = { items: [], total: 0, next_cursor: null };

describe("App", () => {
  it("removes every workspace-scoped cache before loading a switched workspace", async () => {
    const secondWorkspace = {
      id: "00000000-0000-4000-8000-000000000002",
      name: "Second workspace",
      role: "owner" as const,
    };
    const hostedSession = {
      ...authenticatedSession,
      authentication_mode: "oidc" as const,
      user: {
        id: "20000000-0000-4000-8000-000000000071",
        email: "owner@parserium.test",
        display_name: "Workspace owner",
      },
      workspaces: [authenticatedSession.workspace, secondWorkspace],
    };
    const switchedSession = { ...hostedSession, workspace: secondWorkspace };
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith("/api/v1/session/status")) {
        return Promise.resolve(new Response(JSON.stringify(hostedSession), { status: 200 }));
      }
      if (path.endsWith("/api/v1/session/workspace")) {
        return Promise.resolve(new Response(JSON.stringify(switchedSession), { status: 200 }));
      }
      if (path.endsWith("/api/v1/health/status")) {
        return Promise.resolve(new Response(JSON.stringify(healthStatus), { status: 200 }));
      }
      if (path.endsWith("/api/v1/firecrawl/connections")) {
        return Promise.resolve(new Response(JSON.stringify({ connections: [] }), { status: 200 }));
      }
      if (path.includes("/api/v1/collection/jobs") || path.includes("/api/v1/documents")) {
        return Promise.resolve(new Response(JSON.stringify(emptyPage), { status: 200 }));
      }
      if (path.includes("/api/v1/exports")) {
        return Promise.resolve(new Response(JSON.stringify([]), { status: 200 }));
      }
      if (path.includes("/api/v1/activity")) {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              items: [],
              total: 0,
              next_cursor: null,
              summary: { active: 0, queued: 0, failed: 0, completed: 0 },
            }),
            { status: 200 },
          ),
        );
      }
      throw new Error(`Unexpected request: ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    const removeQueries = vi.spyOn(queryClient, "removeQueries");
    renderApp(queryClient);

    fireEvent.click(await screen.findByRole("button", { name: "Connections" }));
    expect(await screen.findByRole("heading", { name: "Firecrawl connections" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: /Workspace: Local workspace/ }));
    fireEvent.click(await screen.findByRole("menuitemradio", { name: /Second workspace/ }));

    expect(
      await screen.findByRole("heading", { name: "Find documents worth keeping." }),
    ).toBeVisible();
    for (const queryKey of [
      "analysis-search",
      "collection-jobs",
      "documents",
      "exports",
      "firecrawl-connections",
      "health",
    ]) {
      expect(removeQueries).toHaveBeenCalledWith({ queryKey: [queryKey] });
    }
  });

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

  it("shows hosted sign in when authentication is required", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            status: "login_required",
            login_url: "/api/v1/auth/login",
            provider_label: "Google",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );

    renderApp();

    expect(await screen.findByRole("heading", { name: "Welcome to Parserium" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Continue with Google" })).toBeVisible();
    expect(screen.queryByLabelText(/Pairing code/)).not.toBeInTheDocument();
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
      if (path.includes("/api/v1/collection/jobs") || path.includes("/api/v1/documents")) {
        return Promise.resolve(new Response(JSON.stringify(emptyPage), { status: 200 }));
      }
      if (path.includes("/api/v1/exports")) {
        return Promise.resolve(new Response(JSON.stringify([]), { status: 200 }));
      }
      throw new Error(`Unexpected request: ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderApp();

    fireEvent.change(await screen.findByLabelText(/Pairing code/), {
      target: { value: "operator-code" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Pair browser" }));

    expect(await screen.findByRole("heading", { name: "Document discovery" })).toBeVisible();
    expect(screen.queryByRole("heading", { name: "System health" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Health" }));
    const healthDialog = await screen.findByRole("dialog", { name: "System health" });
    await waitFor(() => expect(healthDialog).toBeVisible());
    expect(await screen.findByText("database")).toBeVisible();
    expect(screen.queryByText("Run collection")).not.toBeInTheDocument();
  });

  it("sends the in-memory CSRF token when logging out", async () => {
    let loggedOut = false;
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path.endsWith("/api/v1/session/status")) {
        return Promise.resolve(
          new Response(
            JSON.stringify(loggedOut ? { status: "pairing_required" } : authenticatedSession),
            { status: 200 },
          ),
        );
      }
      if (path.endsWith("/api/v1/health/status")) {
        return Promise.resolve(new Response(JSON.stringify(healthStatus), { status: 200 }));
      }
      if (path.includes("/api/v1/collection/jobs") || path.includes("/api/v1/documents")) {
        return Promise.resolve(new Response(JSON.stringify(emptyPage), { status: 200 }));
      }
      if (path.includes("/api/v1/exports")) {
        return Promise.resolve(new Response(JSON.stringify([]), { status: 200 }));
      }
      if (path.endsWith("/api/v1/session/logout")) {
        expect(new Headers(init?.headers).get("X-Parserium-CSRF")).toBe("csrf-token");
        loggedOut = true;
        return Promise.resolve(new Response(null, { status: 204 }));
      }
      throw new Error(`Unexpected request: ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderApp();

    fireEvent.click(await screen.findByRole("button", { name: "Health" }));
    const healthDialog = await screen.findByRole("dialog", { name: "System health" });
    await waitFor(() => expect(healthDialog).toBeVisible());
    fireEvent.click(within(healthDialog).getByRole("button", { name: "Log out" }));

    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Pair this browser" })).toBeVisible(),
    );
  });

  it("opens Documents and Health from the top navigation", async () => {
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith("/api/v1/session/status")) {
        return Promise.resolve(new Response(JSON.stringify(authenticatedSession), { status: 200 }));
      }
      if (path.endsWith("/api/v1/health/status")) {
        return Promise.resolve(new Response(JSON.stringify(healthStatus), { status: 200 }));
      }
      if (path.includes("/api/v1/collection/jobs") || path.includes("/api/v1/documents")) {
        return Promise.resolve(new Response(JSON.stringify(emptyPage), { status: 200 }));
      }
      if (path.includes("/api/v1/exports")) {
        return Promise.resolve(new Response(JSON.stringify([]), { status: 200 }));
      }
      throw new Error(`Unexpected request: ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderApp();

    const discoveryQuery = await screen.findByPlaceholderText("Search reports, research, manuals...");
    fireEvent.change(discoveryQuery, { target: { value: "retained search" } });

    fireEvent.click(await screen.findByRole("button", { name: "Activity" }));
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Activity" })).toBeVisible(),
    );

    fireEvent.click(
      within(screen.getByRole("navigation", { name: "Primary navigation" })).getByRole(
        "button",
        { name: "Collect" },
      ),
    );
    await waitFor(() =>
      expect(screen.getByRole("region", { name: "Document discovery" })).toHaveFocus(),
    );
    expect(screen.getByPlaceholderText("Search reports, research, manuals...")).toHaveValue(
      "retained search",
    );

    fireEvent.click(await screen.findByRole("button", { name: "Documents" }));
    await waitFor(() =>
      expect(screen.getByRole("dialog", { name: "Document library" })).toBeVisible(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Close document library" }));

    fireEvent.click(screen.getByRole("button", { name: "Health" }));
    await waitFor(() =>
      expect(screen.getByRole("dialog", { name: "System health" })).toBeVisible(),
    );
  });
});
