import {
  Alert,
  Badge,
  Box,
  Button,
  Group,
  Loader,
  SimpleGrid,
  Stack,
  Text,
  Title,
} from "@mantine/core";

import type { HealthStatus } from "./api/health";
import { OperationalIcon } from "./OperationalIcon";

interface SystemHealthPanelProps {
  health: HealthStatus | undefined;
  loading: boolean;
  error: boolean;
  logoutPending: boolean;
  onLogout: () => void;
}

const statusColors: Record<string, string> = {
  available: "green",
  degraded: "yellow",
  unavailable: "red",
  unsupported: "gray",
  not_configured: "blue",
};

export function SystemHealthPanel({
  health,
  loading,
  error,
  logoutPending,
  onLogout,
}: SystemHealthPanelProps) {
  return (
    <Box
      component="section"
      id="health"
      className="health-panel"
      aria-labelledby="health-title"
    >
      <Group justify="space-between" align="end">
        <div>
          <Text className="section-kicker">Local readiness</Text>
          <Title id="health-title" order={2}>
            System health
          </Title>
        </div>
        {health ? <Text className="machine-data">Build {health.build_id}</Text> : null}
      </Group>
      {loading ? (
        <Group role="status" mt="md">
          <Loader size="sm" />
          <Text>Checking local services</Text>
        </Group>
      ) : null}
      {error ? (
        <Alert role="alert" color="red" title="Health check unavailable" mt="md">
          The dashboard API did not return a valid health response.
        </Alert>
      ) : null}
      {health ? (
        <SimpleGrid cols={{ base: 1, sm: 2, lg: 3 }} mt="md" spacing="xs">
          {health.components.map((component) => (
            <Stack key={component.name} className="health-component" gap={6}>
              <Group justify="space-between" wrap="nowrap">
                <Text fw={600}>{component.name}</Text>
                <Badge
                  color={statusColors[component.status] ?? "gray"}
                  variant="light"
                  radius="xs"
                >
                  {component.status}
                </Badge>
              </Group>
              {component.detail ? (
                <Text size="sm" className="health-detail">
                  {component.detail}
                </Text>
              ) : (
                <Text size="sm" className="health-detail">
                  No additional detail
                </Text>
              )}
            </Stack>
          ))}
        </SimpleGrid>
      ) : null}
      <div className="health-session">
        <div>
          <Text className="section-kicker">Browser session</Text>
          <Text size="sm" className="health-detail">
            End this browser's paired access to the local dashboard.
          </Text>
        </div>
        <Button
          variant="default"
          color="gray"
          leftSection={<OperationalIcon name="logout" size={17} />}
          onClick={onLogout}
          loading={logoutPending}
        >
          Log out
        </Button>
      </div>
    </Box>
  );
}
