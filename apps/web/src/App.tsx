import { Alert, Container, Drawer, Group, Loader, Text } from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { loadHealth } from "./api/health";
import {
  loadSessionStatus,
  logoutSession,
  pairSession,
  type AuthenticatedSession,
  type SessionStatus,
} from "./api/session";
import { DiscoveryScreen } from "./DiscoveryScreen";
import { CollectionQueue } from "./CollectionQueue";
import { ConnectionsScreen } from "./ConnectionsScreen";
import { DashboardShell, type DashboardDestination } from "./DashboardShell";
import { LoginScreen } from "./LoginScreen";
import { PairingScreen } from "./PairingScreen";
import { QueueScreen } from "./QueueScreen";
import { SystemHealthPanel } from "./SystemHealthPanel";

const workspaceQueryKeys = [
  ["analysis-search"],
  ["activity"],
  ["collection-jobs"],
  ["documents"],
  ["exports"],
  ["firecrawl-connections"],
  ["health"],
] as const;

export function App() {
  const queryClient = useQueryClient();
  const [destination, setDestination] = useState<DashboardDestination>("discover");
  const session = useQuery({
    queryKey: ["session"],
    queryFn: loadSessionStatus,
  });
  const health = useQuery({
    queryKey: ["health"],
    queryFn: loadHealth,
    refetchInterval: 10_000,
    enabled: session.data?.status === "authenticated",
  });
  const pairing = useMutation({
    mutationFn: pairSession,
    onSuccess: (authenticated) => {
      queryClient.setQueryData<SessionStatus>(["session"], authenticated);
    },
  });
  const logout = useMutation({
    mutationFn: async () => {
      if (session.data?.status !== "authenticated") return;
      await logoutSession(session.data.csrf_token);
    },
    onSuccess: async () => {
      for (const queryKey of workspaceQueryKeys) {
        queryClient.removeQueries({ queryKey });
      }
      await queryClient.resetQueries({ queryKey: ["session"], exact: true });
    },
  });

  useEffect(() => {
    if (destination !== "discover") return;
    const discovery = document.getElementById("discover");
    discovery?.scrollIntoView?.({ block: "start" });
    discovery?.focus({ preventScroll: true });
  }, [destination]);

  if (session.isLoading) {
    return (
      <Container size="sm" py="xl">
        <Group role="status">
          <Loader size="sm" />
          <Text>Checking browser pairing</Text>
        </Group>
      </Container>
    );
  }

  if (session.isError) {
    return (
      <Container size="sm" py="xl">
        <Alert color="red" title="Session check unavailable">
          The dashboard API did not return a valid session response.
        </Alert>
      </Container>
    );
  }

  if (session.data?.status === "login_required") {
    return (
      <LoginScreen
        loginUrl={session.data.login_url}
        providerLabel={session.data.provider_label}
      />
    );
  }

  if (session.data?.status === "pairing_required") {
    return <PairingScreen onPair={(code) => pairing.mutateAsync(code).then(() => undefined)} />;
  }

  if (session.data?.status !== "authenticated") return null;

  const authenticatedSession = session.data;

  function workspaceChanged(nextSession: AuthenticatedSession) {
    setDestination("discover");
    for (const queryKey of workspaceQueryKeys) {
      queryClient.removeQueries({ queryKey });
    }
    queryClient.setQueryData<SessionStatus>(["session"], nextSession);
  }

  function selectDestination(nextDestination: DashboardDestination) {
    setDestination(nextDestination);
  }

  return (
    <DashboardShell
      health={health.data}
      healthLoading={health.isLoading}
      healthError={health.isError}
      session={authenticatedSession}
      destination={destination}
      onDestinationChange={selectDestination}
      onWorkspaceChanged={workspaceChanged}
    >
      <div hidden={destination === "queue" || destination === "connections"}>
        <div className="operations-grid" aria-busy={health.isLoading}>
          <DiscoveryScreen
            key={authenticatedSession.workspace.id}
            csrfToken={authenticatedSession.csrf_token}
            firecrawl={
              authenticatedSession.authentication_mode === "oidc"
                ? {
                    workspaceId: authenticatedSession.workspace.id,
                    role: authenticatedSession.workspace.role,
                    onConfigure: () => setDestination("connections"),
                  }
                : undefined
            }
          />
          <CollectionQueue
            csrfToken={authenticatedSession.csrf_token}
            activeDestination={destination}
            onDestinationClose={() => setDestination("discover")}
          />
        </div>
      </div>
      {destination === "connections" && authenticatedSession.authentication_mode === "oidc" ? (
        <ConnectionsScreen session={authenticatedSession} />
      ) : destination === "queue" ? (
        <QueueScreen csrfToken={authenticatedSession.csrf_token} />
      ) : null}
      <Drawer
        opened={destination === "health"}
        onClose={() => setDestination("discover")}
        title="System health"
        position="right"
        size="lg"
        closeButtonProps={{ "aria-label": "Close system health" }}
        classNames={{ content: "parserium-drawer", header: "parserium-drawer-header" }}
      >
        <SystemHealthPanel
          health={health.data}
          loading={health.isLoading}
          error={health.isError}
          logoutPending={logout.isPending}
          onLogout={() => logout.mutate()}
        />
      </Drawer>
    </DashboardShell>
  );
}
