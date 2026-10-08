import { MantineProvider } from "@mantine/core";
import { StrictMode } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LoginScreen } from "./LoginScreen";

afterEach(() => {
  cleanup();
  window.history.replaceState(null, "", "/");
  window.localStorage.clear();
  window.sessionStorage.clear();
  vi.restoreAllMocks();
});

describe("LoginScreen", () => {
  it("presents the approved sign-in layout and useful inline help", () => {
    render(
      <MantineProvider>
        <LoginScreen loginUrl="/api/v1/auth/login" providerLabel="Google" />
      </MantineProvider>,
    );

    expect(screen.getByRole("heading", { name: "Welcome to Parserium" })).toBeVisible();
    expect(screen.getByText("Access is currently invite-only.")).toBeVisible();
    const help = screen.getByText("Having trouble signing in?").closest("details");
    expect(help).not.toHaveAttribute("open");
    expect(screen.getByText(/account your workspace administrator invited/)).toBeInTheDocument();
    expect(document.querySelector('a[href="#"]')).toBeNull();
  });

  it("keeps a configured non-Google provider usable", () => {
    render(
      <MantineProvider>
        <LoginScreen loginUrl="/api/v1/auth/login" providerLabel="Organization SSO" />
      </MantineProvider>,
    );
    expect(screen.getByRole("button", { name: "Continue with Organization SSO" })).toBeEnabled();
    expect(document.querySelector("[data-google-brand]")).toBeNull();
  });

  it("preserves invitation capture across StrictMode's repeated render", () => {
    window.history.replaceState(null, "", "/#invite=strict-invitation");
    render(
      <StrictMode>
        <MantineProvider>
          <LoginScreen loginUrl="/api/v1/auth/login" providerLabel="Google" />
        </MantineProvider>
      </StrictMode>,
    );
    expect(document.querySelector('input[name="invite"]')).toHaveValue("strict-invitation");
    expect(window.location.hash).toBe("");
  });

  it("uses the hosted login form without collecting a password", () => {
    render(
      <MantineProvider>
        <LoginScreen loginUrl="/api/v1/auth/login" providerLabel="Google" />
      </MantineProvider>,
    );

    const button = screen.getByRole("button", { name: "Continue with Google" });
    const form = button.closest("form");
    expect(form).not.toBeNull();
    expect(form).toHaveAttribute("method", "post");
    expect(form).toHaveAttribute("action", "/api/v1/auth/login");
    expect(screen.queryByLabelText(/password/i)).not.toBeInTheDocument();
  });

  it("captures an invitation in memory and removes it from the address bar", () => {
    const invitation = "invite token/+=";
    window.history.replaceState(null, "", `/#invite=${encodeURIComponent(invitation)}`);
    const replaceState = vi.spyOn(window.history, "replaceState");

    render(
      <MantineProvider>
        <LoginScreen loginUrl="/api/v1/auth/login" providerLabel="Google" />
      </MantineProvider>,
    );

    expect(window.location.hash).toBe("");
    expect(replaceState).toHaveBeenCalled();
    expect(document.querySelector('input[name="invite"]')).toHaveValue(invitation);
    expect(window.localStorage).toHaveLength(0);
    expect(window.sessionStorage).toHaveLength(0);
  });

  it("captures an invitation when an already-open login page receives a fragment", () => {
    render(
      <MantineProvider>
        <LoginScreen loginUrl="/api/v1/auth/login" providerLabel="Google" />
      </MantineProvider>,
    );
    const invitation = "new invitation token";

    window.history.replaceState(null, "", `/#invite=${encodeURIComponent(invitation)}`);
    fireEvent(window, new HashChangeEvent("hashchange"));

    expect(window.location.hash).toBe("");
    expect(document.querySelector('input[name="invite"]')).toHaveValue(invitation);
    expect(window.localStorage).toHaveLength(0);
    expect(window.sessionStorage).toHaveLength(0);
  });
});
