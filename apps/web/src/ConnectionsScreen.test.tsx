import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ConnectionsScreen } from "./ConnectionsScreen";
import type { AuthenticatedSession } from "./api/session";
import { parseriumTheme } from "./theme";

const connectionId = "30000000-0000-4000-8000-000000000051";
const connection = {
  id: connectionId,
  name: "Research Firecrawl",
  connection_type: "remote" as const,
  status: "healthy" as const,
  enabled: true,
  is_default: false,
  usable: true,
  normalized_base_url: "https://firecrawl.research.example",
  last_validation_attempt_at: "2026-09-02T12:00:00Z",
  last_validation_success_at: "2026-09-02T12:00:00Z",
  last_failure_category: null,
};

const ownerSession: AuthenticatedSession = {
  status: "authenticated",
  csrf_token: "csrf-token",
  idle_expires_at: "2026-09-02T13:00:00Z",
  authentication_mode: "oidc",
  user: {
    id: "20000000-0000-4000-8000-000000000051",
    email: "owner@parserium.test",
    display_name: "Workspace owner",
  },
  workspace: {
    id: "10000000-0000-4000-8000-000000000051",
    name: "Research workspace",
    role: "owner",
  },
  workspaces: [],
};
ownerSession.workspaces = [ownerSession.workspace];

const memberSession: AuthenticatedSession = {
  ...ownerSession,
  user: {
    id: "20000000-0000-4000-8000-000000000052",
    email: "member@parserium.test",
    display_name: "Workspace member",
  },
  workspace: { ...ownerSession.workspace, role: "member" },
  workspaces: [{ ...ownerSession.workspace, role: "member" }],
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function renderConnections(session: AuthenticatedSession = ownerSession) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <MantineProvider theme={parseriumTheme} forceColorScheme="light">
      <QueryClientProvider client={queryClient}>
        <ConnectionsScreen session={session} />
      </QueryClientProvider>
    </MantineProvider>,
  );
}

