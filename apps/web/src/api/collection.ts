import type { components } from "../generated/dashboard-v1";
import type { DocumentCandidate } from "./discovery";

export type CollectionJob = components["schemas"]["CollectionJobResponse"];
export type CollectionJobPage = components["schemas"]["CollectionJobPageResponse"];
export type StoredDocument = components["schemas"]["StoredDocumentResponse"];
export type StoredDocumentPage = components["schemas"]["StoredDocumentPageResponse"];
export type DocumentExport = components["schemas"]["DocumentExportResponse"];

const collectionStatuses = new Set([
  "queued",
  "downloading",
  "validating",
  "completed",
  "duplicate",
  "failed",
]);
const exportStatuses = new Set(["queued", "exporting", "completed", "failed"]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}

function isCollectionJob(value: unknown): value is CollectionJob {
  if (!isRecord(value)) return false;
  return (
    typeof value.id === "string" &&
    typeof value.source_url === "string" &&
    isNullableString(value.title) &&
    (value.expected_document_type === "pdf" || value.expected_document_type === "docx") &&
    typeof value.status === "string" &&
    collectionStatuses.has(value.status) &&
    typeof value.attempt_count === "number" &&
    typeof value.available_at === "string" &&
    typeof value.bytes_downloaded === "number" &&
    (value.content_length === null || typeof value.content_length === "number") &&
    isNullableString(value.document_id) &&
    isNullableString(value.error_code) &&
    isNullableString(value.error_detail) &&
    (value.error_retryable === null || typeof value.error_retryable === "boolean") &&
    typeof value.created_at === "string" &&
    typeof value.updated_at === "string" &&
    isNullableString(value.started_at) &&
    isNullableString(value.completed_at)
  );
}

function isStoredDocument(value: unknown): value is StoredDocument {
  if (!isRecord(value)) return false;
  return (
    typeof value.id === "string" &&
    typeof value.sha256 === "string" &&
    (value.document_type === "pdf" || value.document_type === "docx") &&
    typeof value.media_type === "string" &&
    typeof value.size_bytes === "number" &&
    typeof value.safe_filename === "string" &&
    typeof value.created_at === "string"
  );
}

function isDocumentExport(value: unknown): value is DocumentExport {
  if (!isRecord(value)) return false;
  return (
    typeof value.id === "string" &&
    typeof value.document_id === "string" &&
    typeof value.relative_directory === "string" &&
    typeof value.target_filename === "string" &&
    isNullableString(value.exported_relative_path) &&
    typeof value.status === "string" &&
    exportStatuses.has(value.status) &&
    typeof value.attempt_count === "number" &&
    typeof value.available_at === "string" &&
    isNullableString(value.error_code) &&
    isNullableString(value.error_detail) &&
    typeof value.created_at === "string" &&
    typeof value.updated_at === "string" &&
    isNullableString(value.completed_at)
  );
}

function isPageMetadata(value: Record<string, unknown>): boolean {
  return (
    Number.isInteger(value.total) &&
    (value.total as number) >= 0 &&
    isNullableString(value.next_cursor)
  );
}

function isCollectionJobPage(value: unknown): value is CollectionJobPage {
  return (
    isRecord(value) &&
    isPageMetadata(value) &&
    Array.isArray(value.items) &&
    value.items.every(isCollectionJob)
  );
}

function isStoredDocumentPage(value: unknown): value is StoredDocumentPage {
  return (
    isRecord(value) &&
    isPageMetadata(value) &&
    Array.isArray(value.items) &&
    value.items.every(isStoredDocument)
  );
}

async function responseJson(response: Response, message: string): Promise<unknown> {
  if (!response.ok) throw new Error(message);
  return response.json() as Promise<unknown>;
}

function csrfHeaders(csrfToken: string): HeadersInit {
  return {
    Accept: "application/json",
    "Content-Type": "application/json",
    "X-Parserium-CSRF": csrfToken,
  };
}

export async function createCollectionJobs(
  candidates: DocumentCandidate[],
  csrfToken: string,
): Promise<CollectionJob[]> {
  const response = await fetch("/api/v1/collection/jobs", {
    method: "POST",
    headers: csrfHeaders(csrfToken),
    credentials: "same-origin",
    body: JSON.stringify({
      candidates: candidates.slice(0, 30).map((candidate) => ({
        url: candidate.url,
        title: candidate.title,
        document_type: candidate.document_type,
      })),
    }),
  });
  const value = await responseJson(response, "Document collection request failed.");
  if (!Array.isArray(value) || !value.every(isCollectionJob)) {
    throw new Error("Document collection returned an invalid response.");
  }
  return value;
}

