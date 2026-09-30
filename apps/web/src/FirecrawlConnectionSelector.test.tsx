import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FirecrawlConnectionSelector } from "./FirecrawlConnectionSelector";
import type { FirecrawlConnectionSummary } from "./api/firecrawlConnections";

const healthyDefault: FirecrawlConnectionSummary = {
  id: "30000000-0000-4000-8000-000000000061",
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

const healthyOverride: FirecrawlConnectionSummary = {
  ...healthyDefault,
  id: "30000000-0000-4000-8000-000000000062",
  name: "Research remote connection with a deliberately long display name",
  connection_type: "remote",
  is_default: false,
};

const unavailableConnections: FirecrawlConnectionSummary[] = [
  {
    ...healthyDefault,
    id: "30000000-0000-4000-8000-000000000063",
    name: "Degraded remote",
    status: "degraded",
    is_default: false,
    usable: false,
  },
  {
    ...healthyDefault,
    id: "30000000-0000-4000-8000-000000000064",
    name: "Disabled cloud",
    status: "disabled",
    enabled: false,
    is_default: false,
    usable: false,
  },
  {
    ...healthyDefault,
    id: "30000000-0000-4000-8000-000000000065",
    name: "Never validated",
    status: "never_validated",
    is_default: false,
    usable: false,
  },
];

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function json(connections: FirecrawlConnectionSummary[]) {
  return new Response(JSON.stringify({ connections }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function renderSelector(
  role: "owner" | "member" = "owner",
  onConfigure = vi.fn(),
) {
  const usability = vi.fn();
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });

  function Harness() {
    const [value, setValue] = useState<string | null>(null);
    return (
      <FirecrawlConnectionSelector
        workspaceId="10000000-0000-4000-8000-000000000061"
        role={role}
        value={value}
        onChange={setValue}
        onUsabilityChange={usability}
        onConfigure={onConfigure}
      />
    );
  }

  render(
    <MantineProvider>
      <QueryClientProvider client={queryClient}>
        <Harness />
      </QueryClientProvider>
    </MantineProvider>,
  );
  return { onConfigure, usability };
}

describe("FirecrawlConnectionSelector", () => {
  it("selects the healthy default and disables every unavailable status", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        json([healthyOverride, ...unavailableConnections, healthyDefault]),
      ),
    );
    const { usability } = renderSelector();

    const select = await screen.findByRole("combobox", { name: "Firecrawl connection" });
    await waitFor(() => expect(select).toHaveValue(healthyDefault.id));
    expect(screen.getByRole("option", { name: /Degraded remote.*Degraded/ })).toBeDisabled();
    expect(screen.getByRole("option", { name: /Disabled cloud.*Disabled/ })).toBeDisabled();
    expect(screen.getByRole("option", { name: /Never validated.*Needs validation/ })).toBeDisabled();
    expect(screen.getByRole("option", { name: /Research remote connection/ })).toHaveAttribute(
      "title",
      healthyOverride.name,
    );
    expect(usability).toHaveBeenLastCalledWith(true);
  });

  it("explains an unusable workspace and gives only owners a configuration action", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(unavailableConnections)));
    const owner = renderSelector("owner");

    expect(await screen.findByText("No usable connection")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Configure connections" }));
    expect(owner.onConfigure).toHaveBeenCalledOnce();
    expect(owner.usability).toHaveBeenLastCalledWith(false);

    cleanup();
    const member = renderSelector("member");
    expect(await screen.findByText("Ask a workspace owner to validate a connection.")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Configure connections" })).not.toBeInTheDocument();
    expect(member.usability).toHaveBeenLastCalledWith(false);
  });
});
