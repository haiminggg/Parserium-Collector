import { Loader, Text, Title, UnstyledButton } from "@mantine/core";
import { activeJob, dateText, type DocumentRecord } from "./api";
import { CloudOperationalIcon, DocumentStatus } from "./CloudPrimitives";
import { OverflowMarquee } from "./OverflowMarquee";

interface CloudActivitySnapshotProps {
  documents: DocumentRecord[];
  isPending: boolean;
  onOpenActivity: () => void;
}

const activityDescription = (document: DocumentRecord) => document.job_status === "succeeded" ? "Extracted Markdown is ready" : document.job_status === "running" ? "Extracting document structure" : activeJob(document) ? "Waiting for the parser" : document.job_status === "failed" || document.validation_status === "invalid" ? "The document needs review" : "Parsing was requested";

const activityState = (document: DocumentRecord) => document.job_status === "succeeded" ? "completed" : document.job_status === "failed" || document.validation_status === "invalid" ? "failed" : activeJob(document) ? "active" : "idle";

export function CloudActivitySnapshot({ documents, isPending, onOpenActivity }: CloudActivitySnapshotProps) {
  const jobs = documents
    .filter(document => document.job_id)
    .sort((left, right) => right.created_at - left.created_at)
    .slice(0, 3);

  return <aside className="cloud-activity-snapshot" aria-labelledby="cloud-activity-snapshot-title">
    <div className="cloud-activity-snapshot-heading">
      <div><Text className="cloud-eyebrow">Current page</Text><Title order={2} id="cloud-activity-snapshot-title">Parsing</Title></div>
      {!isPending && <span className="cloud-activity-snapshot-count" aria-label={`${jobs.length} recent parsing ${jobs.length === 1 ? "job" : "jobs"}`}>{String(jobs.length).padStart(2, "0")}</span>}
    </div>

    {isPending ? <div className="cloud-activity-snapshot-empty"><Loader size="xs" /><p>Loading parsing activity...</p></div> : !jobs.length ? <div className="cloud-activity-snapshot-empty"><CloudOperationalIcon name="queue" size={24} /><p>No parsing jobs on this page.</p><small>Start parsing from saved documents.</small></div> : <div className="cloud-activity-snapshot-timeline">
      <span className="cloud-activity-snapshot-line" aria-hidden="true" />
      <ol>{jobs.map(document => {
        const active = activeJob(document);
        return <li key={document.id} data-state={activityState(document)}>
          <span className="cloud-activity-snapshot-marker" aria-hidden="true"><span className="cloud-activity-snapshot-marker-core" />{active && <span className="cloud-activity-snapshot-marker-pulse" />}</span>
          <div className="cloud-activity-snapshot-entry">
            <span className="cloud-activity-snapshot-meta"><time dateTime={new Date(document.created_at * 1000).toISOString()}>{dateText(document.created_at)}</time><DocumentStatus doc={document} /></span>
            <strong><OverflowMarquee text={document.filename} /></strong>
            <span>{activityDescription(document)}</span>
          </div>
        </li>;
      })}</ol>
    </div>}

    <UnstyledButton className="cloud-text-button cloud-activity-snapshot-link" onClick={onOpenActivity}>Open full activity <CloudOperationalIcon name="arrow-right" size={14} /></UnstyledButton>
  </aside>;
}
