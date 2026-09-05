---
title: Codex Execution State
tags:
  - marzban-vnext
  - execution-state
status: core-review-verified
---

# Execution State

## Technical review — 2026-09-05

- Branch: `vnext-core`; existing UI work remains on `vnext-ui`.
- Review scope: technical/Core only. No UI redesign or production database access.
- Disposable MySQL: all three requested integration tests passed on `8.0.46` (port `33316`) and `26.7.0` (port `33317`). The final `26.7.0` run additionally covers new partial-migration and stale-wallet regressions. No production data accessed.
- Fixed: live migration errors/resumability, migration write window/downgrade guard, bulk replay and billing, central policy, node scope/stale state, backup validation/scheduling, and version-integrity gaps. Details: [[ASTRA_CORE_REVIEW]].
- Consolidated verification: `168 passed, 2 failed, 1 skipped, 1 deselected`; two stale collector fixtures corrected, affected file `3 passed`. The remaining skip is an intentionally disabled SQLite migration test; the three requested MySQL tests are not skipped.
- Dependencies: all 68 installed packages compatible; no upgrades. Bash syntax and downgrade guard passed.
- Merged-head follow-ups: IPv6 discovery startup fallback and legacy/panel retention isolation fixed on Core; affected regressions `2 passed`.
- Direct follow-ups: accounting retries `3 passed`, retained-group renewal `1 passed`, transport splitting `1 passed`. No consolidated suite repeat.
- Safety change: online restore fails closed with `offline_restore_required`; use isolated offline recovery as documented.
- Next: commit Core fixes, create `checkpoint-core-reviewed`, merge into `vnext-ui` without UI changes, verify merged migration head, push requested branches/tag under repository completion rules.

## Previous implementation record (historical)

- Current phase: Core checkpoint
- Phase status: Complete
- Git HEAD: `c824e822a2f5e41d91b894aabd2a7b9c77a200d2`
- Branch: `vnext-core`
- Last checkpoint: Core phases 1-10 implemented; focused backend paths pass.
- Files currently being changed: Core installer, database, policy, Access Group, pricing, bulk, device telemetry, backup/restore, tests, and project memory.
- Completed action: Implemented deterministic release/version enforcement, pinned MySQL migration, dependency cleanup, commercial Plan/Access Group separation, centralized creation policy and Owner pricing, Owner-only Plan management with Admin quick-renew summaries, exact-selection bulk operations, verified telemetry recovery behavior, and integrated logical backup/restore.
- Verification: `5 passed, 1 skipped` release contracts; `23 passed` Plan/Access Group/bulk/backup; `83 passed, 1 skipped` policy/billing/device/health; focused new bulk and backup archive tests pass; application compile and shell syntax pass.
- Exact next action: Commit/tag `checkpoint-core-complete`, create `vnext-ui`, then perform white-label and UI redesign.
- Blocker: None
