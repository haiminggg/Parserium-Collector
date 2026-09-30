import { MantineProvider } from "@mantine/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DashboardShell } from "./DashboardShell";
import { parseriumTheme } from "./theme";

const authenticatedSession = {
  status: "authenticated" as const,
  csrf_token: "csrf-token",
  idle_expires_at: "2026-08-30T12:00:00Z",
  authentication_mode: "local" as const,
  user: null,
  workspace: {
    id: "00000000-0000-4000-8000-000000000001",
    name: "Local workspace",
    role: "owner" as const,
  },
  workspaces: [
    {
      id: "00000000-0000-4000-8000-000000000001",
      name: "Local workspace",
      role: "owner" as const,
    },
  ],
};

afterEach(cleanup);

function renderShell(
  props: Partial<React.ComponentProps<typeof DashboardShell>> = {},
) {
  return render(
    <MantineProvider theme={parseriumTheme} forceColorScheme="light">
      <DashboardShell
        health={{
          overall: "ready",
          build_id: "test-build",
          components: [
            { name: "database", status: "available", detail: null, required: true },
            { name: "worker", status: "available", detail: null, required: true },
          ],
        }}
        healthLoading={false}
        healthError={false}
        session={authenticatedSession}
        {...props}
      >
        <main>Workspace</main>
      </DashboardShell>
    </MantineProvider>,
  );
}

describe("DashboardShell", () => {
  it("labels the console workspace and primary navigation", () => {
    renderShell();

    expect(
      screen.getByRole("main", { name: "Document discovery console" }),
    ).toBeVisible();
    expect(screen.getByRole("navigation", { name: "Primary navigation" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Collect" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.queryByText("Local console")).not.toBeInTheDocument();
  });

  it("provides top-level destinations and reports only overall readiness", () => {
    renderShell();

    expect(screen.getByRole("button", { name: "Collect" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Activity" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Documents" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Health" })).toBeVisible();
    expect(screen.queryByText("database available")).not.toBeInTheDocument();
    expect(screen.queryByText("worker available")).not.toBeInTheDocument();
    expect(screen.getByText("System ready")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Log out" })).not.toBeInTheDocument();
  });

  it("shows Connections only for hosted authenticated sessions", () => {
    const hostedSession = {
      ...authenticatedSession,
      authentication_mode: "oidc" as const,
      user: {
        id: "20000000-0000-4000-8000-000000000061",
        email: "owner@parserium.test",
        display_name: "Workspace owner",
      },
    };
    const { rerender } = renderShell({ session: hostedSession });

    expect(screen.getByRole("button", { name: "Connections" })).toBeVisible();

    rerender(
      <MantineProvider theme={parseriumTheme} forceColorScheme="light">
        <DashboardShell
          health={undefined}
          healthLoading={false}
          healthError={false}
          session={authenticatedSession}
        >
          <main>Workspace</main>
        </DashboardShell>
      </MantineProvider>,
    );
    expect(screen.queryByRole("button", { name: "Connections" })).not.toBeInTheDocument();
  });

  it("does not claim readiness when health is unavailable", () => {
    renderShell({ health: undefined, healthError: true });

    expect(screen.getByText("Status unavailable")).toBeVisible();
    expect(screen.queryByText("System ready")).not.toBeInTheDocument();
  });

  it("invokes navigation destinations", () => {
    const onDestinationChange = vi.fn();
    renderShell({ onDestinationChange });

    fireEvent.click(screen.getByRole("button", { name: "Health" }));
    expect(onDestinationChange).toHaveBeenCalledWith("health");
  });

  it("keeps supported destinations and provides a keyboard skip target", () => {
    const selected: string[] = [];
    renderShell({ destination: "queue", onDestinationChange: (value) => selected.push(value) });
    expect(screen.getByRole("button", { name: "Activity" })).toHaveAttribute("aria-current", "page");
    fireEvent.click(screen.getByRole("button", { name: "Collect" }));
    expect(selected).toEqual(["discover"]);
    expect(screen.getByRole("link", { name: "Skip to workspace" })).toHaveAttribute("href", "#workspace-content");
    expect(screen.getByRole("main", { name: "Document discovery console" })).toHaveAttribute("id", "workspace-content");
    expect(screen.queryByRole("button", { name: "Profiles" })).not.toBeInTheDocument();
  });
});
