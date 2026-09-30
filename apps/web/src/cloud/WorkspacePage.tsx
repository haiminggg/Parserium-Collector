import { useEffect, useRef, useState, type ReactNode } from "react";
import { Alert, Badge, Button, Checkbox, Drawer, Group, Loader, Modal, PasswordInput, Text, TextInput, Title, UnstyledButton } from "@mantine/core";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ParseriumShell } from "../ParseriumShell";
import { activeJob, api, dateText, errorMessage, jsonOptions, prefix, scoped, sizeText, type Connection, type DocumentRecord, type SearchRecord, type SearchResult, type Workspace } from "./api";
import { CloudActivitySnapshot } from "./CloudActivitySnapshot";
import { CloudActivityPage } from "./CloudActivityPage";
import { CloudDocumentsPage } from "./CloudDocumentsPage";
import { CloudEmpty as Empty, CloudOperationalIcon as OperationalIcon, DocumentStatus as Status } from "./CloudPrimitives";
import { EvidenceRail } from "./EvidenceRail";
import { OverflowMarquee } from "./OverflowMarquee";

type Section = "Collect" | "Documents" | "Activity";
const sections: Section[] = ["Collect", "Documents", "Activity"];
const cloudNavigation = sections.map(destination => ({ destination, label: destination }));
const initialSection = (): Section => sections.find(value => value.toLowerCase() === location.hash.slice(1)) || "Collect";

