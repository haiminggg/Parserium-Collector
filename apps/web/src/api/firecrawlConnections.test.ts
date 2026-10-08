import { afterEach, describe, expect, it, vi } from "vitest";

import {
  createFirecrawlConnection,
  deleteFirecrawlConnection,
  listFirecrawlConnections,
  replaceFirecrawlCredential,
  testFirecrawlConnection,
  updateFirecrawlConnection,
} from "./firecrawlConnections";

const connectionId = "30000000-0000-4000-8000-000000000031";
const safeSummary = {
  id: connectionId,
  name: "Primary Firecrawl",
  connection_type: "cloud",
  status: "healthy",
  enabled: true,
  is_default: true,
  usable: true,
  last_validation_attempt_at: "2026-09-02T12:00:00Z",
  last_validation_success_at: "2026-09-02T12:00:00Z",
  last_failure_category: null,
} as const;

afterEach(() => vi.unstubAllGlobals());

describe("Firecrawl connection API client", () => {
  it("uses the safe workspace routes without retaining credentials", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url === "/api/v1/firecrawl/connections" && method === "GET") {
        return new Response(JSON.stringify({ connections: [safeSummary] }), { status: 200 });
      }
      if (url === "/api/v1/firecrawl/connections" && method === "POST") {
        return new Response(JSON.stringify(safeSummary), { status: 201 });
      }
      if (url === `/api/v1/firecrawl/connections/${connectionId}` && method === "PATCH") {
        return new Response(JSON.stringify({ ...safeSummary, name: "Renamed" }), {
          status: 200,
        });
      }
      if (
        url === `/api/v1/firecrawl/connections/${connectionId}/test` &&
        method === "POST"
      ) {
        return new Response(JSON.stringify(safeSummary), { status: 200 });
      }
      if (
        url === `/api/v1/firecrawl/connections/${connectionId}/credential` &&
        method === "PUT"
      ) {
        return new Response(
          JSON.stringify({ ...safeSummary, status: "never_validated", usable: false }),
          { status: 200 },
        );
      }
      if (url === `/api/v1/firecrawl/connections/${connectionId}` && method === "DELETE") {
        return new Response(null, { status: 204 });
      }
      return new Response(null, { status: 404 });
    });
    vi.stubGlobal("fetch", fetchMock);

    const listed = await listFirecrawlConnections();
    const created = await createFirecrawlConnection(
      {
        name: "Primary Firecrawl",
        connection_type: "cloud",
        credential: "create-test-only-token",
        is_default: true,
      },
      "csrf-token",
    );
    const updated = await updateFirecrawlConnection(
      connectionId,
      { name: "Renamed" },
      "csrf-token",
    );
    const tested = await testFirecrawlConnection(connectionId, "csrf-token");
    const replaced = await replaceFirecrawlCredential(
      connectionId,
      "replacement-test-only-token",
      "csrf-token",
    );
    await deleteFirecrawlConnection(connectionId, "csrf-token");

    expect(listed).toEqual([safeSummary]);
    expect(created).toEqual(safeSummary);
    expect(updated.name).toBe("Renamed");
    expect(tested).toEqual(safeSummary);
    expect(replaced.status).toBe("never_validated");
    expect(JSON.stringify([listed, created, updated, tested, replaced])).not.toContain(
      "test-only-token",
    );

    expect(fetchMock).toHaveBeenCalledTimes(6);
    for (const [, init] of fetchMock.mock.calls) {
      expect(init?.credentials).toBe("same-origin");
    }
    for (const [, init] of fetchMock.mock.calls.slice(1)) {
      expect(new Headers(init?.headers).get("X-Parserium-CSRF")).toBe("csrf-token");
    }

    const requestBodies = fetchMock.mock.calls.map(([, init]) => init?.body ?? null);
    expect(requestBodies).toEqual([
      null,
      JSON.stringify({
        name: "Primary Firecrawl",
        connection_type: "cloud",
        credential: "create-test-only-token",
        is_default: true,
      }),
      JSON.stringify({ name: "Renamed" }),
      null,
      JSON.stringify({ credential: "replacement-test-only-token" }),
      null,
    ]);
  });

  it("rejects response fields that could expose a credential envelope", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            connections: [
              {
                ...safeSummary,
                credential_envelope: {
                  key_id: "must-not-be-visible",
                  nonce: "must-not-be-visible",
                  ciphertext: "must-not-be-visible",
                  wrapped_data_key: "must-not-be-visible",
                },
              },
            ],
          }),
          { status: 200 },
        ),
      ),
    );

    await expect(listFirecrawlConnections()).rejects.toThrow(
      "Firecrawl connections returned invalid data.",
    );
  });
});
