import { Button } from "@mantine/core";
import { useRef, useState } from "react";
import type { DocumentType } from "../api/discovery";
import { BrandMark } from "../auth/BrandMark";
import { DiscoveryCommandBar } from "../DiscoveryCommandBar";
import { LoginScreen } from "../LoginScreen";
import { DocumentMotion } from "../workspace/DocumentMotion";
import { useWorkspaceEntrance } from "../workspace/useWorkspaceEntrance";
import "../workspace/workspace.css";
import "./preview.css";

type Screen = "Login" | "Collect" | "Documents" | "Activity";
const unavailable = "UI preview only. Login, discovery, uploads and processing require the backend.";

export function PreviewApp() {
  const [screen, setScreen] = useState<Screen>("Login");
  const [query, setQuery] = useState("");
  const [limit, setLimit] = useState(20);
  const [types, setTypes] = useState<DocumentType[]>(["pdf", "docx"]);
  const [included, setIncluded] = useState("");
  const [excluded, setExcluded] = useState("");
  const [tables, setTables] = useState(true);
  const root = useRef<HTMLDivElement>(null);
  useWorkspaceEntrance(root);

  return (
    <div className="ui-preview" ref={root}>
      <aside className="preview-notice" aria-label="Preview limitations">
        <strong>UI preview</strong><span>{unavailable}</span>
      </aside>
      {screen === "Login" ? (
        <LoginScreen loginUrl="" providerLabel="Google" onPreviewExplore={() => setScreen("Collect")} />
      ) : (
        <div className="workspace-shell preview-workspace">
          <header className="preview-header">
            <span className="auth-brand"><BrandMark />Parserium</span>
            <nav className="dashboard-navigation" aria-label="Primary navigation">
              {(["Collect", "Documents", "Activity"] as const).map((item) => (
                <button key={item} type="button" className="dashboard-navigation-button"
                  aria-current={screen === item ? "page" : undefined}
                  data-active={screen === item || undefined} onClick={() => setScreen(item)}>{item}</button>
              ))}
            </nav>
            <Button variant="subtle" onClick={() => setScreen("Login")}>Back to login preview</Button>
          </header>
          <main className="preview-content">
            {screen === "Collect" ? (
              <>
                <section className="discovery-hero" data-workspace-enter>
                  <h1 className="discovery-hero-title">Find documents worth keeping.</h1>
                  <p className="collect-description">Search online. Review the contents. Keep the source.</p>
                </section>
                <form className="discovery-command-frame" onSubmit={(event) => event.preventDefault()}>
                  <DiscoveryCommandBar query={query} limit={limit} documentTypes={types}
                    includeDomains={included} excludeDomains={excluded} tablesRequired={tables}
                    pending={false} submitBlocked submitBlockedReason={unavailable}
                    onQueryChange={setQuery} onLimitChange={setLimit}
                    onDocumentTypeChange={(type, checked) => setTypes((current) => checked
                      ? Array.from(new Set([...current, type])) : current.filter((value) => value !== type))}
                    onIncludeDomainsChange={setIncluded} onExcludeDomainsChange={setExcluded}
                    onTablesRequiredChange={setTables} />
                  <p className="preview-help">Try the filters and settings. Search is disabled; no documents are fetched or saved.</p>
                </form>
                <div className="preview-columns">
                  <section className="preview-panel">
                    <h2>Your next collection starts here.</h2>
                    <p>No results in this preview. The animation below is an illustration, not a processing job.</p>
                    <DocumentMotion />
                  </section>
                  <section className="preview-panel">
                    <h2>Collection</h2><p>No documents queued.</p>
                    <p>Live progress will appear here when the backend is connected.</p>
                  </section>
                </div>
              </>
            ) : (
              <section className="preview-panel preview-empty">
                <h1>{screen === "Documents" ? "No stored documents" : "No processing jobs"}</h1>
                <p>{screen === "Documents"
                  ? "Your collected documents will appear here. Uploads, downloads and document previews need the backend."
                  : "Your discovery and processing history will appear here when the backend is connected."}</p>
                {screen === "Documents" && <Button disabled title={unavailable}>Upload documents</Button>}
                <Button variant="subtle" onClick={() => setScreen("Collect")}>Back to Collect</Button>
              </section>
            )}
          </main>
        </div>
      )}
    </div>
  );
}
