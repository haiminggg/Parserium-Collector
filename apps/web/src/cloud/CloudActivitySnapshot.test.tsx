import { MantineProvider } from "@mantine/core";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { parseriumTheme } from "../theme";
import type { DocumentRecord } from "./api";
import { CloudActivitySnapshot } from "./CloudActivitySnapshot";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const document = (id: string, createdAt: number, status: string | null): DocumentRecord => ({
  id,
  filename: `${id}.pdf`,
  size_bytes: 2048,
  created_at: createdAt,
  source_url: null,
  validation_status: "valid",
  validation_error_code: null,
  job_id: status ? `job-${id}` : null,
  job_status: status,
  job_error_code: null,
});

const renderSnapshot = (documents: DocumentRecord[], onOpenActivity = vi.fn()) => {
  render(<MantineProvider theme={parseriumTheme}><CloudActivitySnapshot documents={documents} isPending={false} onOpenActivity={onOpenActivity} /></MantineProvider>);
  return onOpenActivity;
};

describe("CloudActivitySnapshot", () => {
  it("shows the latest three real parsing jobs as static timeline entries", () => {
    vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockImplementation(function (this: HTMLElement) {
      return this.classList.contains("cloud-overflow-marquee-viewport") ? 100 : 0;
    });
    vi.spyOn(HTMLElement.prototype, "scrollWidth", "get").mockImplementation(function (this: HTMLElement) {
      if (!this.classList.contains("cloud-overflow-marquee-content")) return 0;
      return this.textContent === "newest.pdf" ? 180 : 80;
    });

    renderSnapshot([
      document("oldest", 10, "succeeded"),
      document("newest", 40, "running"),
      document("second", 30, "queued"),
      document("third", 20, "failed"),
      document("not-parsed", 50, null),
    ]);

    const timeline = screen.getByRole("list");
    const entries = within(timeline).getAllByRole("listitem");
    expect(entries).toHaveLength(3);
    expect(entries.map(entry => within(entry).getByRole("strong").textContent)).toEqual(["newest.pdf", "second.pdf", "third.pdf"]);
    expect(screen.queryByText("oldest.pdf")).not.toBeInTheDocument();
    expect(screen.queryByText("not-parsed.pdf")).not.toBeInTheDocument();
    expect(within(timeline).queryByRole("button")).not.toBeInTheDocument();
    expect(screen.getByText("newest.pdf").closest("[data-overflow]")).toHaveAttribute("data-overflow", "true");
    expect(screen.getByText("second.pdf").closest("[data-overflow]")).toHaveAttribute("data-overflow", "false");
  });

  it("offers only the full Activity destination when the snapshot is empty", () => {
    const onOpenActivity = renderSnapshot([]);

    expect(screen.getByText("No parsing jobs on this page.")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Open full activity" }));
    expect(onOpenActivity).toHaveBeenCalledOnce();
  });
});
