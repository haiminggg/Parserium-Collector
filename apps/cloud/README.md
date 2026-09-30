# Parserium Cloud staging edition

This package contains the separate Cloudflare staging edition for Firecrawl discovery, private PDF/DOCX upload and collection, bounded offline parsing, durable job state, and Markdown retrieval. It serves the real React application built by `apps/web/vite.cloud.config.ts`. It does not replace the self-hosted application or the public preview.

Version `e4b3515f-6def-4efa-ae30-a91f445b8e94` is deployed to `parserium-cloud-staging` with `PROCESSING_ENABLED=true`. The React build and real login page are verified at `https://staging.parserium.com`. Cloud and local continue to share the actual `ParseriumShell`, base stylesheet, and authenticated workspace stylesheet. `DashboardShell` and `WorkspacePage` remain separate data adapters, so the Evidence Wall presentation does not change the cloud API contract. Collect uses Search anchor, Source field, and Collection tray zones while retaining its parsing snapshot. Documents uses a page-scoped corpus index and full document ledger, with the obsolete spotlight removed. Activity uses independent Parse and Search lanes with visible separation between search records, keeps state-driven, reduced-motion-aware GSAP motion on the parsing trace, and does not claim unsupported relationships between searches and documents. The primary page headings use one shared typography treatment and plain product language. Upload, settings, and document inspection share the same visual system. Cutoff filenames remain hover-only, fitting names stay stationary, reduced-motion mode stays static, and linked result names support keyboard focus. Extensionless terminal `/pdf` and `/docx` paths remain classified as documents while ambiguous endpoints remain `LINK`. The preceding signed-in PDF parsing gate passed completely, including retrieval after a fresh login and deletion. The user saved their Firecrawl key and later reported the corrected Firecrawl flow worked. Search and collection DNS requests use `manual` and reject non-2xx responses, preserving the no-redirect credential boundary.

## Implemented

- Google OAuth with PKCE, state, nonce, issuer and signature validation, invitation admission, 24-hour hashed sessions, CSRF protection, and a native authentication rate limiter.
- Workspace-authorized document metadata and private R2 originals.
- PDF and DOCX upload with a 10 MiB actual-byte limit, storage reservation, idempotency, deletion fencing, and bounded cleanup.
- Firecrawl v2 search with workspace-owned encrypted credentials, saved history, request idempotency, one active search and 20 daily searches per workspace.
- Bounded collection of saved search results with public HTTPS/DNS/redirect checks and retained source URLs.
- Offline DOCX conversion using the existing LibreOffice converter, bounded archives, active-content/external-relationship rejection, and the 20-rendered-page limit.
- Durable parse jobs in D1 with one job per document, workspace-scoped idempotency, 20 jobs per workspace per UTC day, and a global 60-minute daily runtime reservation.
- Validation before admission for malformed, encrypted, empty, and over-20-page PDFs.
- Queue delivery containing only `{jobId}`, two application attempts, leases, fencing tokens, bounded retry handling, and scheduled reconciliation.
- One globally named Cloudflare Container Durable Object with `max_instances: 1`, `instance_type: basic`, no internet access, and explicit stop after parsing or failed validation.
- Private Markdown output in R2, with authenticated status, preview, download, and deletion.
- A React/Mantine workspace with Collect, Documents, Activity, connection settings, upload, safe literal Markdown display, background status polling, downloads and confirmed deletion. The old staging shell remains only as a fallback when assets are not bound.
- Public `GET /api/cloud/v1/health`, which reports only service availability and whether processing is enabled.

Firecrawl search and collection are implemented, but no successful live call with the user's Firecrawl account has yet been claimed. The real DOCX fixture succeeds locally in the production image at staging resource limits.

## Local verification

Use Node 24 or newer. From `apps/cloud`:

```powershell
npm ci
npm test
npm run check
npm run build
npm run build:app
node tests/cloud-ui-check.mjs
node scripts/check-live-ui.mjs
```

The previous parsing slice passed all 80 cloud tests. The expansion run passed 79/80 and exposed one obsolete DOCX-rejection assertion; after updating it, all six admission tests passed. The additional expansion integration check, eight Python container tests, headless React/Worker browser checks, accessibility check and staging edge checks passed. Local test identities and credentials remain confined to ephemeral test stores. No test records are deployed.