export async function createAnalyzedCollectionJobs(
  analysisIds: string[],
  csrfToken: string,
): Promise<CollectionJob[]> {
  const response = await fetch("/api/v1/collection/jobs", {
    method: "POST",
    headers: csrfHeaders(csrfToken),
    credentials: "same-origin",
    body: JSON.stringify({ analysis_ids: analysisIds.slice(0, 30) }),
  });
  const value = await responseJson(response, "Analyzed document collection failed.");
  if (!Array.isArray(value) || !value.every(isCollectionJob)) {
    throw new Error("Analyzed document collection returned an invalid response.");
  }
  return value;
}

export async function loadCollectionJobs(): Promise<CollectionJobPage> {
  const response = await fetch("/api/v1/collection/jobs", {
    headers: { Accept: "application/json" },
    credentials: "same-origin",
  });
  const value = await responseJson(response, "Collection status is unavailable.");
  if (!isCollectionJobPage(value)) {
    throw new Error("Collection status returned an invalid response.");
  }
  return value;
}

export async function retryCollectionJob(
  jobId: string,
  csrfToken: string,
): Promise<CollectionJob> {
  const response = await fetch(`/api/v1/collection/jobs/${encodeURIComponent(jobId)}/retry`, {
    method: "POST",
    headers: csrfHeaders(csrfToken),
    credentials: "same-origin",
  });
  const value = await responseJson(response, "Collection retry failed.");
  if (!isCollectionJob(value)) throw new Error("Collection retry returned an invalid response.");
  return value;
}

export async function clearCompletedCollectionJobs(csrfToken: string): Promise<void> {
  const response = await fetch("/api/v1/collection/jobs/completed", {
    method: "DELETE",
    headers: csrfHeaders(csrfToken),
    credentials: "same-origin",
  });
  if (!response.ok) throw new Error("Completed collection history could not be cleared.");
}

export async function loadDocuments(): Promise<StoredDocumentPage> {
  const response = await fetch("/api/v1/documents", {
    headers: { Accept: "application/json" },
    credentials: "same-origin",
  });
  const value = await responseJson(response, "Stored documents are unavailable.");
  if (!isStoredDocumentPage(value)) {
    throw new Error("Stored documents returned an invalid response.");
  }
  return value;
}

export async function deleteDocument(documentId: string, csrfToken: string): Promise<void> {
  const response = await fetch(`/api/v1/documents/${encodeURIComponent(documentId)}`, {
    method: "DELETE",
    headers: csrfHeaders(csrfToken),
    credentials: "same-origin",
  });
  if (!response.ok) throw new Error("The stored document could not be deleted.");
}

export async function createDocumentExport(
  documentId: string,
  relativeDirectory: string,
  csrfToken: string,
): Promise<DocumentExport> {
  const response = await fetch(`/api/v1/documents/${encodeURIComponent(documentId)}/exports`, {
    method: "POST",
    headers: csrfHeaders(csrfToken),
    credentials: "same-origin",
    body: JSON.stringify({ relative_directory: relativeDirectory }),
  });
  const value = await responseJson(response, "Document export request failed.");
  if (!isDocumentExport(value)) {
    throw new Error("Document export returned an invalid response.");
  }
  return value;
}

export async function loadExports(): Promise<DocumentExport[]> {
  const response = await fetch("/api/v1/exports", {
    headers: { Accept: "application/json" },
    credentials: "same-origin",
  });
  const value = await responseJson(response, "Document export status is unavailable.");
  if (!Array.isArray(value) || !value.every(isDocumentExport)) {
    throw new Error("Document export status returned an invalid response.");
  }
  return value;
}

export function normalizeExportDirectory(value: string): string | null {
  const stripped = value.trim();
  if (/^[A-Za-z]:/.test(stripped) || stripped.startsWith("/") || stripped.startsWith("\\")) {
    return null;
  }
  const parts = stripped
    .replaceAll("\\", "/")
    .split("/")
    .filter((part) => part !== "" && part !== ".");
  if (parts.includes("..")) return null;
  return parts.join("/");
}
