import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { QueueScreen } from "./QueueScreen";


const completedJob = {
  id: "30000000-0000-4000-8000-000000000001",
  job_type: "discovery",
  title: "Bank investment reports",
  subtitle: "PDF and DOCX",
  state: "completed",
  stage: "completed",
  progress_percent: 100,
  created_by_user_id: "20000000-0000-4000-8000-000000000001",
  created_by_name: "Haiming",
  related_document_id: null,
  error_code: null,
  can_cancel: false,
  can_retry: false,
  can_delete: true,
  created_at: "2026-09-07T08:00:00Z",
  updated_at: "2026-09-07T08:01:00Z",
  completed_at: "2026-09-07T08:01:00Z",
};

const page = {
  items: [completedJob],
  total: 1,
  next_cursor: null,
  summary: { active: 2, queued: 3, failed: 4, completed: 12 },
};

function renderQueue() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <MantineProvider>
      <QueryClientProvider client={queryClient}>
        <QueueScreen csrfToken="queue-csrf" />
      </QueryClientProvider>
    </MantineProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("QueueScreen", () => {
  it("shows unified counts, filters, and expandable job details", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(JSON.stringify(page), { status: 200 })),
    );
    renderQueue();

    expect(await screen.findByRole("heading", { name: "Activity" })).toBeVisible();
    expect(await screen.findByText("12", { selector: ".activity-summary-value" })).toBeVisible();
    fireEvent.change(screen.getByLabelText("Job type"), { target: { value: "discovery" } });
    fireEvent.click(screen.getByRole("button", { name: "Show job details" }));

    expect(await screen.findByText("30000000-0000-4000-8000-000000000001")).toBeVisible();
    await waitFor(() =>
      expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).includes("job_type=discovery")))
        .toBe(true),
    );
  });

  it("confirms and deletes terminal history without deleting artifacts", async () => {
    let deleted = false;
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "DELETE") {
        deleted = true;
        expect(new Headers(init.headers).get("X-Parserium-CSRF")).toBe("queue-csrf");
        return Promise.resolve(new Response(null, { status: 204 }));
      }
      return Promise.resolve(
        new Response(JSON.stringify(deleted ? { ...page, items: [], total: 0 } : page), {
          status: 200,
        }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);
    renderQueue();

    const row = await screen.findByRole("row", { name: /Bank investment reports/ });
    fireEvent.click(within(row).getByRole("button", { name: "Delete history" }));
    const dialog = await screen.findByRole("dialog", { name: "Delete job history?" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete history" }));

    await waitFor(() => expect(deleted).toBe(true));
    await waitFor(() => expect(screen.queryByText("Bank investment reports")).not.toBeInTheDocument());
  });
});
