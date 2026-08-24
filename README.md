# Parserium Collector

Parserium Collector is an independent, local-first document collection dashboard designed to work with a self-hosted Firecrawl instance through its HTTP API.

The repository is in platform-foundation development. It currently makes no claim that discovery, acquisition, document processing, review, export, backup, security acceptance, or scale acceptance is complete.

The current foundation publishes one browser and API port on `127.0.0.1`. PostgreSQL and the worker remain private. Firecrawl, SearXNG, processors, and document collection workflows are not enabled in this slice.

Parserium Collector is independent software and is not an official Firecrawl product.

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
