import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  collectionPollInterval,
  CollectionQueue,
  documentPollInterval,
  exportPollInterval,
} from "./CollectionQueue";

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  vi.unstubAllGlobals();
});

const timestamp = "2026-08-25T12:00:00Z";

const activeJob = {
  id: "00000000-0000-4000-8000-000000000001",
  source_url: "https://bank.example/active.pdf",
  title: "Active report",
  expected_document_type: "pdf",
  status: "downloading",
  attempt_count: 1,
  available_at: timestamp,
  bytes_downloaded: 512,
  content_length: 1024,
  document_id: null,
  error_code: null,
  error_detail: null,
  error_retryable: null,
  created_at: timestamp,
  updated_at: timestamp,
  started_at: timestamp,
  completed_at: null,
};

const failedJob = {
  ...activeJob,
  id: "00000000-0000-4000-8000-000000000002",
  title: "Failed report",
  status: "failed",
  bytes_downloaded: 128,
  content_length: null,
  error_code: "download_timeout",
  error_detail: "The document server timed out.",
  error_retryable: true,
  completed_at: timestamp,
};

const completedJob = {
  ...activeJob,
  id: "00000000-0000-4000-8000-000000000005",
  title: "Completed report",
  status: "completed",
  bytes_downloaded: 1024,
  content_length: 1024,
  document_id: "00000000-0000-4000-8000-000000000003",
  completed_at: timestamp,
};

const document = {
  id: "00000000-0000-4000-8000-000000000003",
  sha256: "a".repeat(64),
  document_type: "pdf",
  media_type: "application/pdf",
  size_bytes: 2048,
  safe_filename: "bank-report.pdf",
  created_at: timestamp,
};

const completedExport = {
  id: "00000000-0000-4000-8000-000000000004",
  document_id: document.id,
  relative_directory: "bank/2026",
  target_filename: "bank-report.pdf",
  exported_relative_path: "bank/2026/bank-report.pdf",
  status: "completed",
  attempt_count: 1,
  available_at: timestamp,
  error_code: null,
  error_detail: null,
  created_at: timestamp,
  updated_at: timestamp,
  completed_at: timestamp,
};

function page<T>(items: T[], total = items.length) {
  return { items, total, next_cursor: null };
}

function renderQueue() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <MantineProvider>
      <QueryClientProvider client={queryClient}>
        <CollectionQueue csrfToken="queue-csrf" />
      </QueryClientProvider>
    </MantineProvider>,
  );
}

function useCompactViewport() {
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockImplementation(
      (query: string): MediaQueryList => ({
        matches: query === "(max-width: 63.99em)",
        media: query,
        onchange: null,
        addListener: () => undefined,
        removeListener: () => undefined,
        addEventListener: () => undefined,
        removeEventListener: () => undefined,
        dispatchEvent: () => false,
      }),
    ),
  );
}

async function openStoredDocuments() {
  const trigger = await screen.findByRole("button", {
    name: /^Open folder/,
  });
  fireEvent.click(trigger);
  const heading = await screen.findByRole("heading", { name: "Stored documents" });
  await waitFor(() => expect(heading).toBeVisible());
}

