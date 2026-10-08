import { afterEach, expect, it, vi } from "vitest";

import { loadAnalysisSearch, startAnalysisSearch } from "./discovery";

const baseSession = {
  id: "30000000-0000-4000-8000-000000000041",
  query: "investment documents",
  document_types: ["pdf"],
  tables_required: true,
  status: "completed",
  job_stage: "completed",
  error_code: null,
  candidate_count: 0,
  bytes_downloaded: 0,
  session_byte_limit: 536870912,
  cancellation_requested: false,
  created_at: "2026-09-02T12:00:00Z",
  updated_at: "2026-09-02T12:00:01Z",
  expires_at: "2026-09-02T13:00:00Z",
  completed_at: "2026-09-02T12:00:01Z",
  candidates: [],
};

afterEach(() => vi.unstubAllGlobals());

const searchRequest = {
  query: "investment documents",
  limit: 10,
  document_types: ["pdf" as const],
  include_domains: [],
  exclude_domains: [],
  tables_required: true,
  firecrawl_connection_id: "30000000-0000-4000-8000-000000000042",
};

it("maps the stable Firecrawl discovery failure to fixed safe copy", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          detail: {
            code: "firecrawl_discovery_unavailable",
            message: "Untrusted server copy must not be rendered.",
          },
        }),
        { status: 502 },
      ),
    ),
  );

  await expect(startAnalysisSearch(searchRequest, "test-csrf")).rejects.toThrow(
    "Firecrawl document discovery is unavailable. Test the selected connection and try again.",
  );
});

it("uses the fixed search fallback for an unknown failure code", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({ detail: { code: "unknown", message: "Untrusted server copy." } }),
        { status: 502 },
      ),
    ),
  );

  await expect(startAnalysisSearch(searchRequest, "test-csrf")).rejects.toThrow(
    "Document search is unavailable.",
  );
});

it("requires the safe Firecrawl connection snapshots in analysis responses", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify(baseSession), {
        status: 200,
      }),
    ),
  );

  await expect(loadAnalysisSearch(baseSession.id)).rejects.toThrow(
    "Document analysis returned invalid data.",
  );
});

it("accepts a complete analysis response with safe Firecrawl snapshots", async () => {
  const response = {
    ...baseSession,
    firecrawl_connection_id: "30000000-0000-4000-8000-000000000042",
    firecrawl_connection_name_snapshot: "Primary Firecrawl",
    firecrawl_connection_type_snapshot: "cloud",
  };
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify(response), {
        status: 200,
      }),
    ),
  );

  await expect(loadAnalysisSearch(baseSession.id)).resolves.toEqual(response);
});
