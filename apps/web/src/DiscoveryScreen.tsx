import { Alert, Text, Title } from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";

import { createAnalyzedCollectionJobs } from "./api/collection";
import {
  type AnalysisCandidate,
  type AnalysisSession,
  type DocumentDiscoveryRequest,
  type DocumentType,
  loadAnalysisSearch,
  startAnalysisSearch,
} from "./api/discovery";
import { DiscoveryCommandBar } from "./DiscoveryCommandBar";
import { DiscoveryResults } from "./DiscoveryResults";
import { FirecrawlConnectionSelector } from "./FirecrawlConnectionSelector";
import { DocumentMotion } from "./workspace/DocumentMotion";

export interface DiscoveryFirecrawlConfiguration {
  workspaceId: string;
  role: "owner" | "member";
  onConfigure: () => void;
}

interface DiscoveryScreenProps {
  csrfToken: string;
  firecrawl?: DiscoveryFirecrawlConfiguration;
}

const ACTIVE_SESSION_STATUSES = new Set(["queued", "running"]);

const discoveryFailureMessages: Record<string, string> = {
  connection_unavailable: "The selected Firecrawl connection is no longer available.",
  provider_authentication_failed: "Firecrawl rejected the configured credentials.",
  provider_rate_limited: "Firecrawl is rate limited. Wait briefly, then try again.",
  provider_timeout: "Firecrawl did not finish the search in time.",
  provider_invalid_response: "Firecrawl returned a response Parserium could not use.",
  provider_outcome_unknown: "The search result could not be confirmed safely. Try again.",
};

function pendingDiscoveryMessage(session: AnalysisSession): string | null {
  if (session.job_stage === "queued") return "Discovery queued. Waiting for a worker.";
  if (session.job_stage === "discovering") return "Searching for matching documents.";
  if (session.job_stage === "analyzing" && session.candidates.length === 0) {
    return "Preparing discovered documents for analysis.";
  }
  return null;
}

function domains(value: string): string[] {
  return value
    .split(",")
    .map((domain) => domain.trim())
    .filter(Boolean);
}

function candidateSelectable(candidate: AnalysisCandidate, tablesRequired: boolean): boolean {
  if (candidate.status === "ready" || candidate.status === "partial") return true;
  return !tablesRequired && candidate.status === "no_tables";
}