For the 2026-09-14 redirect fix, `node --test tests/discovery-runtime.test.mjs tests/cloud-expansion.test.mjs` passed 2/2 and the Worker bundle built successfully. The runtime regression uses real workerd Request construction without network calls or API mocks. Live deployment inspection confirmed both corrected request policies and the existing secrets; `node scripts/check-live-ui.mjs` passed. The full suite was not repeated for this narrow change.

For the CSS-only local-parity restyle, `npm run build:cloud` passed, all 85 web unit tests passed across 22 files, and `node tests/cloud-ui-check.mjs` passed the existing login, authenticated workspace, accessibility, DOCX, settings and mobile-overflow checks. Fresh desktop and mobile screenshots were reviewed, including a second pass after restoring mobile access to Sign out. The deployed live UI check passed, the public preview remained unchanged, and Wrangler reported no Container application change. No React or Worker source was modified for this restyle.

For the subsequent structural-parity implementation, `ParseriumShell.tsx` became the shared Mantine application shell, `DashboardShell.tsx` became the unchanged-contract local adapter, and cloud Collect/Documents/Activity were recomposed with the local hierarchy and classes. Both web builds passed, all 85 unit tests passed, and the browser verifier passed with zero Collect accessibility violations. A new rendered-height assertion caught collapsed cloud secondary controls under the current local two-column command pattern; moving those controls into `collect-options` made the regression green. Desktop, mobile, Documents, and Settings screenshots were inspected. The live verifier passed and `parserium.com` remained unchanged. Wrangler reported no Container application changes.

For the hover-only filename correction, the browser regression first reproduced the unconditional animation and then passed after the CSS was scoped to hover and linked-name keyboard focus. It also checks pointer-leave reset, fitting names, and reduced motion. A subsequent regression reproduced the former starting hold as an exact zero-pixel offset 400 ms after hover, then passed after the hold was removed. All 87 web unit tests across 23 files passed, both standard and cloud builds passed, and the real local Worker browser verifier passed. The fresh deployment build and Worker bundle passed. The live verifier then passed exact staging assets, login rendering, security headers, enabled parsing, anonymous API rejection, and unchanged `parserium.com`. The control plane confirmed version `b02adf50-3fa3-4513-9aff-d649a90bfc03`, all expected bindings, and both existing secrets by name. Wrangler reused the existing Container image and reported no Container application changes.

The extensionless document-type correction is deployed in version `292982b8-4e45-4a32-8437-3a2db6f0c845`. Search results whose final URL path segment is `pdf` or `docx`, with or without a trailing slash, are now classified alongside ordinary `.pdf` and `.docx` extensions. Ambiguous paths remain unknown, and collection still verifies the actual document bytes. Legacy search history is normalized when returned, without a D1 rewrite. The dedicated real-Worker regression failed against the old extension-only classifier and passed after the correction. The focused test file passes 2 tests, the complete cloud suite passes 83 tests, Worker syntax passes, and the Worker bundle succeeds. The fresh cloud and Worker deployment builds passed. Live verification passed exact assets, login rendering, security headers, enabled parsing, anonymous API rejection, and unchanged `parserium.com`. The control plane confirmed the deployed version, all expected bindings, and both existing secrets by name. No D1 data, migration, Queue, R2, Container, credential, or public-site change was made.

For the Documents and Activity line-reduction pass, the browser regression first measured the old one-pixel panel, metric, heading, event-card, and row dividers, then passed after the CSS-only correction. Documents now separates content with whitespace, a soft spotlight surface, row spacing, hover, and one faint table-header rule. Activity retains its timeline spine and uses a single soft Recent searches surface without card or row rules. All 87 web tests across 23 files and all 83 cloud tests passed. TypeScript, both Vite builds, Worker syntax and bundle, the authenticated browser and accessibility flow, and desktop/mobile screenshot review passed. The live verifier passed exact assets, login rendering, security headers, enabled parsing, anonymous API rejection, and unchanged `parserium.com`. The control plane confirmed version `287d04c2-6ddb-4511-92a9-78d24e567475` at 100 percent, all expected bindings, and both existing secrets by name. Wrangler reused Container image digest `bf70569ab5a4e21e29a3315e51b5ee5255028b611bed80ee1fad87565cdaa3ac` and reported no Container application changes. No backend or data behavior changed.

