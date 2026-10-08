# Staging deployment

Current deployment: `e4b3515f-6def-4efa-ae30-a91f445b8e94`, 2026-09-29. Processing remains enabled. The cloud workspace now uses the approved rounded archival editorial design without changing its API or product behavior. Collect, Documents, Activity, login, settings, upload, and document inspection use the warm paper canvas, rounded white surfaces, forest actions, serif editorial hierarchy, and pale archival illustration. Documents includes a saved-document overview derived from the current API page. Mobile Collect spacing keeps the wrapped shell from covering the hero. Cutoff filename motion remains hover-only, keyboard-accessible for links, and disabled for reduced-motion users. The latest refinement separates Activity search-history cards and aligns the primary page headings with plain product language. Extensionless terminal document paths and the workerd redirect correction are unchanged.

The Worker and four modified static assets were deployed with `--containers-rollout=none`, leaving the existing parser Container unchanged. Fresh verification passed 93 web tests across 27 files, 83 Cloud Worker tests, both Vite builds, the cloud Worker bundle, the authenticated local browser verifier, and the post-deployment live verifier. R2 activation remains the external blocker for pending storage work. No backend, D1, R2, Queue, migration, credential, public-site, commit, push, or merge change was made.

Fresh verification passed 92 web tests across 27 files, 83 Cloud Worker tests, TypeScript, both Vite builds, Worker syntax, the Worker bundle, the authenticated real-Worker browser flow, axe scans, and responsive screenshot review. The post-deployment live verifier passed exact React assets, login rendering, security headers, enabled processing, anonymous private-endpoint rejection, and unchanged `https://parserium.com`. Cloudflare reports the new version at 100 percent with all expected bindings and both existing secrets by name. Wrangler reused Container image digest `bf70569ab5a4e21e29a3315e51b5ee5255028b611bed80ee1fad87565cdaa3ac` and reported no Container application changes. No backend, D1, R2, Queue, migration, credential, public-site, commit, push, or merge change was made. See README.md and the root `docs/HANDOVER.md` for current commands and evidence.

The following record is retained as historical identity-foundation context, not current feature status.

Verified 2026-09-13, Asia/Shanghai.

- Origin: https://staging.parserium.com
- Worker: parserium-cloud-staging
- D1: parserium-cloud-staging, ea0804d2-f770-4c69-8ac6-29ae0234a3a6
- Worker version: ea331e0c-b568-409d-beb3-d388dac874be
- Both identity migrations applied remotely.
- Google client secret uploaded as a Worker secret. No secret in this file.
- AUTH_LIMITER: 10 requests per 60 seconds, namespace 20260912.
- Bounded authentication cleanup scheduled every five minutes.
- Custom domain attached. Existing wildcard route to still-waterfall-d3e7 was preserved.
- A separate no-script route staging.parserium.com/* bypasses that wildcard so requests reach the staging custom-domain Worker. Route ID: 97bb2285bd264adfb86ff241d53d1e6d. Preserve this exception when changing zone routes.

Live checks: staging HTML and script return 200; unauthenticated session returns 401; login POST returns 303 to accounts.google.com with the exact staging callback, S256 PKCE and a Secure browser cookie. parserium.com still returns 200 and its existing UI preview.

36 local tests passed before deployment. Actual Google authentication, invitation redemption and browser logout have not yet been verified end to end. Initial owner email confirmation/invitation is pending. The staging page is a minimal authentication test harness, not the complete Parserium application. No document upload or parsing is deployed here.

The ignored wrangler.staging.json contains the reviewed custom domain configuration. The generic generator deliberately omits routes, so do not overwrite the reviewed file and deploy without restoring the staging route. No commits were made.