function json(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("ConnectionsScreen", () => {
  it("creates a connection and clears the unsaved token after success", async () => {
    const created = {
      ...connection,
      name: "Primary Cloud",
      connection_type: "cloud" as const,
      normalized_base_url: undefined,
      status: "never_validated" as const,
      usable: false,
      is_default: true,
    };
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if ((init?.method ?? "GET") === "GET") return json({ connections: [] });
      return json(created, 201);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderConnections();

    fireEvent.click(await screen.findByRole("button", { name: "Add connection" }));
    const dialog = await screen.findByRole("dialog", { name: "Add Firecrawl connection" });
    const token = screen.getByLabelText(/^API token/) as HTMLInputElement;
    expect(token).toHaveAttribute("autocomplete", "new-password");
    expect(token).toHaveValue("");
    fireEvent.change(screen.getByLabelText(/^Name/), {
      target: { value: "Primary Cloud" },
    });
    fireEvent.change(token, { target: { value: "create-test-only-token" } });
    fireEvent.click(within(dialog).getByRole("switch", { name: "Make default" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Save connection" }));

    expect(await screen.findByText("Primary Cloud")).toBeVisible();
    await waitFor(() => expect(dialog).not.toBeVisible());
    expect(token).toHaveValue("");
    const [, init] = fetchMock.mock.calls.find(([, options]) => options?.method === "POST")!;
    expect(JSON.parse(String(init?.body))).toEqual({
      name: "Primary Cloud",
      connection_type: "cloud",
      credential: "create-test-only-token",
      is_default: true,
    });
  });

  it("clears a token after a failed save and keeps a safe inline error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) =>
        (init?.method ?? "GET") === "GET"
          ? json({ connections: [] })
          : json({ detail: "unsafe provider detail" }, 503),
      ),
    );
    renderConnections();

    fireEvent.click(await screen.findByRole("button", { name: "Add connection" }));
    const dialog = await screen.findByRole("dialog", { name: "Add Firecrawl connection" });
    fireEvent.change(screen.getByLabelText(/^Name/), {
      target: { value: "Primary Cloud" },
    });
    const token = screen.getByLabelText(/^API token/);
    fireEvent.change(token, { target: { value: "failed-test-only-token" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save connection" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "The Firecrawl connection could not be created.",
    );
    expect(token).toHaveValue("");
    expect(screen.queryByText("unsafe provider detail")).not.toBeInTheDocument();
  });

  it("offers every owner management action through focused confirmations", async () => {
    const calls: Array<{ path: string; method: string; body: unknown }> = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      const method = init?.method ?? "GET";
      calls.push({
        path,
        method,
        body: typeof init?.body === "string" ? JSON.parse(init.body) : null,
      });
      if (method === "GET") return json({ connections: [connection] });
      if (method === "DELETE") return new Response(null, { status: 204 });
      if (path.endsWith("/credential")) {
        return json({ ...connection, status: "never_validated", usable: false });
      }
      if (path.endsWith("/test")) return json(connection);
      const body = typeof init?.body === "string" ? JSON.parse(init.body) : {};
      if ("base_url" in body) {
        return json({
          ...connection,
          name: body.name,
          normalized_base_url: body.base_url,
        });
      }
      return json({ ...connection, ...body });
    });
    vi.stubGlobal("fetch", fetchMock);
    renderConnections();
    const row = await screen.findByRole("article", { name: "Research Firecrawl" });

    fireEvent.click(within(row).getByRole("button", { name: "Test connection" }));
    await waitFor(() => expect(calls.some((call) => call.path.endsWith("/test"))).toBe(true));

    fireEvent.click(within(row).getByRole("button", { name: "Edit connection" }));
    const editDialog = await screen.findByRole("dialog", { name: "Edit Firecrawl connection" });
    fireEvent.change(screen.getByLabelText(/^Name/), {
      target: { value: "Research Firecrawl 2" },
    });
    fireEvent.change(screen.getByLabelText(/^Remote HTTPS origin/), {
      target: { value: "https://firecrawl-2.research.example" },
    });
    fireEvent.click(within(editDialog).getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(editDialog).not.toBeVisible());

    fireEvent.click(within(row).getByRole("button", { name: "Make default" }));
    await waitFor(() =>
      expect(calls.some((call) => JSON.stringify(call.body) === '{"is_default":true}')).toBe(true),
    );
    fireEvent.click(within(row).getByRole("button", { name: "Disable connection" }));
    await waitFor(() =>
      expect(calls.some((call) => JSON.stringify(call.body) === '{"enabled":false}')).toBe(true),
    );

    fireEvent.click(within(row).getByRole("button", { name: "Rotate token" }));
    const rotateDialog = await screen.findByRole("dialog", { name: "Rotate API token" });
    const replacement = screen.getByLabelText(/^New API token/);
    fireEvent.change(replacement, { target: { value: "rotate-test-only-token" } });
    fireEvent.click(within(rotateDialog).getByRole("button", { name: "Replace token" }));
    await waitFor(() => expect(rotateDialog).not.toBeVisible());
    expect(replacement).toHaveValue("");

    fireEvent.click(within(row).getByRole("button", { name: "Delete connection" }));
    const confirmation = await screen.findByRole("dialog", {
      name: "Delete Firecrawl connection?",
    });
    fireEvent.click(within(confirmation).getByRole("button", { name: "Delete connection" }));

    await waitFor(() => {
      expect(calls).toEqual(
        expect.arrayContaining([
          expect.objectContaining({ method: "POST", path: expect.stringMatching(/\/test$/) }),
          expect.objectContaining({
            method: "PATCH",
            body: {
              name: "Research Firecrawl 2",
              base_url: "https://firecrawl-2.research.example",
            },
          }),
          expect.objectContaining({
            method: "PUT",
            body: { credential: "rotate-test-only-token" },
          }),
          expect.objectContaining({ method: "PATCH", body: { enabled: false } }),
          expect.objectContaining({ method: "PATCH", body: { is_default: true } }),
          expect.objectContaining({ method: "DELETE" }),
        ]),
      );
    });
  });

  it("shows members a safe read-only view without endpoints or controls", async () => {
    const safeMemberConnection = { ...connection };
    delete (safeMemberConnection as Partial<typeof connection>).normalized_base_url;
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(json({ connections: [safeMemberConnection] })),
    );
    renderConnections(memberSession);

    expect(await screen.findByText("Research Firecrawl")).toBeVisible();
    expect(screen.getByText("Workspace owners manage connection settings.")).toBeVisible();
    expect(screen.queryByText("https://firecrawl.research.example")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add connection" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Test connection" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/API token/)).not.toBeInTheDocument();
  });
});
