import { Alert, Button, Group, Loader, Text, TextInput, Title } from "@mantine/core";
import type { ReactNode } from "react";
import { activeJob, dateText, prefix, sizeText, type DocumentRecord, type Workspace } from "./api";
import { CloudEmpty, CloudOperationalIcon, DocumentStatus } from "./CloudPrimitives";
import { EvidenceRail } from "./EvidenceRail";

interface CloudDocumentsPageProps {
  workspace: Workspace;
  documents: DocumentRecord[];
  visibleDocuments: DocumentRecord[];
  alerts: ReactNode;
  error: Error | null;
  isPending: boolean;
  isFetching: boolean;
  filter: string;
  busy: string;
  processingDisabled: boolean;
  offset: number;
  hasMore: boolean;
  onFilterChange: (value: string) => void;
  onUpload: () => void;
  onRefresh: () => void;
  onInspect: (document: DocumentRecord) => void;
  onParse: (document: DocumentRecord) => void;
  onDelete: (document: DocumentRecord) => void;
  onPrevious: () => void;
  onNext: () => void;
  parserPicker?: ReactNode;
}

export function CloudDocumentsPage({ workspace, documents, visibleDocuments, alerts, error, isPending, isFetching, filter, busy, processingDisabled, offset, hasMore, onFilterChange, onUpload, onRefresh, onInspect, onParse, onDelete, onPrevious, onNext, parserPicker }: CloudDocumentsPageProps) {
  const ready = documents.filter(document => document.job_status === "succeeded").length;
  const parsing = documents.filter(activeJob).length;
  const attention = documents.filter(document => document.job_status === "failed" || document.validation_status === "invalid").length;
  const featuredDocument = documents[0] || null;
  const stateFor = (document: DocumentRecord) => document.job_status === "succeeded" ? "ready" : document.job_status === "failed" || document.validation_status === "invalid" ? "failed" : activeJob(document) ? "active" : "unparsed";

  return <div className="activity-page cloud-wall-page cloud-documents-page">
    <header className="cloud-wall-heading" data-workspace-enter>
      <div>
        <Text className="cloud-wall-kicker">Documents / {workspace.name}</Text>
        <Title className="cloud-page-title" order={1}>Documents</Title>
        <Text className="cloud-wall-introduction">Saved originals and extracted output in one workspace.</Text>
      </div>
      <Button className="connections-primary-action" onClick={onUpload} leftSection={<CloudOperationalIcon name="file" />}>Upload document</Button>
    </header>

    <EvidenceRail active="documents" />
    {alerts}

    <section className="cloud-documents-overview" aria-label="Current corpus overview" data-workspace-enter>
      <div className="cloud-documents-overview-main">
        <section className="cloud-corpus-index" aria-labelledby="cloud-corpus-index-title">
          <div className="cloud-corpus-index-copy">
            <Text className="cloud-eyebrow">Current page</Text>
            <h2 id="cloud-corpus-index-title">Overview</h2>
            <p>Every count reflects only the documents loaded below.</p>
          </div>
          <div className="cloud-editorial-metrics" aria-label="Document status counts on this page">
            <div><strong>{documents.length}</strong><span>On page</span><CloudOperationalIcon name="file" size={20} /></div>
            <div><strong>{ready}</strong><span>Ready</span><CloudOperationalIcon name="queue" size={20} /></div>
            <div data-state="active"><strong>{parsing}</strong><span>Processing</span><CloudOperationalIcon name="health" size={20} /></div>
            <div data-state={attention ? "failed" : undefined}><strong>{attention}</strong><span>Needs attention</span><CloudOperationalIcon name="info" size={20} /></div>
          </div>
        </section>
      </div>
      {featuredDocument && <aside className="cloud-document-feature" aria-label="Saved document">
        <div className="cloud-document-feature-heading"><Text className="cloud-eyebrow">Saved document</Text><CloudOperationalIcon name="file" size={21} /></div>
        <button type="button" className="cloud-document-feature-title" onClick={() => onInspect(featuredDocument)}>
          <span className="activity-type-mark">{featuredDocument.filename.toLowerCase().endsWith(".docx") ? "DOCX" : "PDF"}</span>
          <span><strong>{featuredDocument.filename}</strong><small>{featuredDocument.source_url ? new URL(featuredDocument.source_url).hostname : "Uploaded document"}</small></span>
        </button>
        <div className="cloud-document-feature-meta"><span><small>Size</small><strong>{sizeText(featuredDocument.size_bytes)}</strong></span><span><small>Added</small><strong>{dateText(featuredDocument.created_at)}</strong></span></div>
        <div className="cloud-document-feature-state"><DocumentStatus doc={featuredDocument} /></div>
        <Button className="cloud-document-feature-action" size="sm" variant="light" loading={busy === featuredDocument.id} disabled={!!busy || processingDisabled || featuredDocument.validation_status === "invalid"} onClick={() => featuredDocument.job_id ? onInspect(featuredDocument) : onParse(featuredDocument)}>{featuredDocument.job_status === "succeeded" ? "View output" : featuredDocument.job_id ? "Details" : "Parse document"}</Button>
      </aside>}
    </section>

    {parserPicker && <section className="cloud-documents-parser" aria-label="Parser for new parse jobs">{parserPicker}<p className="cloud-modal-note">Applies to the next document you parse. A parsed document keeps the engine it used.</p></section>}

    <section className="cloud-library cloud-wall-surface" aria-labelledby="cloud-library-title">
      <div className="cloud-library-heading">
        <div><Text className="cloud-eyebrow">Document library</Text><h2 id="cloud-library-title">All documents</h2></div>
        <div className="cloud-library-toolbar">
          <TextInput aria-label="Filter documents on this page" placeholder="Filter by filename" value={filter} onChange={event => onFilterChange(event.target.value)} leftSection={<CloudOperationalIcon name="search" size={17} />} />
          <Button variant="subtle" color="dark" loading={isFetching} onClick={onRefresh}>Refresh</Button>
        </div>
      </div>

      {error ? <Alert color="red">{error.message}<Button variant="subtle" onClick={onRefresh}>Try again</Button></Alert> : isPending ? <div className="cloud-empty"><Loader size="sm" /><p>Loading your documents...</p></div> : !visibleDocuments.length ? <CloudEmpty title={filter ? "No matching filenames" : "A home for your documents"}>{filter ? "Try another filename or clear the filter." : "Upload a PDF or DOCX, or collect an original from a web search."}</CloudEmpty> :
      <div className="activity-ledger cloud-library-ledger cloud-corpus-ledger"><div className="activity-table-scroll"><table className="activity-table cloud-documents-table"><thead><tr><th>Document</th><th>Status</th><th>Size</th><th>Added</th><th>Actions</th></tr></thead><tbody>{visibleDocuments.map((document, index) => <tr className="activity-row" data-state={stateFor(document)} key={document.id}><td><button className="cloud-document-title activity-job-identity" onClick={() => onInspect(document)}><span className="cloud-corpus-index-number" aria-hidden="true">{String(index + 1).padStart(2, "0")}</span><span className="activity-type-mark">{document.filename.toLowerCase().endsWith(".docx") ? "DOCX" : "PDF"}</span><span className="activity-job-copy"><strong>{document.filename}</strong><span>{document.source_url ? new URL(document.source_url).hostname : "Uploaded document"}</span></span></button></td><td><DocumentStatus doc={document} /></td><td className="machine-data">{sizeText(document.size_bytes)}</td><td className="machine-data">{dateText(document.created_at)}</td><td><Group className="activity-row-actions" gap={6} wrap="nowrap">{!document.job_id && document.validation_status !== "invalid" ? <Button size="xs" variant="light" loading={busy === document.id} disabled={!!busy || processingDisabled} onClick={() => onParse(document)}>Parse</Button> : <Button variant="subtle" size="xs" onClick={() => onInspect(document)}>{document.job_status === "succeeded" ? "View output" : "Details"}</Button>}<Button component="a" href={`${prefix}/files/${document.id}`} variant="subtle" color="dark" size="xs" aria-label={`Download ${document.filename}`}><CloudOperationalIcon name="download" /></Button><Button variant="subtle" color="gray" size="xs" aria-label={`Delete ${document.filename}`} disabled={!!busy || activeJob(document)} onClick={() => onDelete(document)}><CloudOperationalIcon name="trash" /></Button></Group></td></tr>)}</tbody></table></div></div>}

      <div className="activity-pagination"><span className="machine-data">{documents.length ? `${offset + 1} to ${offset + documents.length}` : "0 documents"}</span><Group><Button variant="subtle" disabled={offset === 0 || isFetching} onClick={onPrevious}>Previous</Button><Button variant="subtle" disabled={!hasMore || isFetching} onClick={onNext}>Next</Button></Group></div>
    </section>

  </div>;
}
