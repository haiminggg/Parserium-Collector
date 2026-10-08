export type FirecrawlConnectionType = "cloud" | "remote";

export type FirecrawlConnectionStatus =
  | "never_validated"
  | "healthy"
  | "degraded"
  | "disabled"
  | "deleted";

export type FirecrawlConnectionFailureCategory =
  | "invalid_credentials"
  | "blocked_destination"
  | "dns_failure"
  | "tls_failure"
  | "timeout"
  | "rate_limited"
  | "service_unavailable"
  | "response_too_large"
  | "incompatible_response"
  | "credential_unavailable";

export interface FirecrawlConnectionSummary {
  id: string;
  name: string;
  connection_type: FirecrawlConnectionType;
  status: FirecrawlConnectionStatus;
  enabled: boolean;
  is_default: boolean;
  usable: boolean;
  normalized_base_url?: string;
  last_validation_attempt_at: string | null;
  last_validation_success_at: string | null;
  last_failure_category: FirecrawlConnectionFailureCategory | null;
}

export interface CreateFirecrawlConnectionRequest {
  name: string;
  connection_type: FirecrawlConnectionType;
  base_url?: string | null;
  credential: string;
  is_default?: boolean;
}

export interface UpdateFirecrawlConnectionRequest {
  name?: string | null;
  base_url?: string | null;
  enabled?: boolean | null;
  is_default?: boolean | null;
}

const connectionTypes = new Set<FirecrawlConnectionType>(["cloud", "remote"]);
const connectionStatuses = new Set<FirecrawlConnectionStatus>([
  "never_validated",
  "healthy",
  "degraded",
  "disabled",
  "deleted",
]);
const failureCategories = new Set<FirecrawlConnectionFailureCategory>([
  "invalid_credentials",
  "blocked_destination",
  "dns_failure",
  "tls_failure",
  "timeout",
  "rate_limited",
  "service_unavailable",
  "response_too_large",
  "incompatible_response",
  "credential_unavailable",
]);
const summaryKeys = new Set([
  "id",
  "name",
  "connection_type",
  "status",
  "enabled",
  "is_default",
  "usable",
  "normalized_base_url",
  "last_validation_attempt_at",
  "last_validation_success_at",
  "last_failure_category",
]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasOnlyKeys(value: Record<string, unknown>, allowed: Set<string>): boolean {
  return Object.keys(value).every((key) => allowed.has(key));
}

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}

function isConnectionSummary(value: unknown): value is FirecrawlConnectionSummary {
  return (
    isRecord(value) &&
    hasOnlyKeys(value, summaryKeys) &&
    typeof value.id === "string" &&
    typeof value.name === "string" &&
    typeof value.connection_type === "string" &&
    connectionTypes.has(value.connection_type as FirecrawlConnectionType) &&
    typeof value.status === "string" &&
    connectionStatuses.has(value.status as FirecrawlConnectionStatus) &&
    typeof value.enabled === "boolean" &&
    typeof value.is_default === "boolean" &&
    typeof value.usable === "boolean" &&
    (value.normalized_base_url === undefined ||
      typeof value.normalized_base_url === "string") &&
    isNullableString(value.last_validation_attempt_at) &&
    isNullableString(value.last_validation_success_at) &&
    (value.last_failure_category === null ||
      (typeof value.last_failure_category === "string" &&
        failureCategories.has(
          value.last_failure_category as FirecrawlConnectionFailureCategory,
        )))
  );
}

function csrfHeaders(csrfToken: string): HeadersInit {
  return {
    Accept: "application/json",
    "Content-Type": "application/json",
    "X-Parserium-CSRF": csrfToken,
  };
}

async function summaryJson(response: Response, unavailableMessage: string) {
  if (!response.ok) throw new Error(unavailableMessage);
  const value: unknown = await response.json();
  if (!isConnectionSummary(value)) {
    throw new Error("Firecrawl connection returned invalid data.");
  }
  return value;
}

export async function listFirecrawlConnections(): Promise<FirecrawlConnectionSummary[]> {
  const response = await fetch("/api/v1/firecrawl/connections", {
    headers: { Accept: "application/json" },
    credentials: "same-origin",
  });
  if (!response.ok) throw new Error("Firecrawl connections are unavailable.");
  const value: unknown = await response.json();
  if (
    !isRecord(value) ||
    !hasOnlyKeys(value, new Set(["connections"])) ||
    !Array.isArray(value.connections) ||
    !value.connections.every(isConnectionSummary)
  ) {
    throw new Error("Firecrawl connections returned invalid data.");
  }
  return value.connections;
}

export async function createFirecrawlConnection(
  request: CreateFirecrawlConnectionRequest,
  csrfToken: string,
): Promise<FirecrawlConnectionSummary> {
  const response = await fetch("/api/v1/firecrawl/connections", {
    method: "POST",
    headers: csrfHeaders(csrfToken),
    credentials: "same-origin",
    body: JSON.stringify(request),
  });
  return summaryJson(response, "The Firecrawl connection could not be created.");
}

export async function updateFirecrawlConnection(
  connectionId: string,
  request: UpdateFirecrawlConnectionRequest,
  csrfToken: string,
): Promise<FirecrawlConnectionSummary> {
  const response = await fetch(
    `/api/v1/firecrawl/connections/${encodeURIComponent(connectionId)}`,
    {
      method: "PATCH",
      headers: csrfHeaders(csrfToken),
      credentials: "same-origin",
      body: JSON.stringify(request),
    },
  );
  return summaryJson(response, "The Firecrawl connection could not be updated.");
}

export async function testFirecrawlConnection(
  connectionId: string,
  csrfToken: string,
): Promise<FirecrawlConnectionSummary> {
  const response = await fetch(
    `/api/v1/firecrawl/connections/${encodeURIComponent(connectionId)}/test`,
    {
      method: "POST",
      headers: csrfHeaders(csrfToken),
      credentials: "same-origin",
    },
  );
  return summaryJson(response, "The Firecrawl connection test could not be completed.");
}

export async function replaceFirecrawlCredential(
  connectionId: string,
  credential: string,
  csrfToken: string,
): Promise<FirecrawlConnectionSummary> {
  const response = await fetch(
    `/api/v1/firecrawl/connections/${encodeURIComponent(connectionId)}/credential`,
    {
      method: "PUT",
      headers: csrfHeaders(csrfToken),
      credentials: "same-origin",
      body: JSON.stringify({ credential }),
    },
  );
  return summaryJson(response, "The Firecrawl credential could not be replaced.");
}

export async function deleteFirecrawlConnection(
  connectionId: string,
  csrfToken: string,
): Promise<void> {
  const response = await fetch(
    `/api/v1/firecrawl/connections/${encodeURIComponent(connectionId)}`,
    {
      method: "DELETE",
      headers: csrfHeaders(csrfToken),
      credentials: "same-origin",
    },
  );
  if (!response.ok) throw new Error("The Firecrawl connection could not be deleted.");
}
