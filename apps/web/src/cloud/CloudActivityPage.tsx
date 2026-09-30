import { useGSAP } from "@gsap/react";
import { Alert, Button, Loader, Text, Title } from "@mantine/core";
import gsap from "gsap";
import { useRef, type ReactNode } from "react";
import { activeJob, dateText, type DocumentRecord, type SearchRecord, type Workspace } from "./api";
import { CloudEmpty, CloudOperationalIcon, DocumentStatus, documentStatusText } from "./CloudPrimitives";
import { EvidenceRail } from "./EvidenceRail";

gsap.registerPlugin(useGSAP);

interface CloudActivityPageProps {
  workspace: Workspace;
  documents: DocumentRecord[];
  searches: SearchRecord[];
  alerts: ReactNode;
  documentsPending: boolean;
  searchesPending: boolean;
  searchesError: Error | null;
  refreshing: boolean;
  onRefresh: () => void;
  onRefreshSearches: () => void;
  onInspect: (document: DocumentRecord) => void;
  onOpenSearch: (search: SearchRecord) => void;
  onOpenDocuments: () => void;
}

const activityDescription = (document: DocumentRecord) => document.job_status === "succeeded" ? "Extracted Markdown is ready" : document.job_status === "running" ? "Extracting document structure" : activeJob(document) ? "Waiting for the parser" : document.job_status === "failed" || document.validation_status === "invalid" ? "The document needs review" : "Parsing was requested";

