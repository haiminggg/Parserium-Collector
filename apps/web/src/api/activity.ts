import type { components } from "../generated/dashboard-v1";


export type ActivityJob = components["schemas"]["ActivityJobResponse"];
export type ActivityJobType = components["schemas"]["ActivityJobType"];
export type ActivityJobState = components["schemas"]["ActivityJobState"];
export type ActivityPage = components["schemas"]["ActivityPageResponse"];

const jobTypes = new Set(["discovery", "collection", "analysis", "export"]);
const jobStates = new Set(["queued", "active", "completed", "failed", "cancelled"]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}

function isNonNegativeInteger(value: unknown): value is number {
  return Number.isInteger(value) && (value as number) >= 0;
}

function isActivityJob(value: unknown): value is ActivityJob {
  if (!isRecord(value)) return false;
  return (
    typeof value.id === "string" &&
    typeof value.job_type === "string" &&
    jobTypes.has(value.job_type) &&
    typeof value.title === "string" &&
    isNullableString(value.subtitle) &&
    typeof value.state === "string" &&
    jobStates.has(value.state) &&
    typeof value.stage === "string" &&
    (value.progress_percent === null ||
      (isNonNegativeInteger(value.progress_percent) && value.progress_percent <= 100)) &&
    isNullableString(value.created_by_user_id) &&
    isNullableString(value.created_by_name) &&
    isNullableString(value.related_document_id) &&
    isNullableString(value.error_code) &&
    typeof value.can_cancel === "boolean" &&
    typeof value.can_retry === "boolean" &&
    typeof value.can_delete === "boolean" &&
    typeof value.created_at === "string" &&
    typeof value.updated_at === "string" &&
    isNullableString(value.completed_at)
  );
}

function isActivityPage(value: unknown): value is ActivityPage {
  if (!isRecord(value) || !isRecord(value.summary)) return false;
  return (
    Array.isArray(value.items) &&
    value.items.every(isActivityJob) &&
    isNonNegativeInteger(value.total) &&
    isNullableString(value.next_cursor) &&
    isNonNegativeInteger(value.summary.active) &&
    isNonNegativeInteger(value.summary.queued) &&
    isNonNegativeInteger(value.summary.failed) &&
    isNonNegativeInteger(value.summary.completed)
  );
}

export async function listActivity(
  options: {
    limit?: number;
    cursor?: string | null;
    jobType?: ActivityJobType | null;
    state?: ActivityJobState | null;
    creatorId?: string | null;
    createdAfter?: string | null;
  } = {},
): Promise<ActivityPage> {
  const parameters = new URLSearchParams({ limit: String(options.limit ?? 50) });
  if (options.cursor) parameters.set("cursor", options.cursor);
  if (options.jobType) parameters.set("job_type", options.jobType);
  if (options.state) parameters.set("state", options.state);
  if (options.creatorId) parameters.set("creator_id", options.creatorId);
  if (options.createdAfter) parameters.set("created_after", options.createdAfter);
  const response = await fetch(`/api/v1/activity?${parameters}`, {
    headers: { Accept: "application/json" },
  });
  if (!response.ok) throw new Error("Activity history is unavailable.");
  const value: unknown = await response.json();
  if (!isActivityPage(value)) {
    throw new Error("Activity history returned an invalid response.");
  }
  return value;
}

export async function deleteActivityHistory(
  jobType: ActivityJobType,
  jobId: string,
  csrfToken: string,
): Promise<void> {
  const headers = new Headers({ Accept: "application/json" });
  headers.set("X-Parserium-CSRF", csrfToken);
  const response = await fetch(`/api/v1/activity/${jobType}/${jobId}`, {
    method: "DELETE",
    headers,
  });
  if (!response.ok) throw new Error("Activity history could not be deleted.");
}

async function mutateActivity(path: string, csrfToken: string, message: string): Promise<void> {
  const headers = new Headers({ Accept: "application/json" });
  headers.set("X-Parserium-CSRF", csrfToken);
  const response = await fetch(path, { method: "POST", headers });
  if (!response.ok) throw new Error(message);
}

export async function cancelActivityJob(job: ActivityJob, csrfToken: string): Promise<void> {
  if (job.job_type !== "discovery") {
    throw new Error("This job type cannot be cancelled.");
  }
  const headers = new Headers({ Accept: "application/json" });
  headers.set("X-Parserium-CSRF", csrfToken);
  const response = await fetch(`/api/v1/discovery/searches/${job.id}`, {
    method: "DELETE",
    headers,
  });
  if (!response.ok) throw new Error("The discovery job could not be cancelled.");
}

export async function retryActivityJob(job: ActivityJob, csrfToken: string): Promise<void> {
  if (job.job_type === "discovery") {
    return mutateActivity(
      `/api/v1/discovery/searches/${job.id}/retry`,
      csrfToken,
      "The discovery job could not be retried.",
    );
  }
  if (job.job_type === "collection") {
    return mutateActivity(
      `/api/v1/collection/jobs/${job.id}/retry`,
      csrfToken,
      "The collection job could not be retried.",
    );
  }
  throw new Error("This job type cannot be retried.");
}
