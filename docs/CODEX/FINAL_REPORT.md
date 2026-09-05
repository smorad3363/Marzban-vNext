---
title: vNext Final Report
tags:
  - marzban-vnext
  - final-report
status: publication_in_progress
---

# Final Report

## Current release state — v5.2.0

This section supersedes the historical implementation report below. Release source commit is `2d8df17b526236c9980ade37d802531dbca0d06f`, pushed on `vnext-ui`. GHCR publication run `33959015635` is in progress; final tag/Release and registry parity are pending. See [[RELEASE_v5.2.0]] for exact operator procedures and [[STATE]] for resume state.

Actual Linux Fresh Install and v5.1.0/MySQL 8.0.46 Upgrade to v5.2.0/MySQL 26.7.0 passed in isolated Docker-in-Docker. Owner, sentinel, source datadir, logical dumps and migration head were preserved. Real backup generation, full/split upload and restore into a separate empty database passed. The production image now includes pinned Oracle MySQL clients; runtime CLI shebang and installer/version/recovery safety were corrected. Email/SMTP remains frozen.

Focused consolidated evidence: `62 passed, 2 failed, 1 skipped`; both failures were stale source-contract tests, fixed and directly verified (`3 passed`). The skip is intentionally unsupported SQLite migration coverage. All three live MySQL integration tests ran successfully. Additional Plan renewal retry accounting passed after updating its fixture to current Owner-management/Plan-only policy. UI error mapping: eight assertions; Admin UX contract passed. Production frontend generated; real browser full/split uploader and mobile direction checks passed. No full suite rerun.

Restore is **offline-only**; the previous statement promising online pre-restore mutation below is historical and no longer true. Review reports [[ASTRA_CORE_REVIEW]] and [[ASTRA_UI_REVIEW]] remain intact. `vnext-core` and reviewed checkpoint tags were not changed.

## Historical pre-review implementation report

## Outcome

Roadmap phases 1-13 are implemented on isolated branches. `vnext-core` preserves backend/platform work; `vnext-ui` adds the disposable white-label and UI layer. Baseline and Core checkpoint tags remain intact.

## Critical Paths

| Path | Evidence | Result |
|---|---|---|
| Release/install integrity | release contracts, installer syntax, exact tag verification | Pass |
| MySQL migration | pinned image/preflight contracts; MySQL integration tests discovered | Pass; three live-MySQL cases skipped without a configured test server |
| Owner/Admin policy | hierarchy, management, billing, and CLI tests | Pass |
| Create/edit/renew/reset | policy, Plan, namespace, and user operation tests | Pass |
| Plan and Access Group propagation | network scope and propagation tests | Pass |
| Exact bulk operations | checked-selection, multi-action, idempotency, and partial-failure tests | Pass |
| Device/IP reconnect | device integration and Xray node-state tests | Pass |
| Backup/restore | manifest, checksum, split transport, secret preservation, and MySQL integration contracts | Pass |
| Health/restart contract | healthcheck tests and application compile | Pass |
| Owner/Admin UI visibility | Playwright CLI plus UI authorization contracts | Pass |
| White-label | API tests, source scan, dynamic title/favicon, Playwright visible-text scan | Pass |
| Responsive RTL/LTR | Playwright at `1440x900` and `390x844` | Pass |

## Verification Summary

- Backend consolidated run: `115 passed, 3 skipped`; one obsolete test expected delegated Plan management, was corrected to the roadmap’s Owner-only rule, then rerun: `1 passed`.
- Branding API: `4 passed`.
- Frontend: TypeScript and Vite production build pass.
- UI source contracts: Plan inbound selection, admin hierarchy authorization, and admin UX pass.
- Playwright: Owner Settings renders; Admin does not receive Plans/Settings navigation; responsive login renders in RTL/LTR; final console has zero errors; no visible legacy identity.
- Alembic: one head, `b8d5f0a3c721`.

## Operational Notes

- Apply Alembic migrations before first production start.
- Backup restore remains Owner-only and creates a pre-restore backup before mutation.
- The three skipped MySQL integration cases require an explicit disposable live MySQL test URL; static preflight, migration, and archive contracts passed.
- Private publication target: `https://github.com/smorad3363/Marzban-vNext`.

## Publication

- `vnext-core`: `cb602a41277151223dc729754b66a82dc4736637`
- UI checkpoint: `8146bae1fb615da311200ae3da8d9b343a966c90`
- Tags pushed: `baseline-v5.1.0`, `checkpoint-core-complete`, `checkpoint-ui-complete`
- Default branch: `vnext-ui`