export function DiscoveryScreen({ csrfToken, firecrawl }: DiscoveryScreenProps) {
  const queryClient = useQueryClient();
  const [query, setQuery] = useState("");
  const [limit, setLimit] = useState(20);
  const [documentTypes, setDocumentTypes] = useState<DocumentType[]>(["pdf", "docx"]);
  const [tablesRequired, setTablesRequired] = useState(true);
  const [includeDomains, setIncludeDomains] = useState("");
  const [excludeDomains, setExcludeDomains] = useState("");
  const [validationError, setValidationError] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [firecrawlConnectionId, setFirecrawlConnectionId] = useState<string | null>(null);
  const [firecrawlConnectionUsable, setFirecrawlConnectionUsable] = useState(
    firecrawl === undefined,
  );
  const firecrawlBlocked =
    firecrawl !== undefined &&
    (!firecrawlConnectionUsable || firecrawlConnectionId === null);

  const search = useMutation({
    mutationFn: (request: DocumentDiscoveryRequest) =>
      startAnalysisSearch(request, csrfToken),
    onSuccess: (session) => {
      setSelectedIds([]);
      setSessionId(session.id);
      queryClient.setQueryData(["analysis-search", session.id], session);
    },
  });

  const analysis = useQuery({
    queryKey: ["analysis-search", sessionId],
    queryFn: () => loadAnalysisSearch(sessionId as string),
    enabled: sessionId !== null,
    staleTime: 500,
    refetchInterval: (queryResult) => {
      const current = queryResult.state.data as AnalysisSession | undefined;
      return current && ACTIVE_SESSION_STATUSES.has(current.status) ? 750 : false;
    },
  });

  const snapshot = analysis.data ?? search.data;
  const pendingMessage = snapshot ? pendingDiscoveryMessage(snapshot) : null;
  const jobFailure =
    snapshot?.job_stage === "failed"
      ? discoveryFailureMessages[snapshot.error_code ?? ""] ??
        "Document discovery failed. Check the selected Firecrawl connection and try again."
      : null;
  const collection = useMutation({
    mutationFn: (analysisIds: string[]) =>
      createAnalyzedCollectionJobs(analysisIds, csrfToken),
    onSuccess: async () => {
      setSelectedIds([]);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["collection-jobs"] }),
        sessionId
          ? queryClient.invalidateQueries({ queryKey: ["analysis-search", sessionId] })
          : Promise.resolve(),
        queryClient.invalidateQueries({ queryKey: ["documents"] }),
      ]);
    },
  });

  function toggleType(documentType: DocumentType, checked: boolean) {
    setDocumentTypes((current) =>
      checked
        ? [...current, documentType].filter(
            (value, index, values) => values.indexOf(value) === index,
          )
        : current.filter((value) => value !== documentType),
    );
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    search.reset();
    if (documentTypes.length === 0) {
      setValidationError("Select at least one document type.");
      return;
    }
    if (firecrawlBlocked) {
      setValidationError("Configure and validate a Firecrawl connection before discovering.");
      return;
    }
    const included = domains(includeDomains);
    const excluded = domains(excludeDomains);
    if (included.length > 0 && excluded.length > 0) {
      setValidationError("Use either include domains or exclude domains, not both.");
      return;
    }
    setValidationError(null);
    setSelectedIds([]);
    search.mutate({
      query: query.trim(),
      limit,
      document_types: documentTypes,
      include_domains: included,
      exclude_domains: excluded,
      tables_required: tablesRequired,
      firecrawl_connection_id: firecrawl !== undefined ? firecrawlConnectionId : null,
    });
  }

  function toggleCandidate(id: string, checked: boolean) {
    setSelectedIds((current) =>
      checked
        ? [...current, id]
            .filter((candidateId, index, values) => values.indexOf(candidateId) === index)
            .slice(0, 30)
        : current.filter((candidateId) => candidateId !== id),
    );
  }

  function selectAll() {
    if (!snapshot) return;
    setSelectedIds(
      snapshot.candidates
        .filter((candidate) => candidateSelectable(candidate, snapshot.tables_required))
        .slice(0, 30)
        .map((candidate) => candidate.id),
    );
  }

  function collectSelected() {
    if (selectedIds.length === 0) return;
    collection.mutate(selectedIds.slice(0, 30));
  }

  return (
    <section
      id="discover"
      className="discovery-panel"
      aria-labelledby="discovery-title"
      aria-busy={search.isPending || analysis.isFetching}
      tabIndex={-1}
    >
      <div className="discovery-hero" data-workspace-enter>
        <Title className="discovery-hero-title" order={1}>
          Find documents worth keeping.
        </Title>
        <Text className="collect-description">Search online. Review the contents. Keep the source.</Text>
      </div>
      <form className="discovery-form" onSubmit={submit}>
        <Title className="visually-hidden" id="discovery-title" order={2}>
          Document discovery
        </Title>
        <div className="discovery-command-frame">
          <DiscoveryCommandBar
            query={query}
            limit={limit}
            documentTypes={documentTypes}
            includeDomains={includeDomains}
            excludeDomains={excludeDomains}
            tablesRequired={tablesRequired}
            pending={search.isPending}
            connectionSelector={
              firecrawl ? (
                <FirecrawlConnectionSelector
                  workspaceId={firecrawl.workspaceId}
                  role={firecrawl.role}
                  value={firecrawlConnectionId}
                  onChange={setFirecrawlConnectionId}
                  onUsabilityChange={setFirecrawlConnectionUsable}
                  onConfigure={firecrawl.onConfigure}
                />
              ) : undefined
            }
            submitBlocked={firecrawlBlocked}
            submitBlockedReason="A healthy Firecrawl connection is required."
            onQueryChange={setQuery}
            onLimitChange={setLimit}
            onDocumentTypeChange={toggleType}
            onIncludeDomainsChange={setIncludeDomains}
            onExcludeDomainsChange={setExcludeDomains}
            onTablesRequiredChange={setTablesRequired}
          />
        </div>

        <div className="discovery-content">
          {validationError || search.isError || analysis.isError || collection.isError || jobFailure ? (
            <div className="discovery-feedback">
              {validationError ? <Alert role="alert">{validationError}</Alert> : null}
              {search.isError ? (
                <Alert role="alert" color="red" title="Discovery failed">
                  {search.error instanceof Error
                    ? search.error.message
                    : "Document search is unavailable."}
                </Alert>
              ) : null}
              {analysis.isError ? (
                <Alert role="alert" color="red" title="Analysis unavailable">
                  Local table analysis status is unavailable. Check the Parserium worker and try
                  again.
                </Alert>
              ) : null}
              {collection.isError ? (
                <Alert role="alert" color="red" title="Collection failed">
                  The selected analyzed documents could not be collected. Refresh and try again.
                </Alert>
              ) : null}
              {jobFailure ? (
                <Alert role="alert" color="red" title="Discovery failed">
                  {jobFailure}
                </Alert>
              ) : null}
            </div>
          ) : null}

          {snapshot ? (
            pendingMessage ? (
              <div className="discovery-empty-state" aria-live="polite">
                <Text className="section-kicker">Discovery in progress</Text>
                <Text fw={600}>{pendingMessage}</Text>
                <Text>You can leave this page open while Parserium continues in the background.</Text>
              </div>
            ) : snapshot.candidates.length === 0 && snapshot.job_stage !== "failed" ? (
              <div className="discovery-empty-state" aria-live="polite">
                <Text className="section-kicker">Discovered documents</Text>
                <Text fw={600}>0 direct document links found</Text>
                <Text>No direct PDF or DOCX links were found.</Text>
              </div>
            ) : (
              <DiscoveryResults
                candidates={snapshot.candidates}
                selectedIds={selectedIds}
                collecting={collection.isPending}
                tablesRequired={snapshot.tables_required}
                onToggle={toggleCandidate}
                onSelectAll={selectAll}
                onClear={() => setSelectedIds([])}
                onCollect={collectSelected}
              />
            )
          ) : (
            <div className="discovery-idle-state" aria-live="polite" role="region" aria-label="Getting started" tabIndex={0}>
              <DocumentMotion />
            </div>
          )}
        </div>
      </form>
    </section>
  );
}
