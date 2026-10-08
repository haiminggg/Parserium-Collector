export const prefix = "/api/cloud/v1";
export interface Workspace { id: string; name: string; role: string }
export interface Session { user: { id: string; email: string }; workspaces: Workspace[] }
export interface DocumentRecord {
  id: string; filename: string; size_bytes: number; created_at: number; source_url: string | null;
  validation_status: string; validation_error_code: string | null;
  job_id: string | null; job_status: string | null; job_error_code: string | null; job_engine?: string | null;
}
export interface ParseEngine { id: string; label: string; ocr: boolean; license: string; description: string }
export interface ParseEngines { default: string; engines: ParseEngine[] }
export interface SearchResult { id: string; title: string; description: string; url: string; file_type: string | null }
export interface SearchRecord {
  id: string; query: string; file_type: string; status: string; error_code: string | null;
  created_at: number; results: SearchResult[];
}
export interface Connection { configured: boolean; can_manage: boolean; updated_at: number | null; storage_available: boolean }
export class CloudError extends Error {
  constructor(public code: string, public status: number) { super(errorMessage(code)); }
}
export function errorMessage(code: string): string {
  return ({
    authentication_required: "Your session ended. Sign in again to continue.",
    forbidden: "This request could not be authorized. Refresh the page and try again.",
    not_found: "This item is no longer available in your workspace.",
    invalid_pdf: "This PDF is damaged or unreadable. Choose a different file.",
    invalid_docx: "This DOCX could not be converted. It may be damaged or contain active content or external relationships.",
    encrypted_pdf: "Password-protected PDFs are not supported.",
    page_limit_exceeded: "The document exceeds the 20-page limit after rendering.",
    upload_too_large: "Choose a file no larger than 10 MiB.",
    response_too_large: "This source exceeds the 10 MiB collection limit.",
    storage_quota_exceeded: "Your workspace storage is full. Delete documents to free space.",
    daily_job_limit: "Your workspace has reached its 20 daily parse jobs. Try again after midnight UTC.",
    processing_budget_exceeded: "Today's processing allowance is used up. Try again after midnight UTC.",
    processing_disabled: "Parsing is temporarily paused. Your uploaded files are saved.",
    processing_unavailable: "The parser is temporarily unavailable. You can retry this submission.",
    parser_timeout: "Parsing exceeded its time limit. Try a smaller or less complex document.",
    parser_unavailable: "The parser could not complete this job. Upload the document again to try a new job.",
    parser_failed: "The parser could not read this document. Try another file.",
    no_text_extracted: "No text could be extracted. This engine cannot read scanned pages, so upload the document again and choose an engine with OCR.",
    invalid_engine: "That parser is not available. Refresh the page and choose another.",
    parse_conflict: "This document already has a parse request. Refresh the documents to see its status.",
    output_too_large: "The extracted output exceeds 10 MiB.",
    parse_in_progress: "Wait for parsing to finish before deleting this document.",
    upload_conflict: "This upload is already in progress. Refresh the documents before trying again.",
    firecrawl_not_connected: "Connect your Firecrawl account in Settings to search the web.",
    firecrawl_auth_failed: "Firecrawl rejected the API key. Update it in Settings.",
    firecrawl_credits_exhausted: "Your Firecrawl account has no available credits.",
    firecrawl_rate_limited: "Firecrawl is limiting requests. Try again shortly.",
    firecrawl_unavailable: "Firecrawl could not complete this search. Try again shortly.",
    search_interrupted: "The search was interrupted. Start a new search to try again.",
    search_limit_or_busy: "Another search is running, or the workspace has reached its 20 daily searches.",
    connection_storage_unavailable: "Connection settings are temporarily unavailable. Contact your workspace administrator.",
    invalid_api_key: "Enter a valid Firecrawl API key beginning with fc-.",
    owner_required: "Only the workspace owner can change this connection.",
    source_not_public: "This source cannot be collected. Open the source and upload the file manually.",
    source_not_document: "This link did not return a PDF or DOCX. Open the source to find the original file.",
    source_unavailable: "The source could not be downloaded. Open it and upload the file manually.",
    collection_rate_limited: "Too many collection requests. Wait a minute before continuing.",
    storage_unavailable: "Storage is temporarily unavailable. Your request can be retried.",
  } as Record<string, string>)[code] || "The request could not be completed. Please try again.";
}
export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.method && options.method !== "GET") {
    const csrf = document.cookie.split(";").map(value => value.trim()).find(value => value.startsWith("__Host-parserium_csrf="))?.split("=")[1];
    headers.set("X-CSRF-Token", csrf || "");
  }
  const response = await fetch(prefix + path, { ...options, headers, cache: "no-store", credentials: "same-origin" });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    if (response.status === 401 && path !== "/session") window.dispatchEvent(new Event("parserium-session-expired"));
    throw new CloudError(data.error || "service_unavailable", response.status);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}
export const scoped = (path: string, workspace: string) => path + "?" + new URLSearchParams({ workspace });
export const jsonOptions = (method: string, body: unknown, id?: string): RequestInit => ({
  method, body: JSON.stringify(body), headers: { "Content-Type": "application/json", ...(id ? { "Idempotency-Key": id } : {}) },
});
export const activeJob = (doc: DocumentRecord) => ["pending_dispatch", "queued", "running"].includes(doc.job_status || "");
export const dateText = (value: number) => new Date(value * 1000).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
export const sizeText = (value: number) => value < 1048576 ? `${Math.max(1, Math.round(value / 1024))} KB` : `${(value / 1048576).toFixed(1)} MB`;
