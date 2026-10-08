import { Button, Drawer } from "@mantine/core";
import { useMediaQuery } from "@mantine/hooks";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import {
  clearCompletedCollectionJobs,
  createDocumentExport,
  deleteDocument,
  loadCollectionJobs,
  loadDocuments,
  loadExports,
  retryCollectionJob,
} from "./api/collection";
import { CollectionInspector } from "./CollectionInspector";
import type { DashboardDestination } from "./DashboardShell";
import { DocumentUtilityBar } from "./DocumentUtilityBar";
import { OperationalIcon } from "./OperationalIcon";
import { StoredDocumentsPanel } from "./StoredDocumentsPanel";

interface CollectionQueueProps {
  csrfToken: string;
  activeDestination?: DashboardDestination;
  onDestinationClose?: () => void;
}

const activeCollectionStatuses = new Set(["queued", "downloading", "validating"]);
const activeExportStatuses = new Set(["queued", "exporting"]);

export function collectionPollInterval(
  jobs: ReadonlyArray<{ status: string }> | undefined,
): 2000 | false {
  return jobs?.some((job) => activeCollectionStatuses.has(job.status)) ? 2_000 : false;
}

export function exportPollInterval(
  exports: ReadonlyArray<{ status: string }> | undefined,
): 2000 | false {
  return exports?.some((entry) => activeExportStatuses.has(entry.status)) ? 2_000 : false;
}

export function documentPollInterval(
  jobs: ReadonlyArray<{ status: string; document_id: string | null }> | undefined,
  documents: ReadonlyArray<{ id: string }> | undefined,
): 3000 | false {
  if (jobs?.some((job) => activeCollectionStatuses.has(job.status))) return 3_000;
  const loadedDocumentIds = new Set(documents?.map((document) => document.id) ?? []);
  return jobs?.some(
    (job) =>
      (job.status === "completed" || job.status === "duplicate") &&
      job.document_id !== null &&
      !loadedDocumentIds.has(job.document_id),
  )
    ? 3_000
    : false;
}

export function CollectionQueue({
  csrfToken,
  activeDestination = "discover",
  onDestinationClose = () => undefined,
}: CollectionQueueProps) {
  const queryClient = useQueryClient();
  const compactQueue = useMediaQuery("(max-width: 63.99em)");
  const [queueOpened, setQueueOpened] = useState(false);
  const [documentsOpened, setDocumentsOpened] = useState(false);
  const [exportSubfolder, setExportSubfolder] = useState("");
  const jobs = useQuery({
    queryKey: ["collection-jobs"],
    queryFn: loadCollectionJobs,
    refetchInterval: (query) => collectionPollInterval(query.state.data?.items),
  });
  const documents = useQuery({
    queryKey: ["documents"],
    queryFn: loadDocuments,
    refetchInterval: (query) =>
      documentPollInterval(jobs.data?.items, query.state.data?.items),
  });
  const exports = useQuery({
    queryKey: ["exports"],
    queryFn: loadExports,
    refetchInterval: (query) => exportPollInterval(query.state.data),
  });
  const retry = useMutation({
    mutationFn: (jobId: string) => retryCollectionJob(jobId, csrfToken),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["collection-jobs"] });
    },
  });
  const clearCompleted = useMutation({
    mutationFn: () => clearCompletedCollectionJobs(csrfToken),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["collection-jobs"] });
    },
  });
  const createExport = useMutation({
    mutationFn: ({ documentId, directory }: { documentId: string; directory: string }) =>
      createDocumentExport(documentId, directory, csrfToken),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["exports"] });
    },
  });
  const removeDocument = useMutation({
    mutationFn: (documentId: string) => deleteDocument(documentId, csrfToken),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["documents"] }),
        queryClient.invalidateQueries({ queryKey: ["collection-jobs"] }),
        queryClient.invalidateQueries({ queryKey: ["exports"] }),
      ]);
    },
  });

  useEffect(() => {
    if (activeDestination !== "queue" || compactQueue) return;
    const queue = document.getElementById("queue");
    queue?.scrollIntoView?.({ block: "start" });
    queue?.focus({ preventScroll: true });
  }, [activeDestination, compactQueue]);

  const inspector = (
    <CollectionInspector
      jobs={jobs.data?.items}
      totalCount={jobs.data?.total}
      queryFailed={jobs.isError}
      retryFailed={retry.isError}
      retryPending={retry.isPending}
      retryJobId={retry.variables}
      onRetry={(jobId) => retry.mutate(jobId)}
      clearFailed={clearCompleted.isError}
      clearPending={clearCompleted.isPending}
      onClearCompleted={() => clearCompleted.mutate()}
      embedded={compactQueue}
    />
  );

  const documentCount = documents.data?.total;

  return (
    <>
      {compactQueue ? (
        <div className="queue-access" id="queue" tabIndex={-1}>
          <Button
            className="queue-access-button"
            variant="light"
            leftSection={<OperationalIcon name="queue" size={19} />}
            onClick={() => setQueueOpened(true)}
          >
            Open collection queue{jobs.data ? ` (${jobs.data.total})` : ""}
          </Button>
          <Drawer
            opened={queueOpened || activeDestination === "queue"}
            onClose={() => {
              setQueueOpened(false);
              if (activeDestination === "queue") onDestinationClose();
            }}
            title="Collection activity"
            position="right"
            size="md"
            closeButtonProps={{ "aria-label": "Close collection queue" }}
            classNames={{ content: "parserium-drawer", header: "parserium-drawer-header" }}
          >
            {inspector}
          </Drawer>
        </div>
      ) : (
        inspector
      )}

      <DocumentUtilityBar
        documentCount={documentCount}
        onPreferenceChange={setExportSubfolder}
        onOpenFolder={(normalizedSubfolder) => {
          setExportSubfolder(normalizedSubfolder);
          setDocumentsOpened(true);
        }}
      />

      <Drawer
        opened={documentsOpened || activeDestination === "documents"}
        onClose={() => {
          setDocumentsOpened(false);
          if (activeDestination === "documents") onDestinationClose();
        }}
        title="Document library"
        position="bottom"
        size="min(76dvh, 48rem)"
        closeButtonProps={{ "aria-label": "Close document library" }}
        classNames={{
          content: "parserium-drawer documents-drawer",
          header: "parserium-drawer-header",
          body: "documents-drawer-body",
        }}
      >
        <StoredDocumentsPanel
          documents={documents.data?.items}
          totalCount={documents.data?.total}
          exports={exports.data}
          queryFailed={documents.isError || exports.isError}
          exportFailed={createExport.isError}
          exportPending={createExport.isPending}
          exportDocumentId={createExport.variables?.documentId}
          exportSubfolder={exportSubfolder}
          onCreateExport={(documentId, directory) =>
            createExport.mutate({ documentId, directory })
          }
          deleteFailed={removeDocument.isError}
          deletePending={removeDocument.isPending}
          deleteDocumentId={removeDocument.variables}
          onDeleteDocument={async (documentId) => {
            removeDocument.reset();
            await removeDocument.mutateAsync(documentId);
          }}
        />
      </Drawer>
    </>
  );
}
