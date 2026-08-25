import {
  Alert,
  AppShell,
  Badge,
  Button,
  Card,
  Container,
  Group,
  Loader,
  SimpleGrid,
  Stack,
  Text,
  Title,
} from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { loadHealth } from "./api/health";
import {
  loadSessionStatus,
  logoutSession,
  pairSession,
  type SessionStatus,
} from "./api/session";
import { DiscoveryScreen } from "./DiscoveryScreen";
import { PairingScreen } from "./PairingScreen";

const statusColor: Record<string, string> = {
  available: "green",
  degraded: "yellow",
  unavailable: "red",
  unsupported: "gray",
  not_configured: "blue.9",
};

export function App() {
  const queryClient = useQueryClient();
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
    onSuccess: () => {
      queryClient.removeQueries({ queryKey: ["health"] });
      queryClient.setQueryData<SessionStatus>(["session"], { status: "pairing_required" });
    },
  });

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

  if (session.data?.status !== "authenticated") {
    return <PairingScreen onPair={(code) => pairing.mutateAsync(code).then(() => undefined)} />;
  }

  return (
    <AppShell header={{ height: 64 }} padding="md">
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between">
          <Text fw={700}>Parserium Collector</Text>
          {health.data ? (
            <Badge autoContrast color={health.data.overall === "ready" ? "green" : "yellow"}>
              {health.data.overall}
            </Badge>
          ) : null}
          <Button
            variant="subtle"
            onClick={() => logout.mutate()}
            loading={logout.isPending}
          >
            Log out
          </Button>
        </Group>
      </AppShell.Header>
      <AppShell.Main>
        <Container size="lg">
          <Stack gap="lg" aria-busy={health.isLoading}>
            <DiscoveryScreen csrfToken={session.data.csrf_token} />
            <div>
              <Title order={1}>System health</Title>
              <Text>Foundation status only. Collection capabilities remain gated.</Text>
            </div>
            {health.isLoading ? (
              <Group role="status">
                <Loader size="sm" />
                <Text>Checking local services</Text>
              </Group>
            ) : null}
            {health.isError ? (
              <Alert color="red" title="Health check unavailable">
                The dashboard API did not return a valid health response.
              </Alert>
            ) : null}
            {health.data ? (
              <>
                <Text size="sm">Build: {health.data.build_id}</Text>
                <SimpleGrid cols={{ base: 1, sm: 2, lg: 3 }}>
                  {health.data.components.map((component) => (
                    <Card key={component.name} withBorder>
                      <Stack gap="xs">
                        <Group justify="space-between">
                          <Text fw={600}>{component.name}</Text>
                          <Badge autoContrast color={statusColor[component.status] ?? "gray"}>
                            {component.status}
                          </Badge>
                        </Group>
                        {component.detail ? (
                          <Text size="sm">{component.detail}</Text>
                        ) : null}
                      </Stack>
                    </Card>
                  ))}
                </SimpleGrid>
              </>
            ) : null}
          </Stack>
        </Container>
      </AppShell.Main>
    </AppShell>
  );
}
