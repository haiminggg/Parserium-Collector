export interface PairingRequiredSession {
  status: "pairing_required";
}

export interface AuthenticatedSession {
  status: "authenticated";
  csrf_token: string;
  idle_expires_at: string;
}

export type SessionStatus = PairingRequiredSession | AuthenticatedSession;

async function sessionResponse(response: Response): Promise<SessionStatus> {
  if (!response.ok) throw new Error("Session request failed.");
  const value = (await response.json()) as Partial<SessionStatus>;
  if (value.status === "pairing_required") return { status: "pairing_required" };
  if (
    value.status === "authenticated" &&
    typeof value.csrf_token === "string" &&
    typeof value.idle_expires_at === "string"
  ) {
    return {
      status: "authenticated",
      csrf_token: value.csrf_token,
      idle_expires_at: value.idle_expires_at,
    };
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

export async function logoutSession(csrfToken: string): Promise<void> {
  const response = await fetch("/api/v1/session/logout", {
    method: "POST",
    headers: { "X-Parserium-CSRF": csrfToken },
    credentials: "same-origin",
  });
  if (!response.ok) throw new Error("Logout failed.");
}
