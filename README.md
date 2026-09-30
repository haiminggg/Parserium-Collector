# Parserium Collector

Parserium Collector is an independent document discovery and collection dashboard designed to work with Firecrawl through its HTTP API.

The repository is in platform-foundation development. It currently makes no claim that discovery, acquisition, document processing, review, export, backup, security acceptance, or scale acceptance is complete.

The self-hosted development stack publishes one browser and API port on `127.0.0.1`. PostgreSQL and the worker remain private. The repository also includes an isolated hosted-local verification stack for Google OpenID Connect, workspace tenancy, cloud artifact storage, and workspace-managed Firecrawl connections.

Parserium Collector is independent software and is not an official Firecrawl product.

## Deployment modes

The development Compose stack explicitly runs in `self_hosted` mode. Local access still
uses a temporary pairing code from the API container log, and the dashboard remains bound
to `127.0.0.1` by default.

The repository also contains development verification for invite-only OpenID Connect login,
workspace isolation, cloud artifact storage, and encrypted per-workspace Firecrawl credentials.
Hosted mode is not ready for deployment to users. Quotas, audit controls, managed database roles
with forced row-level security, production key management, monitoring, rate limits, and the
remaining hosted release gates must be completed first.

## Hosted Firecrawl connections

In hosted mode, a workspace owner opens **Connections**, chooses **Cloud** or **Remote**, enters
an API token, and saves the connection. Parserium validates the connection before it can be used
for discovery. A connection test performs one metadata-only search and may consume one Firecrawl
search credit.

Remote Firecrawl endpoints require HTTPS, bearer-token enforcement, public DNS, and a secure port
approved by the Parserium operator. Parserium validates DNS and the destination policy before
connecting. The self-hosted edition continues to use the operator-managed
`DASHBOARD_FIRECRAWL_BASE_URL` instead of workspace connections.

Hosted mode requires both `DASHBOARD_CREDENTIAL_ENCRYPTION_KEY_FILE` and
`DASHBOARD_CREDENTIAL_ENCRYPTION_KEY_ID`. Mount the key from an untracked secret file and never
place credential values in Compose files, environment files, source control, browser storage, or
logs. `scripts/hosted-local/setup.ps1` generates a development-only wrapping key for the local
hosted stack. Never copy that key into a production deployment.

## Development status

The approved design is stored in `docs/specs`. Executable work is split into reviewed implementation plans. Public release artifacts are blocked until release hardening verifies matching source, notices, an SBOM, security reporting, backup, supported platforms, and all acceptance suites.

## License

Original source in this repository is licensed under AGPL-3.0-or-later. See `LICENSE`.

## Start the foundation on Windows

Requirements:

- Docker Desktop with Docker Compose
- Windows PowerShell 5.1 or PowerShell 7

Run:

    .\scripts\bootstrap-local.ps1
    docker compose -f deploy\development\compose.yaml up --detach --build

Open http://127.0.0.1:8080. Stop the stack without deleting its named data volumes:

    docker compose -f deploy\development\compose.yaml down

## Start the foundation on Ubuntu 24.04

Requirements:

- Docker Engine with the Compose plugin
- PowerShell 7 for the shared verification command

Run:

    sh ./scripts/bootstrap-local.sh
    docker compose -f deploy/development/compose.yaml up --detach --build

Open http://127.0.0.1:8080. Stop the stack without deleting its named data volumes:

    docker compose -f deploy/development/compose.yaml down

## Verify

The complete foundation gate builds from immutable base-image digests, runs backend and browser tests, checks generated contracts, inspects rendered Compose policy, starts a real PostgreSQL stack, waits for a fresh worker heartbeat, and runs desktop and mobile browser accessibility checks:

On Windows:

    powershell -File .\scripts\verify-foundation.ps1

On Ubuntu 24.04:

    pwsh ./scripts/verify-foundation.ps1

A passing foundation does not enable collection. Firecrawl target operations remain `unsafe_disabled`, and metadata-only search remains `untested` until the discovery plan's isolated no-fetch proof passes.