export function CloudActivityPage({ workspace, documents, searches, alerts, documentsPending, searchesPending, searchesError, refreshing, onRefresh, onRefreshSearches, onInspect, onOpenSearch, onOpenDocuments }: CloudActivityPageProps) {
  const root = useRef<HTMLDivElement>(null);
  const jobs = documents.filter(document => document.job_id);
  const eventKey = jobs.map(document => document.id).join("|");
  const activeKey = jobs.filter(activeJob).map(document => document.id).join("|");

  useGSAP(() => {
    const media = gsap.matchMedia();
    media.add("(prefers-reduced-motion: no-preference)", () => {
      if (!jobs.length) return;
      gsap.timeline({ defaults: { ease: "power3.out" } })
        .from("[data-activity-line]", { scaleY: 0, transformOrigin: "top", duration: 0.7, clearProps: "transform" })
        .from("[data-activity-event]", { y: 18, autoAlpha: 0, duration: 0.45, stagger: 0.08, clearProps: "transform,opacity,visibility" }, "-=0.35");
    }, root);
    return () => media.revert();
  }, { scope: root, dependencies: [eventKey], revertOnUpdate: true });

  useGSAP(() => {
    const media = gsap.matchMedia();
    media.add("(prefers-reduced-motion: no-preference)", () => {
      gsap.to("[data-active-marker]", { scale: 1.45, autoAlpha: 0.35, repeat: -1, yoyo: true, duration: 1.15, ease: "sine.inOut", overwrite: "auto" });
    }, root);
    return () => media.revert();
  }, { scope: root, dependencies: [activeKey], revertOnUpdate: true });

  return <div ref={root} className="activity-page cloud-wall-page cloud-activity-page">
    <header className="cloud-wall-heading" data-workspace-enter>
      <div><Text className="cloud-wall-kicker">Activity / {workspace.name}</Text><Title className="cloud-page-title" order={1}>Activity</Title><Text className="cloud-wall-introduction">Recent searches and document processing in one place.</Text></div>
      <Button className="activity-refresh" variant="subtle" loading={refreshing} onClick={onRefresh}>Refresh activity</Button>
    </header>
    <EvidenceRail active="activity" />
    {alerts}
    <div className="activity-summary cloud-activity-summary cloud-trace-index" data-workspace-enter>
      <div className="activity-summary-item"><span className="activity-summary-label">Searches</span><span className="activity-summary-value">{searches.length}</span></div>
      <div className="activity-summary-item" data-state="active"><span className="activity-summary-label">Searching</span><span className="activity-summary-value">{searches.filter(search => search.status === "running").length}</span></div>
      <div className="activity-summary-item" data-state="active"><span className="activity-summary-label">Parsing on page</span><span className="activity-summary-value">{documents.filter(activeJob).length}</span></div>
      <div className="activity-summary-item"><span className="activity-summary-label">Ready on page</span><span className="activity-summary-value">{documents.filter(document => document.job_status === "succeeded").length}</span></div>
    </div>

    <div className="cloud-activity-editorial cloud-trace-wall">
      <section className="cloud-timeline-panel cloud-trace-lane cloud-parse-lane" aria-label="Document processing">
        <div className="cloud-section-heading cloud-timeline-heading"><div><Text className="cloud-eyebrow">Current page</Text><h2>Document processing</h2></div><Button variant="subtle" onClick={onOpenDocuments}>All documents</Button></div>
        {documentsPending ? <div className="cloud-empty"><Loader size="sm" /><p>Loading parsing activity...</p></div> : !jobs.length ? <CloudEmpty title="No parsing jobs on this page">Start parsing from your saved documents.</CloudEmpty> : <div className="cloud-timeline-wrap">
          <div className="cloud-timeline-line" data-activity-line aria-hidden="true" />
          <ol className="cloud-timeline-list">{jobs.map((document, index) => {
            const active = activeJob(document);
            const state = document.job_status === "succeeded" ? "completed" : document.job_status === "failed" || document.validation_status === "invalid" ? "failed" : active ? "active" : "idle";
            return <li key={document.id} className="cloud-timeline-event" data-activity-event data-state={state}>
              <span className="cloud-timeline-marker" aria-hidden="true"><span className="cloud-timeline-marker-core" />{active && <span className="cloud-timeline-marker-pulse" data-active-marker />}</span>
              <button type="button" className="cloud-timeline-card" onClick={() => onInspect(document)}>
                <span className="cloud-timeline-meta"><span><b>{String(index + 1).padStart(2, "0")}</b><i className="cloud-trace-type">Parse</i>{dateText(document.created_at)}</span><DocumentStatus doc={document} /></span>
                <strong>{document.filename}</strong>
                <span className="cloud-timeline-description">{activityDescription(document)}</span>
                <span className="cloud-timeline-action">{documentStatusText(document)} <CloudOperationalIcon name="arrow-right" size={14} /></span>
              </button>
            </li>;
          })}</ol>
        </div>}
        <p className="cloud-scope-note">Parsing activity reflects jobs attached to the current documents page.</p>
      </section>

      <aside className="activity-ledger cloud-search-ledger cloud-trace-lane cloud-search-lane" aria-label="Search history">
        <div className="cloud-section-heading"><div><Text className="cloud-eyebrow">Recent searches</Text><h2>Search history</h2></div><Button variant="subtle" onClick={onRefreshSearches}>Refresh</Button></div>
        {searchesError && <Alert color="red">{searchesError.message}</Alert>}
        {searchesPending ? <div className="cloud-empty"><Loader size="sm" /></div> : !searches.length ? <CloudEmpty title="Your search history will appear here">Every search is saved so you can return to its results.</CloudEmpty> : searches.map((search, index) => <button key={search.id} className="cloud-history-row cloud-search-row" onClick={() => onOpenSearch(search)}><span className="cloud-search-record"><span className="cloud-search-index" aria-hidden="true">{String(index + 1).padStart(2, "0")}</span><span><strong>{search.query}</strong><small>{dateText(search.created_at)} · {search.file_type === "all" ? "PDF & DOCX" : search.file_type.toUpperCase()}</small></span></span><span>{search.status === "succeeded" ? `${search.results.length} results` : search.status === "running" ? "Searching" : "Failed"}<CloudOperationalIcon name="arrow-right" size={16} /></span></button>)}
      </aside>
    </div>
  </div>;
}