export function WorkspacePage({ workspace, accountSlot }: { workspace: Workspace; accountSlot: ReactNode }) {
  const client = useQueryClient();
  const [section, setSection] = useState<Section>(initialSection);
  const [offset, setOffset] = useState(0);
  const [filter, setFilter] = useState("");
  const [settings, setSettings] = useState(false);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState<{ text: string; error: boolean } | null>(null);
  const [selected, setSelected] = useState<DocumentRecord | null>(null);
  const [deleting, setDeleting] = useState<DocumentRecord | null>(null);
  const [markdown, setMarkdown] = useState<string | null>(null);
  const [outputLoading, setOutputLoading] = useState(false);
  const [outputError, setOutputError] = useState("");
  const [query, setQuery] = useState("");
  const [fileType, setFileType] = useState("all");
  const [limit, setLimit] = useState("10");
  const [searchId, setSearchId] = useState<string | null>(null);
  const [checked, setChecked] = useState<string[]>([]);
  const [focusedResultId, setFocusedResultId] = useState<string | null>(null);
  const [collectionState, setCollectionState] = useState<Record<string, string>>({});
  const [apiKey, setApiKey] = useState("");
  const [connectionNotice, setConnectionNotice] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);
  const mounted = useRef(true);
  const uploadAttempt = useRef<{ file: File; id: string } | null>(null);
  const requestIds = useRef(new Map<string, string>());
  const requestId = (key: string) => {
    const found = requestIds.current.get(key); if (found) return found;
    const id = crypto.randomUUID(); requestIds.current.set(key, id); return id;
  };
  const documents = useQuery({ queryKey: ["documents", workspace.id, offset], queryFn: ({ signal }) => api<{ documents: DocumentRecord[]; has_more: boolean }>(scoped("/documents", workspace.id) + `&offset=${offset}`, { signal }),
    refetchInterval: state => state.state.data?.documents.some(activeJob) ? 2000 : false });
  const searches = useQuery({ queryKey: ["searches", workspace.id], queryFn: ({ signal }) => api<{ searches: SearchRecord[] }>(scoped("/discovery/searches", workspace.id), { signal }),
    refetchInterval: state => state.state.data?.searches.some(search => search.status === "running") ? 3000 : false });
  const connection = useQuery({ queryKey: ["connection", workspace.id], queryFn: ({ signal }) => api<Connection>(scoped("/discovery/connection", workspace.id), { signal }) });
  const health = useQuery({ queryKey: ["health"], queryFn: ({ signal }) => api<{ processing: string }>("/health", { signal }), staleTime: 30000 });
  const history = searches.data?.searches || [];
  const currentSearch = history.find(item => item.id === searchId) || history[0];
  const docs = documents.data?.documents || [];
  const visibleDocs = docs.filter(doc => doc.filename.toLowerCase().includes(filter.toLowerCase()));
  const viewedDocument = docs.find(doc => doc.id === selected?.id) || selected;
  const refresh = () => Promise.all([client.invalidateQueries({ queryKey: ["documents", workspace.id] }), client.invalidateQueries({ queryKey: ["searches", workspace.id] })]);
  const announce = (text: string, error = false) => { if (mounted.current) setNotice({ text, error }); };
  const navigate = (value: Section) => { setSection(value); historyReplace(value); };
  useEffect(() => {
    mounted.current = true;
    const change = () => setSection(initialSection());
    window.addEventListener("hashchange", change);
    return () => { mounted.current = false; window.removeEventListener("hashchange", change); };
  }, []);
  useEffect(() => {
    setChecked([]);
    setFocusedResultId(currentSearch?.results[0]?.id || null);
  }, [currentSearch?.id, currentSearch?.results[0]?.id]);
  useEffect(() => {
    const controller = new AbortController(); setMarkdown(null); setOutputError("");
    if (!viewedDocument?.job_id || viewedDocument.job_status !== "succeeded") { setOutputLoading(false); return; }
    setOutputLoading(true);
    void fetch(`${prefix}/parse-jobs/${encodeURIComponent(viewedDocument.job_id)}/output`, { signal: controller.signal, cache: "no-store", credentials: "same-origin" })
      .then(async response => {
        if (response.status === 401) window.dispatchEvent(new Event("parserium-session-expired"));
        if (!response.ok) throw new Error("The saved output could not be loaded. Close and reopen this document to retry.");
        return response.text();
      }).then(text => { if (!controller.signal.aborted) setMarkdown(text); })
      .catch(error => { if (!controller.signal.aborted) setOutputError((error as Error).message); })
      .finally(() => { if (!controller.signal.aborted) setOutputLoading(false); });
    return () => controller.abort();
  }, [viewedDocument?.job_id, viewedDocument?.job_status]);

  async function upload() {
    if (!file) return;
    if (!/\.(pdf|docx)$/i.test(file.name) || file.size < 5 || file.size > 10485760) { announce("Choose a PDF or DOCX between 5 bytes and 10 MiB.", true); return; }
    setBusy("upload");
    if (uploadAttempt.current?.file !== file) uploadAttempt.current = { file, id: crypto.randomUUID() };
    try {
      const type = file.name.toLowerCase().endsWith(".docx") ? "application/vnd.openxmlformats-officedocument.wordprocessingml.document" : "application/pdf";
      await api("/uploads?" + new URLSearchParams({ workspace: workspace.id, filename: file.name }), { method: "POST", body: file, headers: { "Content-Type": type, "Idempotency-Key": uploadAttempt.current.id } });
      setFile(null); uploadAttempt.current = null; setUploadOpen(false); setOffset(0); navigate("Documents");
      announce("Document saved. Choose Parse to extract its contents."); await refresh();
    } catch (error) { announce((error as Error).message, true); }
    finally { if (mounted.current) setBusy(""); }
  }
  async function parse(doc: DocumentRecord) {
    setBusy(doc.id); announce(`Validating ${doc.filename}. Larger files can take a moment.`);
    try {
      await api("/parse-jobs?document=" + encodeURIComponent(doc.id), { method: "POST", headers: { "Idempotency-Key": requestId("parse:" + doc.id) } });
      requestIds.current.delete("parse:" + doc.id); announce("Parsing queued. You can leave this page and return to the saved result."); await refresh();
    } catch (error) { announce((error as Error).message, true); await refresh(); }
    finally { if (mounted.current) setBusy(""); }
  }
  async function remove() {
    if (!deleting) return; setBusy("delete");
    try {
      await api("/files/" + encodeURIComponent(deleting.id), { method: "DELETE" });
      if (selected?.id === deleting.id) setSelected(null);
      setDeleting(null); announce("Document and extracted output deleted."); await refresh();
    } catch (error) { announce((error as Error).message, true); }
    finally { if (mounted.current) setBusy(""); }
  }
  async function runSearch() {
    if (!query.trim()) return; setBusy("search"); setNotice(null);
    const key = "search:" + JSON.stringify([query.trim(), fileType, limit]);
    try {
      const result = await api<{ search: SearchRecord }>(scoped("/discovery/searches", workspace.id), jsonOptions("POST", { query: query.trim(), file_type: fileType, limit: Number(limit) }, requestId(key)));
      if (result.search.status !== "running") requestIds.current.delete(key);
      setSearchId(result.search.id);
      if (result.search.status === "failed") announce(errorMessage(result.search.error_code || "firecrawl_unavailable"), true);
      await refresh();
    } catch (error) { announce((error as Error).message, true); }
    finally { if (mounted.current) setBusy(""); }
  }
  async function collect(results: SearchResult[]) {
    if (!currentSearch) return; setBusy("collect"); const search = currentSearch;
    let collected = 0;
    for (const result of results) {
      if (!mounted.current) break;
      setCollectionState(state => ({ ...state, [result.id]: "Collecting..." }));
      try {
        await api(scoped("/discovery/collect", workspace.id), jsonOptions("POST", { search_id: search.id, result_id: result.id }, requestId("collect:" + result.id)));
        collected++; setCollectionState(state => ({ ...state, [result.id]: "Saved" })); setChecked(state => state.filter(id => id !== result.id));
      } catch (error) { setCollectionState(state => ({ ...state, [result.id]: (error as Error).message })); }
    }
    announce(`${collected} of ${results.length} documents saved to your workspace.`, collected < results.length);
    await refresh(); if (mounted.current) setBusy("");
  }
  async function saveConnection(disconnect = false) {
    setBusy("connection"); setConnectionNotice("");
    try {
      await api(scoped("/discovery/connection", workspace.id), disconnect ? { method: "DELETE" } : jsonOptions("PUT", { api_key: apiKey.trim() }));
      setApiKey(""); await connection.refetch(); setConnectionNotice(disconnect ? "Firecrawl disconnected." : "API key saved securely. Your next search will verify it with Firecrawl.");
    } catch (error) { setConnectionNotice((error as Error).message); }
    finally { if (mounted.current) setBusy(""); }
  }

  const availableResults = currentSearch?.results.filter(item => collectionState[item.id] !== "Saved") || [];
  const pageAlerts = (
    <div className="cloud-page-alerts">
      {notice && <Alert role={notice.error ? "alert" : "status"} color={notice.error ? "red" : "gray"} withCloseButton closeButtonLabel="Dismiss message" onClose={() => setNotice(null)}>{notice.text}</Alert>}
      {health.data?.processing === "disabled" && <Alert color="yellow">Parsing is paused. You can still collect, upload, and read saved documents.</Alert>}
    </div>
  );

  return <>
    <ParseriumShell
      destination={section}
      navigation={cloudNavigation}
      onDestinationChange={value => navigate(value as Section)}
      headerActions={<><Button className="cloud-settings-button" variant="subtle" color="dark" aria-label="Settings" leftSection={<OperationalIcon name="settings" />} onClick={() => setSettings(true)}>Settings</Button>{accountSlot}</>}
    >
      {section === "Collect" && <div className="operations-grid cloud-operations-grid cloud-wall-page cloud-collect-page">
        <section id="collect" className="discovery-panel" aria-labelledby="discovery-title" tabIndex={-1}>
          <div className="discovery-hero" data-workspace-enter>
            <div className="cloud-hero-copy"><Text className="cloud-wall-kicker">Collect / {workspace.name}</Text><Title className="discovery-hero-title cloud-page-title" order={1}>Find documents</Title><Text className="collect-description">Search for documents, review results, and save originals to your workspace.</Text></div>
          </div>
          <EvidenceRail active="collect" />
          <form className="discovery-form" onSubmit={event => { event.preventDefault(); void runSearch(); }}>
            <Title className="visually-hidden" id="discovery-title" order={2}>Document discovery</Title>
            <div className="discovery-command-frame">
              <div className="collect-command-heading"><span><OperationalIcon name="globe" size={17} />Search</span><Text size="xs">Powered by Firecrawl</Text></div>
              <div className="discovery-command">
                <TextInput className="discovery-query" label="Search query" classNames={{ label: "visually-hidden", input: "command-input" }} leftSection={<OperationalIcon name="search" size={21} />} id="discovery-query" value={query} onChange={event => setQuery(event.currentTarget.value)} maxLength={450} placeholder="Search reports, research, manuals..." required />
                <Button type="submit" className="discovery-primary-action" loading={busy === "search"} disabled={!!busy || !connection.data?.configured || !query.trim()} leftSection={<OperationalIcon name="search" size={19} />}>Discover</Button>
              </div>
              <div className="collect-options">
                <button className="firecrawl-selector cloud-firecrawl-selector" type="button" data-ready={connection.data?.configured || undefined} onClick={() => setSettings(true)}><OperationalIcon name="globe" size={17} /><span>{connection.data?.configured ? "Firecrawl ready" : "Connect Firecrawl"}</span></button>
                <label className="command-segment document-type-summary cloud-compact-select"><span className="visually-hidden">Document format</span><select aria-label="Document format" value={fileType} onChange={event => setFileType(event.currentTarget.value)}><option value="all">PDF &amp; DOCX</option><option value="pdf">PDF only</option><option value="docx">DOCX only</option></select></label>
                <label className="command-segment document-type-summary cloud-compact-select"><span className="visually-hidden">Number of results</span><select aria-label="Number of results" value={limit} onChange={event => setLimit(event.currentTarget.value)}>{["5", "10", "20"].map(value => <option key={value} value={value}>{value} results</option>)}</select></label>
                <Button type="button" variant="subtle" className="collect-settings-button" leftSection={<OperationalIcon name="filter" size={18} />} onClick={() => setSettings(true)}>Search settings</Button>
              </div>
              <Text className="command-summary" size="xs" aria-live="polite">Any reachable source · up to {limit} results</Text>
            </div>

            <div className="discovery-content">
              {pageAlerts}
              {!connection.data?.configured && <div className="cloud-inline-help"><span>Connect your Firecrawl API key to search. Requests use your Firecrawl credits.</span><Button variant="subtle" onClick={() => setSettings(true)}>Set up connection</Button></div>}
              <section className="discovery-results cloud-discovery-results" role="region" aria-label="Discovered documents" aria-live="polite">
                <Group className="result-toolbar" justify="space-between" align="center" wrap="nowrap">
                  <Group className="result-count" gap="lg" wrap="nowrap"><Text className="result-toolbar-title">Source field</Text><Text className="machine-data">{currentSearch ? `${currentSearch.results.length} results` : "Ready to search"}</Text></Group>
                  <Group className="result-actions" gap="xs" wrap="nowrap"><Text className="selected-count">{checked.length} selected</Text><Button type="button" className="collect-action" aria-label="Collect selected" loading={busy === "collect"} disabled={!checked.length || !!busy} onClick={() => void collect(currentSearch?.results.filter(item => checked.includes(item.id)) || [])}>Collect selected</Button></Group>
                </Group>
                {checked.length > 0 && <svg className="cloud-selection-trace" aria-hidden="true" viewBox="0 0 640 42" preserveAspectRatio="none"><path d="M4 21 C 180 21, 190 7, 320 21 S 490 35, 636 21" /></svg>}
                {searches.error ? <div className="discovery-feedback"><Alert color="red">{searches.error.message}<Button variant="subtle" onClick={() => void searches.refetch()}>Retry</Button></Alert></div> :
                (searches.isPending || busy === "search" || currentSearch?.status === "running") ? <div className="cloud-empty"><Loader size="sm" /><h3>Searching the web</h3><p>Results are saved here when the search finishes.</p></div> :
                currentSearch?.status === "failed" ? <Empty title="Search needs attention">{errorMessage(currentSearch.error_code || "firecrawl_unavailable")}</Empty> :
                !currentSearch?.results.length ? <Empty title={currentSearch ? "No matching documents" : "Start with a topic or a source"}>{currentSearch ? "Try broader terms, another format, or a different website." : "Search for a topic, title, or website. Review results before collecting them."}</Empty> :
                <div className="result-table" role="table" aria-label="Discovered document results">
                  <div role="rowgroup"><div className="result-grid result-grid-header cloud-result-grid" role="row"><div role="columnheader" className="result-select-all"><Checkbox aria-label="Select all search results" disabled={!!busy} checked={checked.length > 0 && checked.length === availableResults.length} indeterminate={checked.length > 0 && checked.length < availableResults.length} onChange={event => setChecked(event.currentTarget.checked ? availableResults.map(item => item.id) : [])} /></div><Text role="columnheader">Document title</Text><Text role="columnheader">Source</Text><Text role="columnheader">Type</Text><Text role="columnheader">Action</Text></div></div>
                  <div className="result-list" role="rowgroup">{currentSearch.results.map((result, index) => <div className="result-grid result-row cloud-result-grid" role="row" data-focused={focusedResultId === result.id ? "true" : "false"} data-selected={checked.includes(result.id) ? "true" : "false"} onClick={() => setFocusedResultId(result.id)} onFocusCapture={() => setFocusedResultId(result.id)} onPointerEnter={() => setFocusedResultId(result.id)} key={result.id}><span className="cloud-source-index" aria-hidden="true">{String(index + 1).padStart(2, "0")}</span><div role="cell" className="result-checkbox-cell"><Checkbox aria-label={`Select ${result.title}`} checked={checked.includes(result.id)} disabled={!!busy || collectionState[result.id] === "Saved"} onChange={event => setChecked(event.currentTarget.checked ? [...checked, result.id] : checked.filter(id => id !== result.id))} /></div><div className="result-document cloud-result-document" role="cell"><a className="result-title" href={result.url} target="_blank" rel="noopener noreferrer"><OverflowMarquee text={result.title} /></a><span className="cloud-result-description">{result.description}</span>{collectionState[result.id] && collectionState[result.id] !== "Saved" && <span className="cloud-result-feedback" role="status">{collectionState[result.id]}</span>}</div><Text className="result-source" role="cell">{new URL(result.url).hostname}</Text><Text className="file-type" role="cell">{result.file_type?.toUpperCase() || "LINK"}</Text><div role="cell" className="cloud-result-action-cell"><Button className="cloud-result-action" size="xs" variant="subtle" disabled={!!busy || collectionState[result.id] === "Saved"} onClick={() => void collect([result])}>{collectionState[result.id] === "Saved" ? "Saved" : "Collect"}</Button></div></div>)}</div>
                </div>}
              </section>
            </div>
          </form>
        </section>

        <div className="cloud-collect-rail">
          <aside className="queue-panel cloud-collection-panel cloud-collection-tray" aria-labelledby="cloud-collection-title">
            <div className="queue-heading"><Title order={2} id="cloud-collection-title">Selected documents</Title><Text className="queue-total" aria-label={`${checked.length} selected documents`}>{String(checked.length).padStart(2, "0")}</Text></div>
            <Text className="cloud-collection-copy">Selected sources wait here before they enter your private corpus.</Text>
            {checked.length ? <ul className="cloud-selected-list">{currentSearch?.results.filter(item => checked.includes(item.id)).map(item => <li key={item.id}>{item.title}</li>)}</ul> : <div className="queue-empty"><OperationalIcon name="queue" size={26} /><Text fw={600}>Nothing selected yet</Text><Text size="sm">Select documents from the results ledger.</Text></div>}
            <Button className="cloud-collect-button" disabled={!checked.length || !!busy} loading={busy === "collect"} onClick={() => void collect(currentSearch?.results.filter(item => checked.includes(item.id)) || [])}>Collect {checked.length || "selected"} documents</Button>
            <Text className="cloud-rail-foot">PDF and DOCX, up to 10 MiB each. Parsing starts separately after collection.</Text>
            <UnstyledButton className="cloud-text-button" onClick={() => navigate("Documents")}>Open saved documents <OperationalIcon name="arrow-right" size={14} /></UnstyledButton>
          </aside>
          <CloudActivitySnapshot documents={docs} isPending={documents.isPending} onOpenActivity={() => navigate("Activity")} />
        </div>

        <div className="documents-utility cloud-documents-utility">
          <div className="documents-utility-summary"><OperationalIcon name="folder" size={20} /><Text className="documents-utility-title">{documents.isPending ? "Saved documents loading" : `${docs.length} saved document${docs.length === 1 ? "" : "s"} on this page`}</Text></div>
          <Button className="cloud-documents-upload" variant="default" leftSection={<OperationalIcon name="file" />} onClick={() => setUploadOpen(true)}>Upload document</Button>
          <UnstyledButton className="documents-utility-open" onClick={() => navigate("Documents")}>Open documents <OperationalIcon name="arrow-right" size={16} /></UnstyledButton>
        </div>
      </div>}

      {section === "Documents" && <CloudDocumentsPage
        workspace={workspace}
        documents={docs}
        visibleDocuments={visibleDocs}
        alerts={pageAlerts}
        error={documents.error}
        isPending={documents.isPending}
        isFetching={documents.isFetching}
        filter={filter}
        busy={busy}
        processingDisabled={health.data?.processing === "disabled"}
        offset={offset}
        hasMore={documents.data?.has_more || false}
        onFilterChange={setFilter}
        onUpload={() => setUploadOpen(true)}
        onRefresh={() => void documents.refetch()}
        onInspect={setSelected}
        onParse={doc => void parse(doc)}
        onDelete={setDeleting}
        onPrevious={() => setOffset(Math.max(0, offset - 50))}
        onNext={() => setOffset(offset + 50)}
      />}

      {section === "Activity" && <CloudActivityPage
        workspace={workspace}
        documents={docs}
        searches={history}
        alerts={pageAlerts}
        documentsPending={documents.isPending}
        searchesPending={searches.isPending}
        searchesError={searches.error}
        refreshing={searches.isFetching || documents.isFetching}
        onRefresh={() => void refresh()}
        onRefreshSearches={() => void searches.refetch()}
        onInspect={setSelected}
        onOpenSearch={search => { setSearchId(search.id); navigate("Collect"); }}
        onOpenDocuments={() => navigate("Documents")}
      />}
    </ParseriumShell>

    <Modal.Root opened={uploadOpen} onClose={() => { if (busy !== "upload") setUploadOpen(false); }} centered size="lg" classNames={{ content: "cloud-modal", header: "cloud-modal-header", body: "cloud-modal-body" }}>
      <Modal.Overlay />
      <Modal.Content><Modal.Header component="div"><Modal.Title>Add a document</Modal.Title><Modal.CloseButton aria-label="Close upload" /></Modal.Header><Modal.Body>
      {notice?.error && <Alert mb="md" color="red" role="alert">{notice.text}</Alert>}
      <div className="cloud-modal-intro"><p className="cloud-eyebrow">Private workspace</p><h2>Bring the original with you.</h2><p>Upload the source now. Choose when to parse it after it joins your library.</p></div>
      <div className={`cloud-dropzone ${dragging ? "is-dragging" : ""}`} onDragOver={event => { event.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={event => { event.preventDefault(); setDragging(false); if (!busy) setFile(event.dataTransfer.files[0] || null); }}>
        <span className="cloud-dropzone-icon"><OperationalIcon name="file" size={30} /></span><h3>{file ? file.name : "Drop your document here"}</h3><p>{file ? sizeText(file.size) : "Drag a PDF or DOCX into this space"}</p><Button variant="outline" color="dark" disabled={!!busy} onClick={() => fileInput.current?.click()}>{file ? "Choose another file" : "Choose a file"}</Button><input ref={fileInput} type="file" hidden accept=".pdf,.docx" onChange={event => setFile(event.currentTarget.files?.[0] || null)} />
      </div>
      <div className="cloud-upload-constraints" aria-label="Upload constraints"><span><strong>Formats</strong>PDF or DOCX</span><span><strong>Original size</strong>Up to 10 MiB</span><span><strong>Parsing</strong>Up to 20 rendered pages</span></div>
      <p className="cloud-modal-note">Originals are stored privately in this workspace.</p>
      <Group className="cloud-modal-actions" justify="flex-end"><Button variant="subtle" color="dark" disabled={!!busy} onClick={() => setUploadOpen(false)}>Cancel</Button><Button disabled={!file || !!busy} loading={busy === "upload"} onClick={() => void upload()}>Upload document</Button></Group>
      </Modal.Body></Modal.Content>
    </Modal.Root>
    <Drawer.Root opened={settings} onClose={() => { setSettings(false); setApiKey(""); setConnectionNotice(""); }} position="right" size="md" classNames={{ content: "parserium-drawer", header: "parserium-drawer-header" }}>
      <Drawer.Overlay />
      <Drawer.Content><Drawer.Header component="div"><Drawer.Title>Workspace settings</Drawer.Title><Drawer.CloseButton aria-label="Close workspace settings" /></Drawer.Header><Drawer.Body>
      <div className="cloud-settings">
        <div className="cloud-drawer-intro"><p className="cloud-eyebrow">{workspace.name}</p><h2>Search the wider web.</h2><p>Firecrawl powers online discovery using the credits from your own account.</p></div>
        {connection.isPending ? <div className="cloud-settings-loading"><Loader size="sm" /><span>Loading connection...</span></div> : connection.error ? <Alert color="red">{connection.error.message}</Alert> : <>
          <section className="cloud-connection-card" aria-labelledby="cloud-firecrawl-title">
            <div className="cloud-connection-heading"><div><p className="cloud-eyebrow">Connection</p><h3 id="cloud-firecrawl-title">Firecrawl</h3></div><Badge color={connection.data?.configured ? "parseriumGreen" : "gray"} variant="light">{connection.data?.configured ? "Key configured" : "Not connected"}</Badge></div>
            {connection.data?.updated_at && <p className="cloud-connection-date">Updated {dateText(connection.data.updated_at)}</p>}
            {!connection.data?.storage_available && <Alert color="yellow">Connection settings need an administrator to finish service configuration.</Alert>}
            {connection.data?.can_manage ? <form onSubmit={event => { event.preventDefault(); void saveConnection(); }}><PasswordInput label={connection.data.configured ? "Replace API key" : "Firecrawl API key"} value={apiKey} onChange={event => setApiKey(event.target.value)} autoComplete="off" placeholder="fc-..." maxLength={203} description="Encrypted when stored. The saved key is never returned to your browser." /><Group mt="md"><Button type="submit" disabled={!apiKey.trim() || !!busy || !connection.data.storage_available} loading={busy === "connection"}>Save connection</Button>{connection.data.configured && <Button variant="subtle" color="red" disabled={!!busy} onClick={() => void saveConnection(true)}>Disconnect</Button>}</Group></form> : <p className="cloud-subtle">Ask the workspace owner to configure or replace the API key.</p>}
            {connectionNotice && <Alert role="status" color="gray">{connectionNotice}</Alert>}
            <a className="cloud-connection-link" href="https://www.firecrawl.dev/app/api-keys" target="_blank" rel="noopener noreferrer">Manage your Firecrawl API keys <OperationalIcon name="external-link" size={13} /></a>
          </section>
        </>}
        <section className="cloud-settings-limits" aria-labelledby="cloud-limits-title"><p className="cloud-eyebrow">Capacity</p><h3 id="cloud-limits-title">Workspace limits</h3><dl><div><dt>Original file</dt><dd>10 MiB</dd></div><div><dt>Rendered pages</dt><dd>20</dd></div><div><dt>Daily parse jobs</dt><dd>20</dd></div><div><dt>Daily searches</dt><dd>20</dd></div><div><dt>Reserved storage</dt><dd>1 GiB</dd></div></dl><p>Complex documents can exceed the parsing time limit. Keep originals for reference when reviewing extracted content.</p></section>
      </div>
      </Drawer.Body></Drawer.Content>
    </Drawer.Root>
    <Drawer.Root opened={!!viewedDocument} onClose={() => setSelected(null)} position="right" size="xl" classNames={{ content: "parserium-drawer", header: "parserium-drawer-header" }}>
      <Drawer.Overlay />
      <Drawer.Content><Drawer.Header component="div"><Drawer.Title>Document details</Drawer.Title><Drawer.CloseButton aria-label="Close document details" /></Drawer.Header><Drawer.Body>
      {notice && <Alert mb="md" color={notice.error ? "red" : "gray"} role={notice.error ? "alert" : "status"}>{notice.text}</Alert>}
      {viewedDocument && <article className="cloud-inspector">
        <header className="cloud-inspector-heading"><p className="cloud-eyebrow">{viewedDocument.source_url ? "Collected original" : "Uploaded original"}</p><h2>{viewedDocument.filename}</h2><div className="cloud-inspector-meta"><Status doc={viewedDocument} /><span>{sizeText(viewedDocument.size_bytes)}</span><span>{dateText(viewedDocument.created_at)}</span></div></header>
        <div className="cloud-inspector-actions"><Button component="a" href={`${prefix}/files/${viewedDocument.id}`} variant="outline" color="dark" leftSection={<OperationalIcon name="download" />}>Download original</Button>{viewedDocument.job_status === "succeeded" && <Button component="a" href={`${prefix}/parse-jobs/${viewedDocument.job_id}/output`}>Download Markdown</Button>}{!viewedDocument.job_id && viewedDocument.validation_status !== "invalid" && <Button disabled={!!busy || health.data?.processing === "disabled"} loading={busy === viewedDocument.id} onClick={() => void parse(viewedDocument)}>Parse document</Button>}</div>
        {viewedDocument.source_url && <a className="cloud-inspector-source" href={viewedDocument.source_url} target="_blank" rel="noopener noreferrer">View original source <OperationalIcon name="external-link" size={13} /></a>}
        <div className="cloud-inspector-state">{activeJob(viewedDocument) && <Alert color="orange" title={viewedDocument.job_status === "running" ? "Extracting your document" : "Waiting to parse"}>This job continues in the background. Its result will stay here when it finishes.</Alert>}{(viewedDocument.job_error_code || viewedDocument.validation_error_code) && <Alert color="red">{errorMessage(viewedDocument.job_error_code || viewedDocument.validation_error_code || "parser_failed")}</Alert>}</div>
        <section className="cloud-output-panel cloud-inspector-reader" aria-labelledby="cloud-output-title"><div className="cloud-output-heading"><div><p className="cloud-eyebrow">Structured result</p><h3 id="cloud-output-title">Extracted Markdown</h3></div>{markdown !== null && <Button variant="subtle" size="xs" leftSection={<OperationalIcon name="copy" />} onClick={() => { void navigator.clipboard.writeText(markdown).then(() => announce("Markdown copied.")).catch(() => announce("Copy failed. Download the Markdown instead.", true)); }}>Copy</Button>}</div>{outputLoading ? <div className="cloud-output-loading" role="status"><Loader size="sm" /> Loading saved output...</div> : outputError ? <Alert color="red">{outputError}</Alert> : markdown !== null ? <pre className="cloud-markdown">{markdown || "The parser returned no text."}</pre> : <p className="cloud-output-empty">The saved extraction will appear here after parsing.</p>}<p className="cloud-modal-note">Review extracted content against the original, especially table headers and layout.</p></section>
      </article>}
      </Drawer.Body></Drawer.Content>
    </Drawer.Root>
    <Modal.Root opened={!!deleting} onClose={() => { if (busy !== "delete") setDeleting(null); }} centered classNames={{ content: "cloud-modal cloud-delete-modal", header: "cloud-modal-header", body: "cloud-modal-body" }}>
      <Modal.Overlay />
      <Modal.Content><Modal.Header component="div"><Modal.Title>Delete document?</Modal.Title><Modal.CloseButton aria-label="Close delete confirmation" /></Modal.Header><Modal.Body><div className="cloud-delete-message"><span aria-hidden="true"><OperationalIcon name="trash" size={22} /></span><div><h2>Remove this original and its output?</h2><p><strong>{deleting?.filename}</strong> and its extracted Markdown will be permanently removed from this workspace.</p></div></div><Group className="cloud-modal-actions" justify="flex-end" mt="lg"><Button variant="subtle" color="dark" disabled={!!busy} onClick={() => setDeleting(null)}>Keep document</Button><Button color="parseriumRed" loading={busy === "delete"} disabled={!!busy} onClick={() => void remove()}>Delete document</Button></Group></Modal.Body></Modal.Content>
    </Modal.Root>
  </>;
}
function historyReplace(section: Section) { window.history.replaceState(null, "", `${location.pathname}${location.search}#${section.toLowerCase()}`); }