describe("CollectionQueue", () => {
  it("polls active work and suspends polling when work is idle", () => {
    expect(collectionPollInterval([activeJob])).toBe(2_000);
    expect(collectionPollInterval([{ ...activeJob, status: "completed" }])).toBe(false);
    expect(collectionPollInterval(undefined)).toBe(false);
    expect(exportPollInterval([{ ...completedExport, status: "exporting" }])).toBe(2_000);
    expect(exportPollInterval([completedExport])).toBe(false);
    expect(
      documentPollInterval(
        [{ ...activeJob, status: "completed", document_id: document.id }],
        [],
      ),
    ).toBe(3_000);
    expect(
      documentPollInterval(
        [{ ...activeJob, status: "completed", document_id: document.id }],
        [document],
      ),
    ).toBe(false);
  });

  it("keeps stored documents behind an explicit utility control", async () => {
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith("/api/v1/collection/jobs")) {
        return Promise.resolve(new Response(JSON.stringify(page([])), { status: 200 }));
      }
      if (path.endsWith("/api/v1/documents")) {
        return Promise.resolve(
          new Response(JSON.stringify(page([document], 243)), { status: 200 }),
        );
      }
      if (path.endsWith("/api/v1/exports")) {
        return Promise.resolve(new Response(JSON.stringify([]), { status: 200 }));
      }
      throw new Error(`Unexpected request: ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderQueue();

    const trigger = await screen.findByRole("button", {
      name: "Open folder, 243 stored documents",
    });
    expect(
      screen.queryByRole("article", { name: "Stored document bank-report.pdf" }),
    ).not.toBeInTheDocument();

    fireEvent.click(trigger);
    const storedDocument = await screen.findByRole("article", {
      name: "Stored document bank-report.pdf",
    });
    await waitFor(() => expect(storedDocument).toBeVisible());
    expect(screen.getByText("243 documents")).toBeVisible();
    expect(
      screen.getByRole("link", { name: "Download bank-report.pdf" }),
    ).toHaveAttribute(
      "href",
      `/api/v1/documents/${document.id}/download`,
    );
  });

  it("persists one normalized export subfolder and shares it with document exports", async () => {
    window.localStorage.setItem(
      "parserium.exportSubfolder.v1",
      "bank\\2026/./reports",
    );
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith("/api/v1/collection/jobs")) {
        return Promise.resolve(new Response(JSON.stringify(page([])), { status: 200 }));
      }
      if (path.endsWith("/api/v1/documents")) {
        return Promise.resolve(new Response(JSON.stringify(page([document])), { status: 200 }));
      }
      if (path.endsWith("/api/v1/exports")) {
        return Promise.resolve(new Response(JSON.stringify([]), { status: 200 }));
      }
      throw new Error(`Unexpected request: ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderQueue();

    const preference = await screen.findByLabelText("Export subfolder");
    expect(preference).toHaveValue("bank/2026/reports");
    fireEvent.change(preference, { target: { value: "funds\\2027" } });
    expect(window.localStorage.getItem("parserium.exportSubfolder.v1")).toBe(
      "funds/2027",
    );
    fireEvent.blur(preference);
    expect(preference).toHaveValue("funds/2027");

    await openStoredDocuments();
    expect(
      screen.getByLabelText("Export subfolder for bank-report.pdf"),
    ).toHaveValue("funds/2027");
    expect(screen.getByText("Exports filtered to funds/2027")).toBeVisible();
  });

  it("copies the normalized export subfolder with visible feedback", async () => {
    window.localStorage.setItem("parserium.exportSubfolder.v1", "bank/2026");
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith("/api/v1/collection/jobs")) {
        return Promise.resolve(new Response(JSON.stringify(page([])), { status: 200 }));
      }
      if (path.endsWith("/api/v1/documents")) {
        return Promise.resolve(new Response(JSON.stringify(page([])), { status: 200 }));
      }
      if (path.endsWith("/api/v1/exports")) {
        return Promise.resolve(new Response(JSON.stringify([]), { status: 200 }));
      }
      throw new Error(`Unexpected request: ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderQueue();

    fireEvent.click(await screen.findByRole("button", { name: "Copy export subfolder" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("bank/2026"));
    expect(screen.getByRole("status")).toHaveTextContent("Export subfolder copied");
  });

  it("shows progress, safe failures, retry, documents, and completed exports", async () => {
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      const method = init?.method ?? "GET";
      if (path.endsWith("/api/v1/collection/jobs") && method === "GET") {
        return Promise.resolve(
          new Response(JSON.stringify(page([activeJob, failedJob])), { status: 200 }),
        );
      }
      if (path.endsWith("/api/v1/documents") && method === "GET") {
        return Promise.resolve(new Response(JSON.stringify(page([document])), { status: 200 }));
      }
      if (path.endsWith("/api/v1/exports") && method === "GET") {
        return Promise.resolve(
          new Response(JSON.stringify([completedExport]), { status: 200 }),
        );
      }
      if (path.includes(`/collection/jobs/${failedJob.id}/retry`) && method === "POST") {
        expect(new Headers(init?.headers).get("X-Parserium-CSRF")).toBe("queue-csrf");
        return Promise.resolve(
          new Response(JSON.stringify({ ...failedJob, status: "queued" }), { status: 200 }),
        );
      }
      if (path.includes(`/documents/${document.id}/exports`) && method === "POST") {
        expect(new Headers(init?.headers).get("X-Parserium-CSRF")).toBe("queue-csrf");
        return Promise.resolve(
          new Response(JSON.stringify({ ...completedExport, status: "queued" }), {
            status: 202,
          }),
        );
      }
      throw new Error(`Unexpected request: ${method} ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderQueue();

    expect(await screen.findByRole("heading", { name: "Collection queue" })).toBeVisible();
    const activeJobArticle = await screen.findByRole("article", {
      name: "Collection job Active report",
    });
    await waitFor(() => expect(activeJobArticle).toBeVisible());
    expect(
      screen.getByRole("progressbar", { name: "Download progress for Active report" }),
    ).toHaveAttribute("aria-valuenow", "50");
    expect(screen.getAllByText("bank.example").length).toBeGreaterThan(0);
    expect(screen.getByText("Downloading")).toBeVisible();
    const activeTitle = screen.getByText("Active report");
    expect(activeTitle).toHaveAttribute("tabindex", "0");
    fireEvent.mouseEnter(activeTitle);
    expect(await screen.findByRole("tooltip")).toHaveTextContent("Active report");
    fireEvent.mouseLeave(activeTitle);
    await waitFor(() => expect(screen.queryByRole("tooltip")).not.toBeInTheDocument());
    fireEvent.focus(activeTitle);
    expect(await screen.findByRole("tooltip")).toHaveTextContent("Active report");
    fireEvent.blur(activeTitle);
    await waitFor(() => expect(screen.queryByRole("tooltip")).not.toBeInTheDocument());
    expect(screen.getByText("Failed report")).toHaveAttribute("tabindex", "0");
    expect(screen.queryByText("Extracting tables")).not.toBeInTheDocument();
    expect(screen.queryByText("Table validation passed")).not.toBeInTheDocument();
    await openStoredDocuments();
    const storedDocument = await screen.findByRole("article", {
      name: "Stored document bank-report.pdf",
    });
    const documentExport = await screen.findByRole("group", {
      name: "Document export bank-report.pdf",
    });
    expect(storedDocument).toBeVisible();
    expect(documentExport).toBeVisible();
    const storedTitle = within(storedDocument).getByText("bank-report.pdf");
    fireEvent.mouseEnter(storedTitle);
    expect(await screen.findByRole("tooltip")).toHaveTextContent("bank-report.pdf");
    fireEvent.mouseLeave(storedTitle);
    await waitFor(() => expect(screen.queryByRole("tooltip")).not.toBeInTheDocument());
    const exportedTitle = within(documentExport).getByText("bank-report.pdf");
    fireEvent.focus(exportedTitle);
    expect(await screen.findByRole("tooltip")).toHaveTextContent("bank-report.pdf");
    expect(await screen.findByText("512 of 1024 bytes (50%)")).toBeVisible();
    expect(screen.getByText("128 bytes downloaded")).toBeVisible();
    expect(screen.getByText("The document server timed out.")).toBeVisible();
    expect(screen.getByText("bank/2026/bank-report.pdf")).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "Retry Failed report" }));
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          ([input, init]) =>
            String(input).includes(`/collection/jobs/${failedJob.id}/retry`) &&
            (init as RequestInit | undefined)?.method === "POST",
        ),
      ).toBe(true),
    );
  });

  it("clears only completed history through the protected collection endpoint", async () => {
    let cleared = false;
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      const method = init?.method ?? "GET";
      if (path.endsWith("/api/v1/collection/jobs") && method === "GET") {
        return Promise.resolve(
          new Response(
            JSON.stringify(page(cleared ? [failedJob] : [completedJob, failedJob])),
            { status: 200 },
          ),
        );
      }
      if (path.endsWith("/api/v1/collection/jobs/completed") && method === "DELETE") {
        expect(new Headers(init?.headers).get("X-Parserium-CSRF")).toBe("queue-csrf");
        cleared = true;
        return Promise.resolve(new Response(null, { status: 204 }));
      }
      if (path.endsWith("/api/v1/documents")) {
        return Promise.resolve(new Response(JSON.stringify(page([])), { status: 200 }));
      }
      if (path.endsWith("/api/v1/exports")) {
        return Promise.resolve(new Response(JSON.stringify([]), { status: 200 }));
      }
      throw new Error(`Unexpected request: ${method} ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderQueue();

    const completedArticle = await screen.findByRole("article", {
      name: "Collection job Completed report",
    });
    await waitFor(() => expect(completedArticle).toBeVisible());
    fireEvent.click(screen.getByRole("button", { name: "Clear completed" }));

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          ([input, init]) =>
            String(input).endsWith("/api/v1/collection/jobs/completed") &&
            (init as RequestInit | undefined)?.method === "DELETE",
        ),
      ).toBe(true),
    );
    await waitFor(() =>
      expect(
        screen.queryByRole("article", { name: "Collection job Completed report" }),
      ).not.toBeInTheDocument(),
    );
    expect(
      screen.getByRole("article", { name: "Collection job Failed report" }),
    ).toBeVisible();
    expect(screen.queryByRole("button", { name: "Clear completed" })).not.toBeInTheDocument();
  });

  it("validates export subfolders before sending and submits contained paths", async () => {
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      const method = init?.method ?? "GET";
      if (path.endsWith("/api/v1/collection/jobs") && method === "GET") {
        return Promise.resolve(new Response(JSON.stringify(page([])), { status: 200 }));
      }
      if (path.endsWith("/api/v1/documents") && method === "GET") {
        return Promise.resolve(new Response(JSON.stringify(page([document])), { status: 200 }));
      }
      if (path.endsWith("/api/v1/exports") && method === "GET") {
        return Promise.resolve(new Response(JSON.stringify([]), { status: 200 }));
      }
      if (path.includes(`/documents/${document.id}/exports`) && method === "POST") {
        return Promise.resolve(
          new Response(JSON.stringify({ ...completedExport, status: "queued" }), {
            status: 202,
          }),
        );
      }
      throw new Error(`Unexpected request: ${method} ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderQueue();

    await openStoredDocuments();
    const input = await screen.findByLabelText("Export subfolder for bank-report.pdf");
    fireEvent.change(input, { target: { value: "../outside" } });
    fireEvent.click(screen.getByRole("button", { name: "Export bank-report.pdf" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("relative subfolder");
    expect(
      fetchMock.mock.calls.some(
        ([input, init]) =>
          String(input).includes(`/documents/${document.id}/exports`) &&
          (init as RequestInit | undefined)?.method === "POST",
      ),
    ).toBe(false);

    fireEvent.change(input, { target: { value: "bank/2026" } });
    fireEvent.click(screen.getByRole("button", { name: "Export bank-report.pdf" }));
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          ([input, init]) =>
            String(input).includes(`/documents/${document.id}/exports`) &&
            (init as RequestInit | undefined)?.method === "POST" &&
            JSON.parse(String((init as RequestInit).body)).relative_directory === "bank/2026",
        ),
      ).toBe(true),
    );
  });

  it("keeps the export subfolder stable across queued input events", async () => {
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith("/api/v1/collection/jobs")) {
        return Promise.resolve(new Response(JSON.stringify(page([])), { status: 200 }));
      }
      if (path.endsWith("/api/v1/documents")) {
        return Promise.resolve(new Response(JSON.stringify(page([document])), { status: 200 }));
      }
      if (path.endsWith("/api/v1/exports")) {
        return Promise.resolve(new Response(JSON.stringify([]), { status: 200 }));
      }
      throw new Error(`Unexpected request: ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderQueue();

    await openStoredDocuments();
    const input = await screen.findByLabelText("Export subfolder for bank-report.pdf");
    const setNativeValue = Object.getOwnPropertyDescriptor(
      HTMLInputElement.prototype,
      "value",
    )?.set;
    expect(setNativeValue).toBeDefined();

    expect(() => {
      act(() => {
        setNativeValue?.call(input, "a");
        input.dispatchEvent(new Event("input", { bubbles: true }));
        setNativeValue?.call(input, "ab");
        input.dispatchEvent(new Event("input", { bubbles: true }));
      });
    }).not.toThrow();
    expect(input).toHaveValue("ab");
  });

  it("deletes a confirmed document once with CSRF and refreshes related data", async () => {
    let documentDeleted = false;
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      const method = init?.method ?? "GET";
      if (path.endsWith("/api/v1/collection/jobs") && method === "GET") {
        return Promise.resolve(new Response(JSON.stringify(page([])), { status: 200 }));
      }
      if (path.endsWith("/api/v1/documents") && method === "GET") {
        return Promise.resolve(
          new Response(JSON.stringify(page(documentDeleted ? [] : [document])), { status: 200 }),
        );
      }
      if (path.endsWith("/api/v1/exports") && method === "GET") {
        return Promise.resolve(new Response(JSON.stringify([]), { status: 200 }));
      }
      if (path.endsWith(`/api/v1/documents/${document.id}`) && method === "DELETE") {
        expect(new Headers(init?.headers).get("X-Parserium-CSRF")).toBe("queue-csrf");
        expect(init?.credentials).toBe("same-origin");
        documentDeleted = true;
        return Promise.resolve(new Response(null, { status: 204 }));
      }
      throw new Error(`Unexpected request: ${method} ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderQueue();
    await openStoredDocuments();

    fireEvent.click(screen.getByRole("button", { name: "Delete bank-report.pdf" }));
    fireEvent.click(await screen.findByRole("button", { name: "Delete document" }));

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.filter(
          ([input, init]) =>
            String(input).endsWith(`/api/v1/documents/${document.id}`) &&
            (init as RequestInit | undefined)?.method === "DELETE",
        ),
      ).toHaveLength(1),
    );
    await waitFor(() =>
      expect(
        screen.queryByRole("article", { name: "Stored document bank-report.pdf" }),
      ).not.toBeInTheDocument(),
    );
    await waitFor(() =>
      expect(
        screen.queryByRole("dialog", { name: "Delete stored document?" }),
      ).not.toBeInTheDocument(),
    );
    expect(
      fetchMock.mock.calls.filter(([input]) => String(input).endsWith("/api/v1/documents"))
        .length,
    ).toBeGreaterThan(1);
    expect(
      fetchMock.mock.calls.filter(([input]) => String(input).endsWith("/api/v1/exports")).length,
    ).toBeGreaterThan(1);
    expect(
      fetchMock.mock.calls.filter(([input]) =>
        String(input).endsWith("/api/v1/collection/jobs"),
      ).length,
    ).toBeGreaterThan(1);
  });

  it("moves collection activity into a drawer at compact widths", async () => {
    useCompactViewport();
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith("/api/v1/collection/jobs")) {
        return Promise.resolve(new Response(JSON.stringify(page([], 38)), { status: 200 }));
      }
      if (path.endsWith("/api/v1/documents")) {
        return Promise.resolve(new Response(JSON.stringify(page([])), { status: 200 }));
      }
      if (path.endsWith("/api/v1/exports")) {
        return Promise.resolve(new Response(JSON.stringify([]), { status: 200 }));
      }
      throw new Error(`Unexpected request: ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderQueue();

    const trigger = await screen.findByRole("button", { name: "Open collection queue (38)" });
    expect(screen.queryByRole("heading", { name: "Collection queue" })).not.toBeInTheDocument();

    fireEvent.click(trigger);
    const heading = await screen.findByRole("heading", { name: "Collection queue" });
    await waitFor(() => expect(heading).toBeVisible());
    expect(screen.getByLabelText("38 collection jobs")).toBeVisible();
  });
});
