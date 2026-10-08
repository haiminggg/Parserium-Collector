import { useEffect, useRef, useState } from "react";

import { BrandMark } from "./auth/BrandMark";
import { DocumentArtwork } from "./auth/DocumentArtwork";
import { useLoginEntrance } from "./auth/useLoginEntrance";
import "./auth/login.css";

interface LoginScreenProps {
  loginUrl: string;
  providerLabel: string;
  onPreviewExplore?: () => void;
}

const capturedInvitations = new WeakMap<Window, string>();

function consumeInvitation(): string {
  const retained = capturedInvitations.get(window);
  if (retained !== undefined) return retained;

  const fragment = new URLSearchParams(window.location.hash.replace(/^#/, ""));
  if (!fragment.has("invite")) return "";

  const invitation = fragment.get("invite") ?? "";
  capturedInvitations.set(window, invitation);
  window.history.replaceState(
    window.history.state,
    "",
    `${window.location.pathname}${window.location.search}`,
  );
  return invitation;
}

export function LoginScreen({ loginUrl, providerLabel, onPreviewExplore }: LoginScreenProps) {
  const [invitation, setInvitation] = useState(consumeInvitation);
  const root = useRef<HTMLDivElement>(null);
  useLoginEntrance(root);

  useEffect(() => {
    capturedInvitations.delete(window);
    function captureChangedInvitation() {
      if (!new URLSearchParams(window.location.hash.replace(/^#/, "")).has("invite")) {
        return;
      }
      setInvitation(consumeInvitation());
      capturedInvitations.delete(window);
    }

    window.addEventListener("hashchange", captureChangedInvitation);
    return () => window.removeEventListener("hashchange", captureChangedInvitation);
  }, []);

  return (
    <div className="auth-page" ref={root}>
      <a className="auth-skip" href="#sign-in">Skip to sign in</a>
      <header className="auth-header">
        <span className="auth-brand"><BrandMark />Parserium</span>
        <span className="auth-header-caption">Your document workspace</span>
      </header>
      <main className="auth-main">
        <section className="auth-story" aria-label="About Parserium">
          <div data-auth-enter>
            <p className="auth-headline">Your documents.<br />Ready for what’s next.</p>
            <p className="auth-story-caption">Collect, parse, and review in one workspace.</p>
          </div>
          <DocumentArtwork />
        </section>
        <section className="auth-card" aria-labelledby="sign-in-title" id="sign-in" tabIndex={-1}>
          <div className="auth-card-heading" data-auth-enter>
            <BrandMark className="auth-card-mark" />
            <h1 id="sign-in-title">Welcome to Parserium</h1>
            <p>Sign in to your document workspace.</p>
          </div>
          {onPreviewExplore ? (
            <div className="auth-form">
              <button className="auth-provider-button" type="button" disabled>
                <img src="/brand/google-g.png" alt="" width="20" height="20" />
                <span>Continue with {providerLabel}</span>
              </button>
              <p>Google sign-in is disabled in this UI preview. No account is needed.</p>
              <button className="auth-provider-button" type="button" onClick={onPreviewExplore}>
                Explore the preview
              </button>
            </div>
          ) : <form method="post" action={loginUrl} className="auth-form">
            <input type="hidden" name="invite" value={invitation} />
            {invitation ? (
              <p className="auth-invitation" role="status">
                Your workspace invitation is ready to be accepted after sign in.
              </p>
            ) : null}
            <button className="auth-provider-button" type="submit">
              {providerLabel.trim().toLowerCase() === "google" ? (
                <img data-google-brand src="/brand/google-g.png" alt="" width="20" height="20" />
              ) : null}
              <span>Continue with {providerLabel}</span>
            </button>
          </form>}
          <div className="auth-access" data-auth-enter>
            <span className="auth-lock" aria-hidden="true">
              <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" strokeWidth="1.6">
                <rect x="5" y="10" width="14" height="11" rx="2" />
                <path d="M8 10V7a4 4 0 0 1 8 0v3" />
              </svg>
            </span>
            <h2>Access is currently invite-only.</h2>
            <p>Use the {providerLabel} account your workspace administrator invited.</p>
          </div>
          <details className="auth-help">
            <summary>Having trouble signing in?</summary>
            <div className="auth-help-content">
              <p>Check that you are using your invited account. If you received an invitation link, open it before continuing.</p>
              <p>If access is still unavailable, ask your workspace administrator to check your invitation. Parserium does not collect your password.</p>
            </div>
          </details>
        </section>
      </main>
      <footer className="auth-footer">Find the source. Keep the context.</footer>
    </div>
  );
}
