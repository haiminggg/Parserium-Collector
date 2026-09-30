export interface PairingRequiredSession {
  status: "pairing_required";
}

export interface LoginRequiredSession {
  status: "login_required";
  login_url: string;
  provider_label: string;
}

export interface WorkspaceSummary {
  id: string;
  name: string;
  role: "owner" | "member";
}

export interface UserSummary {
  id: string;
  email: string;
  display_name: string | null;
}

export interface AuthenticatedSession {
  status: "authenticated";
  csrf_token: string;
  idle_expires_at: string;
  authentication_mode: "local" | "oidc";
  user: UserSummary | null;
  workspace: WorkspaceSummary;
  workspaces: WorkspaceSummary[];
}

export type SessionStatus =
  | PairingRequiredSession
  | LoginRequiredSession
  | AuthenticatedSession;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function workspaceSummary(value: unknown): WorkspaceSummary | null {
  if (!isRecord(value)) return null;
  if (
    typeof value.id !== "string" ||
    typeof value.name !== "string" ||
    (value.role !== "owner" && value.role !== "member")
  ) {
    return null;
  }
  return { id: value.id, name: value.name, role: value.role };
}

function userSummary(value: unknown): UserSummary | null | undefined {
  if (value === null) return null;
  if (!isRecord(value)) return undefined;
  if (
    typeof value.id !== "string" ||
    typeof value.email !== "string" ||
    (typeof value.display_name !== "string" && value.display_name !== null)
  ) {
    return undefined;
  }
  return {
    id: value.id,
    email: value.email,
    display_name: value.display_name,
  };
}

async function sessionResponse(response: Response): Promise<SessionStatus> {
  if (!response.ok) throw new Error("Session request failed.");
  const value: unknown = await response.json();
  if (!isRecord(value)) throw new Error("Session response was invalid.");
  if (value.status === "pairing_required") return { status: "pairing_required" };
  if (
    value.status === "login_required" &&
    typeof value.login_url === "string" &&
    typeof value.provider_label === "string"
  ) {
    return {
      status: "login_required",
      login_url: value.login_url,
      provider_label: value.provider_label,
    };
  }
  if (
    value.status === "authenticated" &&
    typeof value.csrf_token === "string" &&
    typeof value.idle_expires_at === "string" &&
    (value.authentication_mode === "local" || value.authentication_mode === "oidc")
  ) {
    const user = userSummary(value.user);
    const workspace = workspaceSummary(value.workspace);
    const workspaces = Array.isArray(value.workspaces)
      ? value.workspaces.map(workspaceSummary)
      : [];
    if (
      user !== undefined &&
      workspace !== null &&
      workspaces.length > 0 &&
      workspaces.every((candidate): candidate is WorkspaceSummary => candidate !== null) &&
      workspaces.some((candidate) => candidate.id === workspace.id)
    ) {
      return {
        status: "authenticated",
        csrf_token: value.csrf_token,
        idle_expires_at: value.idle_expires_at,
        authentication_mode: value.authentication_mode,
        user,
        workspace,
        workspaces,
      };
    }
  }
  throw new Error("Session response was invalid.");
}

export async function loadSessionStatus(): Promise<SessionStatus> {
  return sessionResponse(
    await fetch("/api/v1/session/status", {
      headers: { Accept: "application/json" },
      credentials: "same-origin",
    }),
  );
}

export async function pairSession(code: string): Promise<AuthenticatedSession> {
  const session = await sessionResponse(
    await fetch("/api/v1/session/pair", {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ code }),
    }),
  );
  if (session.status !== "authenticated") throw new Error("Pairing did not authenticate.");
  return session;
}

export async function switchWorkspace(
  csrfToken: string,
  workspaceId: string,
): Promise<AuthenticatedSession> {
  const session = await sessionResponse(
    await fetch("/api/v1/session/workspace", {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-Parserium-CSRF": csrfToken,
      },
      credentials: "same-origin",
      body: JSON.stringify({ workspace_id: workspaceId }),
    }),
  );
  if (session.status !== "authenticated") {
    throw new Error("Workspace switch did not authenticate.");
  }
  return session;
}

export async function logoutSession(csrfToken: string): Promise<void> {
  const response = await fetch("/api/v1/session/logout", {
    method: "POST",
    headers: { "X-Parserium-CSRF": csrfToken },
    credentials: "same-origin",
  });
  if (!response.ok) throw new Error("Logout failed.");
}
