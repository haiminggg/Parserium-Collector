import type { components } from "../generated/dashboard-v1";

export type HealthStatus = components["schemas"]["HealthStatus"];

export async function loadHealth(): Promise<HealthStatus> {
  const response = await fetch("/api/v1/health/status", {
    headers: { Accept: "application/json" },
    credentials: "same-origin",
  });
  if (!response.ok) {
    throw new Error("Health request failed.");
  }
  return (await response.json()) as HealthStatus;
}

