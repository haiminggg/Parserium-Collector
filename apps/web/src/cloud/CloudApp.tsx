import { lazy, Suspense, useEffect, useState } from "react";
import { Alert, Button, Loader, Select } from "@mantine/core";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { BrandMark } from "../auth/BrandMark";
import { api, CloudError, type Session } from "./api";
import { WorkspacePage } from "./WorkspacePage";
const LoginScreen = lazy(() => import("../LoginScreen").then(module => ({ default: module.LoginScreen })));

export function CloudApp() {
  const client = useQueryClient();
  const [workspaceId, setWorkspaceId] = useState("");
  const [logoutError, setLogoutError] = useState("");
  const [loggingOut, setLoggingOut] = useState(false);
  const session = useQuery({ queryKey: ["session"], queryFn: async ({ signal }) => {
    try { return await api<Session>("/session", { signal }); }
    catch (error) { if (error instanceof CloudError && error.status === 401) return null; throw error; }
  } });
  useEffect(() => {
    const expire = () => {
      void client.cancelQueries();
      client.removeQueries({ predicate: query => query.queryKey[0] !== "session" });
      client.setQueryData(["session"], null); setWorkspaceId("");
    };
    window.addEventListener("parserium-session-expired", expire);
    return () => window.removeEventListener("parserium-session-expired", expire);
  }, [client]);
  if (session.isPending) return <main className="cloud-center"><BrandMark /><Loader color="gray" size="sm" /><p role="status">Opening your workspace...</p></main>;
  if (session.error) return <main className="cloud-center"><h1>Workspace unavailable</h1><p>We could not check your session.</p><Button onClick={() => void session.refetch()}>Try again</Button></main>;
  if (!session.data) return <>{new URLSearchParams(location.search).has("auth") && <Alert color="red" title="Sign-in was not completed">Check your invitation and Google account, then try again.</Alert>}<Suspense fallback={<main className="cloud-center"><Loader size="sm" /><p>Loading sign in...</p></main>}><LoginScreen loginUrl="/api/cloud/v1/auth/login" providerLabel="Google" /></Suspense></>;
  const account = session.data;
  const workspace = account.workspaces.find(item => item.id === workspaceId) || account.workspaces[0];
  const logout = async () => {
    setLoggingOut(true); setLogoutError("");
    try { await api("/logout", { method: "POST" }); client.clear(); location.replace("/"); }
    catch (error) { setLogoutError((error as Error).message); setLoggingOut(false); }
  };
  return <div className="cloud-app">
    {workspace ? <WorkspacePage key={workspace.id} workspace={workspace} accountSlot={<div className="cloud-account workspace-control">
      <Select aria-label="Workspace" value={workspace.id} onChange={value => value && setWorkspaceId(value)} data={account.workspaces.map(item => ({ value: item.id, label: item.name }))} allowDeselect={false} className="cloud-workspace-select" />
      <Button className="cloud-sign-out" variant="subtle" color="dark" size="xs" loading={loggingOut} onClick={() => void logout()} title={account.user.email}>Sign out</Button>
    </div>} /> : <main className="cloud-center"><h1>No workspace access</h1><p>Contact your workspace administrator for an invitation.</p><Button onClick={() => void logout()}>Sign out</Button></main>}
    {logoutError && <Alert className="cloud-global-alert" color="red" role="alert" withCloseButton closeButtonLabel="Dismiss sign-out error" onClose={() => setLogoutError("")}>{logoutError}</Alert>}
  </div>;
}
