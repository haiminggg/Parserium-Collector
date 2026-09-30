import {
  Alert,
  Button,
  Drawer,
  Group,
  Modal,
  PasswordInput,
  SegmentedControl,
  Skeleton,
  Switch,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";

import {
  createFirecrawlConnection,
  deleteFirecrawlConnection,
  listFirecrawlConnections,
  replaceFirecrawlCredential,
  testFirecrawlConnection,
  updateFirecrawlConnection,
  type FirecrawlConnectionSummary,
  type FirecrawlConnectionType,
  type UpdateFirecrawlConnectionRequest,
} from "./api/firecrawlConnections";
import type { AuthenticatedSession } from "./api/session";

interface ConnectionsScreenProps {
  session: AuthenticatedSession;
}

type DrawerMode = "create" | "edit" | "credential" | null;

const statusLabels = {
  never_validated: "Needs validation",
  healthy: "Healthy",
  degraded: "Degraded",
  disabled: "Disabled",
  deleted: "Deleted",
} as const;

function checkedAt(value: string | null): string {
  if (value === null) return "Not checked";
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "Not checked";
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

export function ConnectionsScreen({ session }: ConnectionsScreenProps) {
  const queryClient = useQueryClient();
  const queryKey = ["firecrawl-connections", session.workspace.id] as const;
  const canManage = session.workspace.role === "owner";
  const connections = useQuery({
    queryKey,
    queryFn: listFirecrawlConnections,
  });
  const [drawerMode, setDrawerMode] = useState<DrawerMode>(null);
  const [selected, setSelected] = useState<FirecrawlConnectionSummary | null>(null);
  const [name, setName] = useState("");
  const [connectionType, setConnectionType] =
    useState<FirecrawlConnectionType>("cloud");
  const [baseUrl, setBaseUrl] = useState("");
  const [credential, setCredential] = useState("");
  const [isDefault, setIsDefault] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<FirecrawlConnectionSummary | null>(null);

  function storeConnection(next: FirecrawlConnectionSummary) {
    queryClient.setQueryData<FirecrawlConnectionSummary[]>(queryKey, (current = []) => {
      const exists = current.some((item) => item.id === next.id);
      const normalized = current.map((item) => {
        if (item.id === next.id) return next;
        return next.is_default ? { ...item, is_default: false } : item;
      });
      return exists ? normalized : [...normalized, next];
    });
  }

  const createConnection = useMutation({
    mutationFn: (request: Parameters<typeof createFirecrawlConnection>[0]) =>
      createFirecrawlConnection(request, session.csrf_token),
    onSuccess: storeConnection,
  });
  const updateConnection = useMutation({
    mutationFn: ({
      connectionId,
      request,
    }: {
      connectionId: string;
      request: UpdateFirecrawlConnectionRequest;
    }) => updateFirecrawlConnection(connectionId, request, session.csrf_token),
    onSuccess: storeConnection,
  });
  const validateConnection = useMutation({
    mutationFn: (connectionId: string) =>
      testFirecrawlConnection(connectionId, session.csrf_token),
    onSuccess: storeConnection,
  });
  const rotateCredential = useMutation({
    mutationFn: ({ connectionId, token }: { connectionId: string; token: string }) =>
      replaceFirecrawlCredential(connectionId, token, session.csrf_token),
    onSuccess: storeConnection,
  });
  const removeConnection = useMutation({
    mutationFn: (connectionId: string) =>
      deleteFirecrawlConnection(connectionId, session.csrf_token),
    onSuccess: (_value, connectionId) => {
      queryClient.setQueryData<FirecrawlConnectionSummary[]>(queryKey, (current = []) =>
        current.filter((item) => item.id !== connectionId),
      );
    },
  });

  function resetDrawer() {
    setDrawerMode(null);
    setSelected(null);
    setName("");
    setConnectionType("cloud");
    setBaseUrl("");
    setCredential("");
    setIsDefault(false);
    setActionError(null);
  }

  function openCreate() {
    resetDrawer();
    setDrawerMode("create");
  }

  function openEdit(connection: FirecrawlConnectionSummary) {
    resetDrawer();
    setSelected(connection);
    setName(connection.name);
    setConnectionType(connection.connection_type);
    setBaseUrl(connection.normalized_base_url ?? "");
    setDrawerMode("edit");
  }

  function openCredential(connection: FirecrawlConnectionSummary) {
    resetDrawer();
    setSelected(connection);
    setDrawerMode("credential");
  }

  async function submitDrawer(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setActionError(null);
    try {
      if (drawerMode === "create") {
        const token = credential;
        setCredential("");
        await createConnection.mutateAsync({
          name,
          connection_type: connectionType,
          ...(connectionType === "remote" ? { base_url: baseUrl } : {}),
          credential: token,
          is_default: isDefault,
        });
      } else if (drawerMode === "edit" && selected !== null) {
        await updateConnection.mutateAsync({
          connectionId: selected.id,
          request: {
            name,
            ...(selected.connection_type === "remote" ? { base_url: baseUrl } : {}),
          },
        });
      } else if (drawerMode === "credential" && selected !== null) {
        const token = credential;
        setCredential("");
        await rotateCredential.mutateAsync({ connectionId: selected.id, token });
      } else {
        return;
      }
      resetDrawer();
    } catch (error) {
      setActionError(
        error instanceof Error ? error.message : "The Firecrawl connection request failed.",
      );
    }
  }

  async function patchConnection(
    connection: FirecrawlConnectionSummary,
    request: UpdateFirecrawlConnectionRequest,
  ) {
    setActionError(null);
    try {
      await updateConnection.mutateAsync({ connectionId: connection.id, request });
    } catch (error) {
      setActionError(
        error instanceof Error ? error.message : "The Firecrawl connection request failed.",
      );
    }
  }

  async function confirmDelete() {
    if (deleteTarget === null) return;
    setActionError(null);
    try {
      await removeConnection.mutateAsync(deleteTarget.id);
      setDeleteTarget(null);
    } catch (error) {
      setActionError(
        error instanceof Error ? error.message : "The Firecrawl connection request failed.",
      );
    }
  }

  const drawerTitle =
    drawerMode === "create"
      ? "Add Firecrawl connection"
      : drawerMode === "edit"
        ? "Edit Firecrawl connection"
        : "Rotate API token";
  const drawerPending =
    createConnection.isPending || updateConnection.isPending || rotateCredential.isPending;

  return (
    <main className="connections-page" aria-labelledby="connections-title">
      <section className="connections-frame">
        <header className="connections-heading">
          <div>
            <Text className="section-kicker">Workspace transport</Text>
            <Title order={1} id="connections-title">
              Firecrawl connections
            </Title>
            <Text className="connections-introduction">
              Choose which Firecrawl service this workspace uses to discover documents.
            </Text>
          </div>
          {canManage ? (
            <Button className="connections-primary-action" onClick={openCreate}>
              Add connection
            </Button>
          ) : (
            <Text className="connections-member-note">
              Workspace owners manage connection settings.
            </Text>
          )}
        </header>

        {connections.isError ? (
          <Alert role="alert" color="red" title="Connections unavailable">
            Parserium could not load this workspace's Firecrawl connections.
          </Alert>
        ) : null}
        {actionError !== null && drawerMode === null ? (
          <Alert role="alert" color="red" title="Connection action failed">
            {actionError}
          </Alert>
        ) : null}

        {connections.isLoading ? (
          <div className="connections-loading" role="status" aria-label="Loading connections">
            <Skeleton height={92} radius={0} />
            <Skeleton height={92} radius={0} />
          </div>
        ) : null}

        {connections.data?.length === 0 ? (
          <section className="connections-empty" aria-label="No Firecrawl connections">
            <Text fw={620}>No connection is configured for this workspace.</Text>
            <Text size="sm">
              {canManage
                ? "Add a Cloud or remote connection before running hosted discovery."
                : "Ask a workspace owner to configure and validate a connection."}
            </Text>
          </section>
        ) : null}

        {connections.data && connections.data.length > 0 ? (
          <section className="connection-ledger" aria-label="Workspace Firecrawl connections">
            <div className="connection-ledger-header" aria-hidden="true">
              <span>Connection</span>
              <span>Status</span>
              <span>Last checked</span>
              {canManage ? <span>Actions</span> : null}
            </div>
            {connections.data.map((connection) => (
              <article
                key={connection.id}
                className="connection-row"
                aria-label={connection.name}
              >
                <div className="connection-identity">
                  <Group gap="xs" wrap="wrap">
                    <Text fw={640}>{connection.name}</Text>
                    {connection.is_default ? (
                      <span className="connection-default-label">Default</span>
                    ) : null}
                  </Group>
                  <Text className="machine-data">
                    {connection.connection_type === "cloud" ? "Firecrawl Cloud" : "Remote"}
                  </Text>
                  {canManage && connection.normalized_base_url ? (
                    <Text className="connection-endpoint" title={connection.normalized_base_url}>
                      {connection.normalized_base_url}
                    </Text>
                  ) : null}
                </div>
                <div>
                  <span className="connection-status" data-tone={connection.status}>
                    {statusLabels[connection.status]}
                  </span>
                  {connection.last_failure_category ? (
                    <Text className="machine-data connection-failure">
                      {connection.last_failure_category.replaceAll("_", " ")}
                    </Text>
                  ) : null}
                </div>
                <Text className="machine-data connection-checked">
                  {checkedAt(connection.last_validation_attempt_at)}
                </Text>
                {canManage ? (
                  <div className="connection-actions">
                    <Button
                      variant="subtle"
                      size="compact-sm"
                      loading={
                        validateConnection.isPending &&
                        validateConnection.variables === connection.id
                      }
                      onClick={() => validateConnection.mutate(connection.id)}
                    >
                      Test connection
                    </Button>
                    <Button variant="subtle" size="compact-sm" onClick={() => openEdit(connection)}>
                      Edit connection
                    </Button>
                    <Button
                      variant="subtle"
                      size="compact-sm"
                      onClick={() => openCredential(connection)}
                    >
                      Rotate token
                    </Button>
                    <Button
                      variant="subtle"
                      size="compact-sm"
                      onClick={() =>
                        void patchConnection(connection, { enabled: !connection.enabled })
                      }
                    >
                      {connection.enabled ? "Disable connection" : "Enable connection"}
                    </Button>
                    <Button
                      variant="subtle"
                      size="compact-sm"
                      disabled={connection.is_default || !connection.usable}
                      onClick={() => void patchConnection(connection, { is_default: true })}
                    >
                      Make default
                    </Button>
                    <Button
                      variant="subtle"
                      color="red"
                      size="compact-sm"
                      onClick={() => setDeleteTarget(connection)}
                    >
                      Delete connection
                    </Button>
                  </div>
                ) : null}
              </article>
            ))}
          </section>
        ) : null}
      </section>

      <Drawer
        opened={drawerMode !== null}
        onClose={() => {
          if (!drawerPending) resetDrawer();
        }}
        title={drawerTitle}
        position="right"
        size="md"
        closeOnClickOutside={!drawerPending}
        closeOnEscape={!drawerPending}
        closeButtonProps={{ "aria-label": "Close connection editor", disabled: drawerPending }}
        classNames={{ content: "parserium-drawer", header: "parserium-drawer-header" }}
      >
        <form className="connection-form" onSubmit={(event) => void submitDrawer(event)}>
          {actionError !== null ? (
            <Alert role="alert" color="red">
              {actionError}
            </Alert>
          ) : null}
          {drawerMode === "credential" ? (
            <PasswordInput
              required
              label="New API token"
              description="The saved token is never shown in the dashboard."
              value={credential}
              autoComplete="new-password"
              onChange={(event) => setCredential(event.currentTarget.value)}
            />
          ) : (
            <>
              <TextInput
                required
                label="Name"
                value={name}
                maxLength={120}
                onChange={(event) => setName(event.currentTarget.value)}
              />
              {drawerMode === "create" ? (
                <fieldset className="connection-type-fieldset">
                  <legend>Connection type</legend>
                  <SegmentedControl
                    fullWidth
                    value={connectionType}
                    data={[
                      { label: "Cloud", value: "cloud" },
                      { label: "Remote", value: "remote" },
                    ]}
                    onChange={(value) => setConnectionType(value as FirecrawlConnectionType)}
                  />
                </fieldset>
              ) : null}
              {connectionType === "remote" ? (
                <TextInput
                  required
                  type="url"
                  label="Remote HTTPS origin"
                  description="Use an allowed HTTPS origin without a path or query."
                  placeholder="https://firecrawl.example.com"
                  value={baseUrl}
                  onChange={(event) => setBaseUrl(event.currentTarget.value)}
                />
              ) : null}
              {drawerMode === "create" ? (
                <>
                  <PasswordInput
                    required
                    label="API token"
                    description="Parserium encrypts this token before storage and never returns it."
                    value={credential}
                    autoComplete="new-password"
                    onChange={(event) => setCredential(event.currentTarget.value)}
                  />
                  <Switch
                    label="Make default"
                    checked={isDefault}
                    onChange={(event) => setIsDefault(event.currentTarget.checked)}
                  />
                </>
              ) : null}
            </>
          )}
          <Text className="connection-credit-note" size="sm">
            Validation performs one metadata-only search and may consume one Firecrawl search
            credit.
          </Text>
          <Group justify="flex-end" gap="sm" className="connection-form-actions">
            <Button variant="default" disabled={drawerPending} onClick={resetDrawer}>
              Cancel
            </Button>
            <Button type="submit" loading={drawerPending}>
              {drawerMode === "create"
                ? "Save connection"
                : drawerMode === "edit"
                  ? "Save changes"
                  : "Replace token"}
            </Button>
          </Group>
        </form>
      </Drawer>

      <Modal
        opened={deleteTarget !== null}
        onClose={() => {
          if (!removeConnection.isPending) setDeleteTarget(null);
        }}
        title="Delete Firecrawl connection?"
        centered
        closeOnClickOutside={!removeConnection.isPending}
        closeOnEscape={!removeConnection.isPending}
        closeButtonProps={{
          "aria-label": "Close delete confirmation",
          disabled: removeConnection.isPending,
        }}
        classNames={{
          content: "document-delete-modal",
          header: "document-delete-modal-header",
          body: "document-delete-modal-body",
        }}
      >
        {deleteTarget ? (
          <div className="document-delete-confirmation">
            <Text>
              Delete <strong>{deleteTarget.name}</strong> and its stored credential?
            </Text>
            <Text size="sm" className="document-delete-warning">
              Searches cannot use this connection after deletion. This action cannot be undone
              from the dashboard.
            </Text>
            <Group justify="flex-end" gap="sm">
              <Button
                variant="default"
                disabled={removeConnection.isPending}
                onClick={() => setDeleteTarget(null)}
              >
                Cancel
              </Button>
              <Button
                color="red"
                loading={removeConnection.isPending}
                onClick={() => void confirmDelete()}
              >
                Delete connection
              </Button>
            </Group>
          </div>
        ) : null}
      </Modal>
    </main>
  );
}
