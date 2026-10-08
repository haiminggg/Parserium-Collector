import { MantineProvider } from "@mantine/core";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AuthenticatedSession } from "./api/session";
import { WorkspaceMenu } from "./WorkspaceMenu";

const session: AuthenticatedSession = {
  status: "authenticated",
  csrf_token: "csrf-token",
  idle_expires_at: "2026-08-30T12:00:00Z",
  authentication_mode: "oidc",
  user: {
    id: "20000000-0000-4000-8000-000000000001",
    email: "owner@example.com",
    display_name: "Owner",
  },
  workspace: {
    id: "10000000-0000-4000-8000-000000000001",
    name: "Northbridge",
    role: "owner",
  },
  workspaces: [
    {
      id: "10000000-0000-4000-8000-000000000001",
      name: "Northbridge",
      role: "owner",
    },
    {
      id: "10000000-0000-4000-8000-000000000002",
      name: "Meridian",
      role: "member",
    },
  ],
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function renderMenu(onSwitched = vi.fn()) {
  render(
    <MantineProvider>
      <WorkspaceMenu session={session} onSwitched={onSwitched} />
    </MantineProvider>,
  );
  return onSwitched;
}

describe("WorkspaceMenu", () => {
  it("switches workspace with the in-memory CSRF token", async () => {
    const switched = { ...session, workspace: session.workspaces[1] };
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(switched), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const onSwitched = renderMenu();

    fireEvent.click(screen.getByRole("button", { name: /Northbridge/ }));
    fireEvent.click(await screen.findByRole("menuitemradio", { name: /Meridian/ }));

    await waitFor(() => expect(onSwitched).toHaveBeenCalledWith(switched));
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/session/workspace", {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-Parserium-CSRF": "csrf-token",
      },
      credentials: "same-origin",
      body: JSON.stringify({
        workspace_id: "10000000-0000-4000-8000-000000000002",
      }),
    });
  });

  it("keeps the active workspace visible after a failed switch", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 500 })));
    const onSwitched = renderMenu();

    fireEvent.click(screen.getByRole("button", { name: /Northbridge/ }));
    fireEvent.click(await screen.findByRole("menuitemradio", { name: /Meridian/ }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Workspace could not be switched.",
    );
    expect(screen.getByRole("button", { name: /Northbridge/ })).toBeVisible();
    expect(onSwitched).not.toHaveBeenCalled();
  });

  it("renders one workspace as non-interactive text", () => {
    render(
      <MantineProvider>
        <WorkspaceMenu
          session={{ ...session, workspaces: [session.workspace] }}
          onSwitched={vi.fn()}
        />
      </MantineProvider>,
    );

    expect(screen.getByText("Northbridge")).toBeVisible();
    expect(screen.queryByRole("button", { name: /Northbridge/ })).not.toBeInTheDocument();
  });
});
