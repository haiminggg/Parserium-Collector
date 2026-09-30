import { MantineProvider } from "@mantine/core";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { StoredDocument } from "./api/collection";
import { StoredDocumentsPanel } from "./StoredDocumentsPanel";

const document: StoredDocument = {
  id: "00000000-0000-4000-8000-000000000003",
  sha256: "a".repeat(64),
  document_type: "pdf",
  media_type: "application/pdf",
  size_bytes: 2048,
  safe_filename: "2026 institutional investment allocation report.pdf",
  created_at: "2026-08-31T12:00:00Z",
};

afterEach(cleanup);

function panel(onDeleteDocument: (documentId: string) => Promise<void>) {
  return (
    <MantineProvider>
      <StoredDocumentsPanel
        documents={[document]}
        exports={[]}
        queryFailed={false}
        exportFailed={false}
        exportPending={false}
        exportDocumentId={undefined}
        exportSubfolder=""
        onCreateExport={() => undefined}
        deleteFailed={false}
        deletePending={false}
        deleteDocumentId={undefined}
        onDeleteDocument={onDeleteDocument}
      />
    </MantineProvider>
  );
}

describe("StoredDocumentsPanel", () => {
  it("opens an accessible filename-specific confirmation and cancel changes nothing", async () => {
    const onDeleteDocument = vi.fn<(documentId: string) => Promise<void>>();
    render(panel(onDeleteDocument));

    fireEvent.click(
      screen.getByRole("button", {
        name: `Delete ${document.safe_filename}`,
      }),
    );

    const dialog = await screen.findByRole("dialog", { name: "Delete stored document?" });
    expect(dialog).toHaveTextContent(document.safe_filename);
    expect(dialog).toHaveAttribute("aria-modal", "true");
    await waitFor(() => expect(screen.getByRole("button", { name: "Cancel" })).toHaveFocus());

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(onDeleteDocument).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(
      screen.getByRole("article", { name: `Stored document ${document.safe_filename}` }),
    ).toBeVisible();
  });

  it("disables duplicate confirmation while pending and closes only after success", async () => {
    let resolveDelete: (() => void) | undefined;
    const request = new Promise<void>((resolve) => {
      resolveDelete = resolve;
    });
    const deleteRequest = vi.fn<(documentId: string) => Promise<void>>(() => request);

    function Harness() {
      const [pending, setPending] = useState(false);
      return (
        <MantineProvider>
          <StoredDocumentsPanel
            documents={[document]}
            exports={[]}
            queryFailed={false}
            exportFailed={false}
            exportPending={false}
            exportDocumentId={undefined}
            exportSubfolder=""
            onCreateExport={() => undefined}
            deleteFailed={false}
            deletePending={pending}
            deleteDocumentId={pending ? document.id : undefined}
            onDeleteDocument={async (documentId) => {
              setPending(true);
              try {
                await deleteRequest(documentId);
              } finally {
                setPending(false);
              }
            }}
          />
        </MantineProvider>
      );
    }

    render(<Harness />);
    fireEvent.click(screen.getByRole("button", { name: `Delete ${document.safe_filename}` }));
    const confirm = await screen.findByRole("button", { name: "Delete document" });
    fireEvent.click(confirm);

    await waitFor(() => expect(confirm).toBeDisabled());
    fireEvent.click(confirm);
    expect(deleteRequest).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("dialog")).toBeVisible();

    await act(async () => resolveDelete?.());
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("keeps the confirmation and document visible after failure", async () => {
    function Harness() {
      const [failed, setFailed] = useState(false);
      return (
        <MantineProvider>
          <StoredDocumentsPanel
            documents={[document]}
            exports={[]}
            queryFailed={false}
            exportFailed={false}
            exportPending={false}
            exportDocumentId={undefined}
            exportSubfolder=""
            onCreateExport={() => undefined}
            deleteFailed={failed}
            deletePending={false}
            deleteDocumentId={document.id}
            onDeleteDocument={async () => {
              setFailed(true);
              throw new Error("delete failed");
            }}
          />
        </MantineProvider>
      );
    }

    render(<Harness />);
    fireEvent.click(screen.getByRole("button", { name: `Delete ${document.safe_filename}` }));
    fireEvent.click(await screen.findByRole("button", { name: "Delete document" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The stored document could not be deleted.",
    );
    expect(screen.getByRole("dialog")).toBeVisible();
    expect(
      screen.getByRole("article", { name: `Stored document ${document.safe_filename}` }),
    ).toBeVisible();
  });
});
