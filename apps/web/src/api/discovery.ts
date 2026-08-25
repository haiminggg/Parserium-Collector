export type DocumentType = "pdf" | "docx";

export interface DocumentDiscoveryRequest {
  query: string;
  limit: number;
  document_types: DocumentType[];
  include_domains: string[];
  exclude_domains: string[];
}

export interface DocumentCandidate {
  url: string;
  title: string | null;
  description: string | null;
  document_type: DocumentType;
  source: "firecrawl";
}

export interface DocumentDiscoveryResponse {
  provider_search_ids: string[];
  candidates: DocumentCandidate[];
  rejected_non_document_results: number;
}

export async function searchDocuments(
  request: DocumentDiscoveryRequest,
  csrfToken: string,
): Promise<DocumentDiscoveryResponse> {
  const response = await fetch("/api/v1/discovery/search", {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      "X-Parserium-CSRF": csrfToken,
    },
    credentials: "same-origin",
    body: JSON.stringify(request),
  });
  if (!response.ok) throw new Error("Document search is unavailable.");
  const result = (await response.json()) as DocumentDiscoveryResponse;
  if (!Array.isArray(result.candidates) || !Array.isArray(result.provider_search_ids)) {
    throw new Error("Document search returned an invalid response.");
  }
  return result;
}
