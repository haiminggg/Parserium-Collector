import { afterEach, describe, expect, it, vi } from "vitest";

import { deleteActivityHistory, listActivity } from "./activity";


const page = {
  items: [
    {
      id: "30000000-0000-4000-8000-000000000001",
      job_type: "discovery",
      title: "Bank reports",
      subtitle: "PDF and DOCX",
      state: "completed",
      stage: "completed",
      progress_percent: 100,
      created_by_user_id: "20000000-0000-4000-8000-000000000001",
      created_by_name: "Owner",
      related_document_id: null,
      error_code: null,
      can_cancel: false,
      can_retry: false,
      can_delete: true,
      created_at: "2026-09-07T08:00:00Z",
      updated_at: "2026-09-07T08:01:00Z",
      completed_at: "2026-09-07T08:01:00Z",
    },
  ],
  total: 1,
  next_cursor: null,
  summary: { active: 0, queued: 0, failed: 0, completed: 1 },
};


afterEach(() => vi.unstubAllGlobals());


describe("activity API", () => {
  it("loads and validates a cursor page", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(page), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(listActivity({ limit: 25, cursor: "next" })).resolves.toEqual(page);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/activity?limit=25&cursor=next",
      expect.objectContaining({ headers: { Accept: "application/json" } }),
    );
  });

  it("rejects an incompatible response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ ...page, summary: { active: -1 } }), { status: 200 }),
      ),
    );

    await expect(listActivity()).rejects.toThrow("Activity history returned an invalid response.");
  });

  it("deletes history with CSRF protection", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    await deleteActivityHistory("discovery", page.items[0].id, "csrf-token");

    expect(fetchMock).toHaveBeenCalledWith(
      `/api/v1/activity/discovery/${page.items[0].id}`,
      expect.objectContaining({
        method: "DELETE",
        headers: expect.any(Headers),
      }),
    );
    const headers = new Headers(fetchMock.mock.calls[0][1].headers);
    expect(headers.get("X-Parserium-CSRF")).toBe("csrf-token");
  });
});
