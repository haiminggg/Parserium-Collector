import { Button, Text } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useId } from "react";

import {
  listFirecrawlConnections,
  type FirecrawlConnectionStatus,
  type FirecrawlConnectionSummary,
} from "./api/firecrawlConnections";

interface FirecrawlConnectionSelectorProps {
  workspaceId: string;
  role: "owner" | "member";
  value: string | null;
  onChange: (connectionId: string | null) => void;
  onUsabilityChange: (usable: boolean) => void;
  onConfigure: () => void;
}

const statusLabels: Record<FirecrawlConnectionStatus, string> = {
  healthy: "Healthy",
  degraded: "Degraded",
  disabled: "Disabled",
  never_validated: "Needs validation",
  deleted: "Deleted",
};

function isUsable(connection: FirecrawlConnectionSummary): boolean {
  return connection.status === "healthy" && connection.enabled && connection.usable;
}

export function FirecrawlConnectionSelector({
  workspaceId,
  role,
  value,
  onChange,
  onUsabilityChange,
  onConfigure,
}: FirecrawlConnectionSelectorProps) {
  const selectorId = useId();
  const connections = useQuery({
    queryKey: ["firecrawl-connections", workspaceId],
    queryFn: listFirecrawlConnections,
  });

  useEffect(() => {
    const summaries = connections.data ?? [];
    const current = summaries.find(
      (connection) => connection.id === value && isUsable(connection),
    );
    const preferred =
      current ??
      summaries.find((connection) => connection.is_default && isUsable(connection)) ??
      summaries.find(isUsable) ??
      null;
    const nextValue = preferred?.id ?? null;
    if (nextValue !== value) onChange(nextValue);
    onUsabilityChange(nextValue !== null);
  }, [connections.data, onChange, onUsabilityChange, value, workspaceId]);

  const summaries = connections.data ?? [];
  const selected = summaries.find((connection) => connection.id === value) ?? null;
  const hasUsableConnection = summaries.some(isUsable);
  const unavailable = !connections.isLoading && !hasUsableConnection;
  const explanation = connections.isError
    ? "Connections unavailable"
    : "No usable connection";

  return (
    <div
      className="firecrawl-selector"
      data-unavailable={unavailable || connections.isError || undefined}
    >
      <label className="visually-hidden" htmlFor={selectorId}>
        Firecrawl connection
      </label>
      <select
        id={selectorId}
        className="firecrawl-selector-input"
        value={value ?? ""}
        disabled={connections.isLoading || connections.isError || !hasUsableConnection}
        title={selected?.name ?? explanation}
        onChange={(event) => onChange(event.currentTarget.value || null)}
      >
        {connections.isLoading ? <option value="">Checking connections</option> : null}
        {connections.isError ? <option value="">Connections unavailable</option> : null}
        {unavailable && !connections.isError ? (
          <option value="">Select connection</option>
        ) : null}
        {hasUsableConnection && value === null ? (
          <option value="">Choose connection</option>
        ) : null}
        {summaries.map((connection) => (
          <option
            key={connection.id}
            value={connection.id}
            disabled={!isUsable(connection)}
            title={connection.name}
          >
            {connection.name}, {statusLabels[connection.status]}
            {connection.is_default ? ", Default" : ""}
          </option>
        ))}
      </select>
      {unavailable || connections.isError ? (
        <div className="firecrawl-selector-empty" role="status">
          <Text component="span">{explanation}</Text>
          {role === "owner" ? (
            <Button type="button" variant="subtle" size="compact-xs" onClick={onConfigure}>
              Configure connections
            </Button>
          ) : (
            <Text component="span">Ask a workspace owner to validate a connection.</Text>
          )}
        </div>
      ) : null}
    </div>
  );
}
