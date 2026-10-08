import { afterEach, describe, expect, it, vi } from "vitest";

import { loadSessionStatus } from "./session";

afterEach(() => vi.unstubAllGlobals());

describe("session response validation", () => {
  it("accepts the hosted login-required response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            status: "login_required",
            login_url: "/api/v1/auth/login",
            provider_label: "Google",
          }),
          { status: 200 },
        ),
      ),
    );

    await expect(loadSessionStatus()).resolves.toEqual({
      status: "login_required",
      login_url: "/api/v1/auth/login",
      provider_label: "Google",
    });
  });

  it("rejects a hosted response without a provider label", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            status: "login_required",
            login_url: "/api/v1/auth/login",
          }),
          { status: 200 },
        ),
      ),
    );

    await expect(loadSessionStatus()).rejects.toThrow("Session response was invalid.");
  });

  it("rejects incomplete authenticated workspace data", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            status: "authenticated",
            csrf_token: "csrf-token",
            idle_expires_at: "2026-08-30T12:00:00Z",
          }),
          { status: 200 },
        ),
      ),
    );

    await expect(loadSessionStatus()).rejects.toThrow("Session response was invalid.");
  });
});