The full Documents and Activity redesign is deployed in version `531ef6a6-607a-465e-9c60-df55c8315c87`. TDD browser regressions first reproduced the below-fold desktop document ledger, missing spotlight action, late timeline start, and horizontally hidden mobile document actions. The final implementation passes those regressions, the complete authenticated browser and accessibility flow, all 87 web tests across 23 files, TypeScript, both Vite builds, and responsive screenshot review. The live verifier passed exact assets, login rendering, security headers, enabled parsing, anonymous API rejection, and unchanged `parserium.com`. The control plane confirms version 21 at 100 percent with all expected bindings and both existing secrets by name. Wrangler reused Container image digest `bf70569ab5a4e21e29a3315e51b5ee5255028b611bed80ee1fad87565cdaa3ac` and reported no Container application changes. No backend, data, migration, credential, or public-site behavior changed.

The Evidence Wall UI is deployed in version `e6b77f05-cc1e-443d-a2c6-e45f9257d27f`. It preserves the existing feature set while rebuilding the information architecture and visual system across Collect, Documents, Activity, upload, settings, and document inspection. The Documents spotlight was removed rather than repurposed. The corpus ledger now exposes real row state, and the Activity page presents separate Parse and Search lanes. A real browser check caught and fixed two implementation defects before deployment: low-contrast corpus indices and unresolved design tokens on portaled drawers. The final verification passed 92 web tests across 27 files, 83 Cloud Worker tests, TypeScript, both Vite builds, Worker syntax, the Worker bundle, the full authenticated browser and accessibility flow, and responsive screenshot review. The live verifier passed exact assets, login rendering, security headers, enabled processing, anonymous API rejection, and unchanged `parserium.com`. The control plane confirms the version at 100 percent with all expected bindings and both existing secrets by name. Wrangler reused Container image digest `bf70569ab5a4e21e29a3315e51b5ee5255028b611bed80ee1fad87565cdaa3ac` and reported no Container application changes. No backend, data, migration, credential, or public-site behavior changed.

The backend parser tests also passed. The production container image built locally, ran as UID/GID 10001, and parsed the ruled-table fixture through its real HTTP endpoint with one page, one table, and 116 Markdown bytes. `npm audit` reported zero known vulnerabilities at that point in time.

## Staging resources

- Worker: `parserium-cloud-staging`
- D1: `parserium-cloud-staging`, ID `ea0804d2-f770-4c69-8ac6-29ae0234a3a6`
- Private R2: `parserium-cloud-staging-files`
- Queue: `parserium-cloud-staging-parse`, ID `2d8bd82671404fbab0f8c7f86c68d7c4`
- Dead-letter queue: `parserium-cloud-staging-parse-dlq`, ID `5ced3e0bbc7c4634a4f7769a2119c089`
- Container application: `parserium-cloud-staging-parsercontainer`, ID `a03bc01d-4b58-4a4d-8d5a-21f957d3065a`
- Durable Object namespace: `930e2e75a4474271a3f3a31226ce690a`
- Cron: every five minutes

The R2 bucket has no public `r2.dev` endpoint or custom domain. Migrations through `0006_discovery.sql` are applied remotely, and none are pending. Queue concurrency is one and the dead-letter queue is configured. The named Container instance is inactive after the application deployment.

## Current deployment state

Cloudflare's control plane reports the custom domain `staging.parserium.com` attached to the production environment of `parserium-cloud-staging`, with version `e6b77f05-cc1e-443d-a2c6-e45f9257d27f` deployed at 100 percent. The production content API contains the parsing UI and health route.

The edge hostname serves the new parsing UI. Repeated cache-busted requests to the public health path return HTTP 200 with `{"ok":true,"processing":"enabled"}` and `Cache-Control: no-store`.

The zone also contains a more-specific no-script route:

```text
staging.parserium.com/*
route ID: 97bb2285bd264adfb86ff241d53d1e6d
script: null
```

