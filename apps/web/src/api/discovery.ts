import type { FirecrawlConnectionType } from "./firecrawlConnections";

export type DocumentType = "pdf" | "docx";

export type AnalysisSessionStatus =
  | "queued"
  | "running"
  | "completed"
  | "cancelled"
  | "failed";

export type DiscoveryJobStage =
  | "queued"
  | "discovering"
  | "analyzing"
  | "completed"
  | "cancelled"
  | "failed";

export type CandidateAnalysisStatus =
  | "queued"
  | "downloading"
  | "validating"
  | "converting"
  | "parsing"
  | "ready"
  | "no_tables"
  | "partial"
  | "failed"
  | "cancelled"
  | "promoted";

export type PublicAnalysisState = "valid" | "no_tables" | "partial" | "failed";

export interface DocumentDiscoveryRequest {
  query: string;
  limit: number;
  document_types: DocumentType[];
  include_domains: string[];
  exclude_domains: string[];
  tables_required: boolean;
  firecrawl_connection_id: string | null;
  force_refresh?: boolean;
}

export interface DocumentCandidate {
  url: string;
  title: string | null;
  description: string | null;
  document_type: DocumentType;
  source: "firecrawl";
}

export interface TableBoundingBox {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface CandidateTable {
  id: string;
  page_num: number;
  table_index: number;
  bounding_box: TableBoundingBox;
  cells: string[][];
  markdown: string;
}

export interface AnalysisCandidate {
  id: string;
  ordinal: number;
  source_url: string;
  title: string | null;
  description: string | null;
  document_type: DocumentType;
  status: CandidateAnalysisStatus;
  public_state: PublicAnalysisState | null;
  attempt_count: number;
  bytes_downloaded: number;
  content_length: number | null;
  page_count: number | null;
  analyzed_page_count: number;
  table_count: number;
  table_count_lower_bound: boolean;
  preview_available: boolean;
  preview_page_num: number | null;
  preview_width: number | null;
  preview_height: number | null;
  error_code: string | null;
  error_detail: string | null;
  error_retryable: boolean | null;
  tables: CandidateTable[];
  created_at: string;
  updated_at: string;
  completed_at: string | null;
}

export interface AnalysisSession {
  id: string;
  query: string;
  document_types: DocumentType[];
  tables_required: boolean;
  firecrawl_connection_id: string | null;
  firecrawl_connection_name_snapshot: string | null;
  firecrawl_connection_type_snapshot: FirecrawlConnectionType | null;
  status: AnalysisSessionStatus;
  job_stage: DiscoveryJobStage;
  error_code: string | null;
  candidate_count: number;
  bytes_downloaded: number;
  session_byte_limit: number;
  cancellation_requested: boolean;
  created_at: string;
  updated_at: string;
  expires_at: string;
  completed_at: string | null;
  candidates: AnalysisCandidate[];
}

const sessionStatuses = new Set<AnalysisSessionStatus>([
  "queued",
  "running",
  "completed",
  "cancelled",
  "failed",
]);

const discoveryJobStages = new Set<DiscoveryJobStage>([
  "queued",
  "discovering",
  "analyzing",
  "completed",
  "cancelled",
  "failed",
]);

const candidateStatuses = new Set<CandidateAnalysisStatus>([
  "queued",
  "downloading",
  "validating",
  "converting",
  "parsing",
  "ready",
  "no_tables",
  "partial",
  "failed",
  "cancelled",
  "promoted",
]);

const publicStates = new Set<PublicAnalysisState>([
  "valid",
  "no_tables",
  "partial",
  "failed",
]);

const safeFailureMessages = {
  firecrawl_discovery_unavailable:
    "Firecrawl document discovery is unavailable. Test the selected connection and try again.",
} as const;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}

function isNullableNumber(value: unknown): value is number | null {
  return value === null || typeof value === "number";
}

function isBoundingBox(value: unknown): value is TableBoundingBox {
  return (
    isRecord(value) &&
    typeof value.x === "number" &&
    typeof value.y === "number" &&
    typeof value.width === "number" &&
    typeof value.height === "number"
  );
}

function isCandidateTable(value: unknown): value is CandidateTable {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    typeof value.page_num === "number" &&
    typeof value.table_index === "number" &&
    isBoundingBox(value.bounding_box) &&
    Array.isArray(value.cells) &&
    value.cells.every(
      (row) => Array.isArray(row) && row.every((cell) => typeof cell === "string"),
    ) &&
    typeof value.markdown === "string"
  );
}

