import { Group, Text, UnstyledButton } from "@mantine/core";
import { type ReactNode } from "react";

import type { HealthStatus } from "./api/health";
import type { AuthenticatedSession } from "./api/session";
import { OperationalIcon } from "./OperationalIcon";
import { ParseriumShell } from "./ParseriumShell";
import { WorkspaceMenu } from "./WorkspaceMenu";

export type DashboardDestination =
  | "discover"
  | "queue"
  | "documents"
  | "connections"
  | "health";

interface DashboardShellProps {
  children: ReactNode;
  health: HealthStatus | undefined;
  healthLoading: boolean;
  healthError: boolean;
  session: AuthenticatedSession;
  destination?: DashboardDestination;
  onDestinationChange?: (destination: DashboardDestination) => void;
  onWorkspaceChanged?: (session: AuthenticatedSession) => void | Promise<void>;
}

const primaryNavigation: ReadonlyArray<{
  label: string;
  destination: Exclude<DashboardDestination, "health">;
}> = [
  { label: "Collect", destination: "discover" },
  { label: "Documents", destination: "documents" },
  { label: "Activity", destination: "queue" },
];

export function DashboardShell({
  children,
  health,
  healthLoading,
  healthError,
  session,
  destination = "discover",
  onDestinationChange = () => undefined,
  onWorkspaceChanged = () => undefined,
}: DashboardShellProps) {
  return (
    <ParseriumShell
      destination={destination}
      navigation={primaryNavigation}
      onDestinationChange={(value) => onDestinationChange(value as DashboardDestination)}
      headerActions={
        <>
          <WorkspaceMenu session={session} onSwitched={onWorkspaceChanged} />
          {session.authentication_mode === "oidc" ? (
            <UnstyledButton
              className="workspace-connections-button"
              aria-label="Connections"
              title="Firecrawl connections"
              data-active={destination === "connections" || undefined}
              aria-current={destination === "connections" ? "page" : undefined}
              onClick={() => onDestinationChange("connections")}
            >
              <OperationalIcon name="settings" size={20} />
              <span>Connections</span>
            </UnstyledButton>
          ) : null}
          <Group className="system-strip" gap="md" wrap="nowrap" role="status">
            {healthLoading ? <Text size="xs">Checking system</Text> : null}
            {healthError ? <Text size="xs">Status unavailable</Text> : null}
            {health ? (
              <Text
                className="overall-status"
                data-tone={health.overall === "ready" ? "ready" : "warning"}
                size="xs"
              >
                <span className="status-dot" aria-hidden="true" />
                {health.overall === "ready" ? "System ready" : "System degraded"}
              </Text>
            ) : null}
          </Group>
          <UnstyledButton
            className="dashboard-health-button"
            data-active={destination === "health" || undefined}
            aria-current={destination === "health" ? "page" : undefined}
            onClick={() => onDestinationChange("health")}
          >
            Health
          </UnstyledButton>
        </>
      }
    >
      {children}
    </ParseriumShell>
  );
}
