import {
  ActionIcon,
  Alert,
  Button,
  Group,
  Modal,
  Text,
  TextInput,
  Title,
  Tooltip,
} from "@mantine/core";
import { useState } from "react";

import {
  normalizeExportDirectory,
  type DocumentExport,
  type StoredDocument,
} from "./api/collection";
import { OperationalIcon } from "./OperationalIcon";

interface StoredDocumentsPanelProps {
  documents: StoredDocument[] | undefined;
  totalCount?: number;
  exports: DocumentExport[] | undefined;
  queryFailed: boolean;
  exportFailed: boolean;
  exportPending: boolean;
  exportDocumentId: string | undefined;
  exportSubfolder: string;
  onCreateExport: (documentId: string, directory: string) => void;
  deleteFailed: boolean;
  deletePending: boolean;
  deleteDocumentId: string | undefined;
  onDeleteDocument: (documentId: string) => Promise<void>;
}

const exportStatusPresentation: Record<string, { tone: string; label: string }> = {
  queued: { tone: "neutral", label: "Waiting" },
  exporting: { tone: "active", label: "Exporting" },
  completed: { tone: "ready", label: "Completed" },
  failed: { tone: "error", label: "Failed" },
};

export function StoredDocumentsPanel({
  documents,
  totalCount,
  exports,
  queryFailed,
  exportFailed,
  exportPending,
  exportDocumentId,
  exportSubfolder,
  onCreateExport,
  deleteFailed,
  deletePending,
  deleteDocumentId,
  onDeleteDocument,
}: StoredDocumentsPanelProps) {
  const [exportDirectories, setExportDirectories] = useState<Record<string, string>>({});
  const [exportValidationError, setExportValidationError] = useState<string | null>(null);
  const [documentToDelete, setDocumentToDelete] = useState<StoredDocument | null>(null);
  const [submittingDocumentId, setSubmittingDocumentId] = useState<string | null>(null);
  const visibleExports = exportSubfolder
    ? exports?.filter((entry) => entry.relative_directory === exportSubfolder)
    : exports;

  function submitExport(documentId: string) {
    const normalized = normalizeExportDirectory(
      exportDirectories[documentId] ?? exportSubfolder,
    );
    if (normalized === null) {
      setExportValidationError("Enter a relative subfolder without drive letters or parent paths.");
      return;
    }
    setExportValidationError(null);
    onCreateExport(documentId, normalized);
  }

  const deletionPending =
    documentToDelete !== null &&
    (submittingDocumentId === documentToDelete.id ||
      (deletePending && deleteDocumentId === documentToDelete.id));

  async function confirmDeletion() {
    if (documentToDelete === null || deletionPending) return;
    const documentId = documentToDelete.id;
    setSubmittingDocumentId(documentId);
    try {
      await onDeleteDocument(documentId);
      setDocumentToDelete(null);
    } catch {
      // The owning mutation renders a safe error while the confirmation stays open.
    } finally {
      setSubmittingDocumentId(null);
    }
  }

  return (
    <>
      <section className="documents-panel" aria-labelledby="documents-title">
      <div className="documents-panel-heading">
        <div>
          <Text className="section-kicker">Local document store</Text>
          <Title order={2} id="documents-title">
            Stored documents
          </Title>
        </div>
        <Text className="machine-data">
          {documents
            ? `${totalCount ?? documents.length} documents`
            : "Loading documents"}
        </Text>
      </div>

      {queryFailed ? (
        <Alert role="alert" color="red" title="Stored documents unavailable">
          Parserium could not load stored documents or export status.
        </Alert>
      ) : null}
      {exportFailed ? (
        <Alert role="alert" color="red">
          The document export could not be queued.
        </Alert>
      ) : null}
      {exportValidationError ? <Alert role="alert">{exportValidationError}</Alert> : null}

      <div className="document-list">
        {documents?.length === 0 ? <Text size="sm">No stored documents yet.</Text> : null}
        {documents?.map((document) => (
          <article
            key={document.id}
            className="document-row"
            aria-label={`Stored document ${document.safe_filename}`}
          >
            <span
              className={`file-glyph file-glyph-${document.document_type}`}
              aria-hidden="true"
            >
              <OperationalIcon
                name={document.document_type === "pdf" ? "file-pdf" : "file-docx"}
                size={25}
              />
            </span>
            <div className="document-identity">
              <Tooltip
                label={document.safe_filename}
                openDelay={300}
                withArrow
                multiline
                maw={420}
                events={{ hover: true, focus: true, touch: false }}
                classNames={{ tooltip: "document-name-tooltip" }}
              >
                <Text fw={600} tabIndex={0}>
                  {document.safe_filename}
                </Text>
              </Tooltip>
              <Text className="machine-data">
                {document.size_bytes} bytes • {document.media_type}
              </Text>
            </div>
            <Text className="file-type">{document.document_type.toUpperCase()}</Text>
            <TextInput
              className="export-directory"
              label={`Export subfolder for ${document.safe_filename}`}
              value={exportDirectories[document.id] ?? exportSubfolder}
              placeholder="Optional, for example bank/2026"
              onChange={(event) => {
                const value = event.currentTarget.value;
                setExportDirectories((current) => ({
                  ...current,
                  [document.id]: value,
                }));
              }}
            />
            <div className="document-actions">
              <Button
                component="a"
                variant="subtle"
                leftSection={<OperationalIcon name="download" size={17} />}
                href={`/api/v1/documents/${encodeURIComponent(document.id)}/download`}
                download={document.safe_filename}
                aria-label={`Download ${document.safe_filename}`}
              >
                Download
              </Button>
              <Button
                variant="light"
                leftSection={<OperationalIcon name="folder" size={17} />}
                onClick={() => submitExport(document.id)}
                loading={exportPending && exportDocumentId === document.id}
                aria-label={`Export ${document.safe_filename}`}
              >
                Export
              </Button>
              <Tooltip label={`Delete ${document.safe_filename}`} withArrow openDelay={300}>
                <ActionIcon
                  className="document-delete-action"
                  variant="subtle"
                  color="red"
                  size="lg"
                  aria-label={`Delete ${document.safe_filename}`}
                  onClick={() => setDocumentToDelete(document)}
                >
                  <OperationalIcon name="trash" size={18} />
                </ActionIcon>
              </Tooltip>
            </div>
          </article>
        ))}
      </div>

      <div className="export-ledger">
        <div className="export-ledger-heading">
          <div>
            <Text className="section-kicker">Recent exports</Text>
            {exportSubfolder ? (
              <Text className="export-filter-summary">Exports filtered to {exportSubfolder}</Text>
            ) : null}
          </div>
          <Text className="machine-data">{visibleExports?.length ?? 0}</Text>
        </div>
        {visibleExports?.length === 0 ? (
          <Text size="sm">
            {exportSubfolder ? "No export jobs in this subfolder yet." : "No export jobs yet."}
          </Text>
        ) : null}
        {visibleExports?.map((entry) => {
          const presentation = exportStatusPresentation[entry.status] ?? {
            tone: "neutral",
            label: entry.status,
          };
          return (
            <div
              key={entry.id}
              className="export-row"
              role="group"
              aria-label={`Document export ${entry.target_filename}`}
            >
              <div>
                <Tooltip
                  label={entry.target_filename}
                  openDelay={300}
                  withArrow
                  multiline
                  maw={420}
                  events={{ hover: true, focus: true, touch: false }}
                  classNames={{ tooltip: "document-name-tooltip" }}
                >
                  <Text className="export-filename" size="sm" fw={600} tabIndex={0}>
                    {entry.target_filename}
                  </Text>
                </Tooltip>
                {entry.exported_relative_path ? (
                  <Text className="machine-data">{entry.exported_relative_path}</Text>
                ) : null}
                {entry.error_detail ? (
                  <Text className="queue-error" size="sm">
                    {entry.error_detail}
                  </Text>
                ) : null}
              </div>
              <span className="queue-status" data-tone={presentation.tone}>
                {presentation.label}
              </span>
            </div>
          );
        })}
      </div>
      </section>

      <Modal
        opened={documentToDelete !== null}
        onClose={() => {
          if (!deletionPending) setDocumentToDelete(null);
        }}
        title="Delete stored document?"
        centered
        size="md"
        closeOnClickOutside={!deletionPending}
        closeOnEscape={!deletionPending}
        closeButtonProps={{
          "aria-label": "Close delete confirmation",
          disabled: deletionPending,
        }}
        classNames={{
          content: "document-delete-modal",
          header: "document-delete-modal-header",
          body: "document-delete-modal-body",
        }}
      >
        {documentToDelete ? (
          <div className="document-delete-confirmation">
            <Text>
              Delete <strong>{documentToDelete.safe_filename}</strong> from stored documents?
            </Text>
            <Text size="sm" className="document-delete-warning">
              This removes Parserium's stored copy and cannot be undone from the dashboard.
            </Text>
            {deleteFailed && deleteDocumentId === documentToDelete.id ? (
              <Alert role="alert" color="red" title="Delete failed">
                The stored document could not be deleted.
              </Alert>
            ) : null}
            <Group justify="flex-end" gap="sm" className="document-delete-controls">
              <Button
                variant="default"
                data-autofocus
                disabled={deletionPending}
                onClick={() => setDocumentToDelete(null)}
              >
                Cancel
              </Button>
              <Button
                color="red"
                leftSection={<OperationalIcon name="trash" size={17} />}
                loading={deletionPending}
                disabled={deletionPending}
                onClick={() => void confirmDeletion()}
              >
                Delete document
              </Button>
            </Group>
          </div>
        ) : null}
      </Modal>
    </>
  );
}