That route is preserved because it excludes the older `*.parserium.com/*` preview Worker. The user approved temporarily attaching this exact route to `parserium-cloud-staging`. The API confirmed the new script value, but the first 12 cache-busted health checks over about 25 seconds still returned HTTP 401. The route was restored to `script: null`, as verified by a fresh API read.

Several minutes later, the edge converged to the new Worker with the original no-script route restored. Six further checks over 50 seconds consistently returned the disabled health response. The no-store headers and cache-busting exclude ordinary response caching. Delayed Cloudflare edge routing convergence is the best-supported explanation, but Cloudflare does not expose enough internal state to prove which control-plane refresh triggered it. The public preview retained the same title, byte length, and SHA-256 fingerprint, and no Container instance started.

The disabled safety checks passed before activation: unauthenticated submission/status/output returned HTTP 401, R2 remained private, the required Worker bindings and Google secret were present, the Queue wiring was intact, D1 had no documents or parse jobs, and no Container instance was running. The same checks remained safe after activation. Do not remove the no-script exception, alter DNS, or change the wildcard preview route.

## Configuration and deployment

`scripts/staging-config.mjs` generates the staging-only configuration and requires an explicit `PROCESSING_ENABLED=true|false`. The ignored `wrangler.staging.json` currently sets it true. The generated configuration includes the custom domain, private R2 binding, Queue producer and consumer, dead-letter queue, SQLite Durable Object export, Container limits, rate limiter, and cron.

Credentials are not stored in this package. Load Cloudflare and Google credentials only into the operator process. Never print or commit them. `GOOGLE_CLIENT_SECRET` is already installed as a Worker secret.

`FIRECRAWL_CREDENTIAL_KEY` is also installed. This is the base64-encoded 32-byte AES-GCM wrapping key, not a Firecrawl account key. `node scripts/ensure-credential-key.mjs` checks staging secret names and creates it only if absent. Preserve it across deployments. The workspace owner supplies their Firecrawl account key through Settings; the browser can never retrieve the saved secret.

The reviewed staging config binds `ASSETS` to `../web/dist-cloud`. Build the application before deploying. The application expansion spec is `docs/superpowers/specs/2026-09-13-cloud-application-expansion.md` at the worktree root.

Final staging gate:

1. Have the user sign in and upload the real ruled-table PDF.
2. Submit one parse job and observe queued/running then succeeded.
3. Inspect and download the actual Markdown.
4. Refresh, log out, log in, and retrieve it again.
5. Delete the document and confirm the original and output disappear.
6. Inspect D1, R2, Queue, and Container state afterward.

The first post-activation submission used a separate five-page document. It validated, then exhausted two attempts and ended with no output. Reproduction against the exact downloaded R2 bytes on the unchanged production image at 0.25 vCPU and 1 GiB returned `parser_timeout` after about 53 seconds. The temporary private copy and diagnostic containers were removed. Persistent observability was not enabled for the failed request, so its later Container/RPC exception is not recoverable from stored logs.

The required one-page ruled-table fixture then succeeded on attempt 1. D1 recorded one page, one table, and 1,169 ms runtime; private R2 contains the confirmed 116-byte Markdown object; the Container stopped; and the user confirmed preview and download worked. The pinned LiteParse Linux build incorrectly emits the fixture's first grid row as a paragraph plus rule instead of the table header. The same behavior was reproduced with LiteParse 2.14.4 on Linux, while the Windows wheel emits the expected table. This is an unresolved upstream parser-quality difference, not a durable storage failure.

## Google and operator contract

The exact OAuth callback is `https://staging.parserium.com/api/cloud/v1/auth/callback`. Existing users sign in without another invitation. New users require an unexpired email-bound invitation. Identities are keyed by Google's issuer and subject, never email alone.

Operator commands make real D1 writes and never create resources or run migrations implicitly. From `apps/cloud`, with scoped Cloudflare credentials loaded privately:

```powershell
npm run operator -- --confirm-remote workspace-create --name "My workspace"
npm run operator -- --confirm-remote invite --workspace-id ID --email EMAIL
```

Do not blindly retry either command after a network timeout. Inspect D1 first because the write might have succeeded.