function isAnalysisCandidate(value: unknown): value is AnalysisCandidate {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    typeof value.ordinal === "number" &&
    typeof value.source_url === "string" &&
    isNullableString(value.title) &&
    isNullableString(value.description) &&
    (value.document_type === "pdf" || value.document_type === "docx") &&
    typeof value.status === "string" &&
    candidateStatuses.has(value.status as CandidateAnalysisStatus) &&
    (value.public_state === null ||
      (typeof value.public_state === "string" &&
        publicStates.has(value.public_state as PublicAnalysisState))) &&
    typeof value.attempt_count === "number" &&
    typeof value.bytes_downloaded === "number" &&
    isNullableNumber(value.content_length) &&
    isNullableNumber(value.page_count) &&
    typeof value.analyzed_page_count === "number" &&
    typeof value.table_count === "number" &&
    typeof value.table_count_lower_bound === "boolean" &&
    typeof value.preview_available === "boolean" &&
    isNullableNumber(value.preview_page_num) &&
    isNullableNumber(value.preview_width) &&
    isNullableNumber(value.preview_height) &&
    isNullableString(value.error_code) &&
    isNullableString(value.error_detail) &&
    (value.error_retryable === null || typeof value.error_retryable === "boolean") &&
    Array.isArray(value.tables) &&
    value.tables.every(isCandidateTable) &&
    typeof value.created_at === "string" &&
    typeof value.updated_at === "string" &&
    isNullableString(value.completed_at)
  );
}

function isAnalysisSession(value: unknown): value is AnalysisSession {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    typeof value.query === "string" &&
    Array.isArray(value.document_types) &&
    value.document_types.every((item) => item === "pdf" || item === "docx") &&
    typeof value.tables_required === "boolean" &&
    isNullableString(value.firecrawl_connection_id) &&
    isNullableString(value.firecrawl_connection_name_snapshot) &&
    (value.firecrawl_connection_type_snapshot === null ||
      value.firecrawl_connection_type_snapshot === "cloud" ||
      value.firecrawl_connection_type_snapshot === "remote") &&
    typeof value.status === "string" &&
    sessionStatuses.has(value.status as AnalysisSessionStatus) &&
    typeof value.job_stage === "string" &&
    discoveryJobStages.has(value.job_stage as DiscoveryJobStage) &&
    isNullableString(value.error_code) &&
    typeof value.candidate_count === "number" &&
    typeof value.bytes_downloaded === "number" &&
    typeof value.session_byte_limit === "number" &&
    typeof value.cancellation_requested === "boolean" &&
    typeof value.created_at === "string" &&
    typeof value.updated_at === "string" &&
    typeof value.expires_at === "string" &&
    isNullableString(value.completed_at) &&
    Array.isArray(value.candidates) &&
    value.candidates.every(isAnalysisCandidate)
  );
}

function csrfHeaders(csrfToken: string): HeadersInit {
  return {
    Accept: "application/json",
    "Content-Type": "application/json",
    "X-Parserium-CSRF": csrfToken,
  };
}

async function responseFailure(response: Response, fallback: string): Promise<Error> {
  try {
    const value: unknown = await response.json();
    if (isRecord(value) && isRecord(value.detail) && typeof value.detail.code === "string") {
      const code = value.detail.code as keyof typeof safeFailureMessages;
      if (Object.hasOwn(safeFailureMessages, code)) {
        return new Error(safeFailureMessages[code]);
      }
    }
  } catch {
    // The fixed fallback below is the safe public boundary.
  }
  return new Error(fallback);
}

async function analysisJson(response: Response, message: string): Promise<AnalysisSession> {
  if (!response.ok) throw await responseFailure(response, message);
  const value: unknown = await response.json();
  if (!isAnalysisSession(value)) throw new Error("Document analysis returned invalid data.");
  return value;
}

export async function startAnalysisSearch(
  request: DocumentDiscoveryRequest,
  csrfToken: string,
): Promise<AnalysisSession> {
  const response = await fetch("/api/v1/discovery/searches", {
    method: "POST",
    headers: csrfHeaders(csrfToken),
    credentials: "same-origin",
    body: JSON.stringify(request),
  });
  return analysisJson(response, "Document search is unavailable.");
}

export async function loadAnalysisSearch(sessionId: string): Promise<AnalysisSession> {
  const response = await fetch(
    `/api/v1/discovery/searches/${encodeURIComponent(sessionId)}`,
    {
      headers: { Accept: "application/json" },
      credentials: "same-origin",
    },
  );
  return analysisJson(response, "Document analysis status is unavailable.");
}

export async function cancelAnalysisSearch(
  sessionId: string,
  csrfToken: string,
): Promise<void> {
  const response = await fetch(
    `/api/v1/discovery/searches/${encodeURIComponent(sessionId)}`,
    {
      method: "DELETE",
      headers: csrfHeaders(csrfToken),
      credentials: "same-origin",
    },
  );
  if (!response.ok) throw new Error("Document analysis could not be cancelled.");
}
