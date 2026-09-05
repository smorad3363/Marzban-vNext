---
title: Codex Execution State
tags:
  - marzban-vnext
  - execution-state
status: in_progress
---

# Execution State

## Live release checkpoint — installer work

- Latest completed evidence: actual Linux Fresh Install, `help/version/status`, overwrite refusal and downgrade refusal passed in Docker-in-Docker. HTTP complete/split upload and Owner boundary passed. Real MySQL `26.7.0` archive generation and restore into empty `release_restore_test` passed (17,211 bytes, two parts); source remained intact.
- Consolidated Core gate: `62 passed, 2 failed, 1 skipped`; failures were stale source-contract fixtures (`_save_upload` removal and old installer defaults), corrected; affected three checks passed. Skip is explicitly disabled SQLite coverage, not live MySQL. All three live migration tests were included against disposable `release_final_marzban_test` on port `33317`.
- Additional runtime findings fixed: missing MySQL client binaries, Windows CLI shebang, backup dump requiring global privileges. Image now copies Oracle `mysql`/`mysqldump` from pinned `mysql:26.7.0` (MariaDB client was tested and rejected for `SHOW PACKAGE STATUS` incompatibility); dump uses `--no-tablespaces --set-gtid-purged=OFF`. Read-only `.env` mount added for complete configuration backups. No Email/SMTP changes.
- Browser real-login/Owner dashboard passed; initial 401 before login is expected, not an application exception. Fresh-image build overlapped frontend build and captured old bundle; do NOT treat that as final upload UI evidence. Wait for `release-ui-followup-build.log`, then build final candidate and inspect actual bundled uploader. Sidebar Settings/theme/logo controls had unreadable light-mode foreground; limited explicit colors and Persian Settings label fixed.
- Remaining mandatory work: actual v5.1.0 upgrade (baseline image downloaded inside lab), updated-image/browser upload smoke, final image runtime/CLI parity, reviewed branch/tag publication and GitHub Release/operator commands. No final release published yet.
- Branch: `vnext-ui`; A+B+C committed as `a2ddd44cedc52d597bbf1c7f294feef9159e60f5`. Earlier reviewed checkpoints remain unchanged.
- Current phase: D, installer safety implementation; E/F/G are NOT complete. `VERSION`/runtime/compose are prepared for `5.2.0`, not released.
- A+B+C verification: 14 backup tests, 8 UI-error assertions, existing UX contract and TypeScript passed. No final consolidated suite yet.
- Installer fixes in progress: correct fork/default ref, embedded CLI release and runtime/MySQL parity, fresh-install stamp ordering, refuse install overwrite and application downgrade, pre-update logical/file recovery snapshot, no unsafe automatic image rollback after migrations, preserve retained CLI backups and exclude raw versioned MySQL directories, safe password alphabet, authenticated private-repository downloads.
- Docker build context excludes `.venv*`, `output`, and `.playwright-cli`. Email/SMTP remains unchanged.
- Disposable Linux daemon: `marzban-release-lab-20260905` (`purpose=marzban-release-disposable`), Docker-in-Docker, no host socket or production data mounts. Bash/curl/jq/yq/util-linux/tar/rsync installed. MySQL image pull in progress. No actual fresh/upgrade success claimed yet.
- Active build evidence: `output/playwright/release-build.log`, `output/playwright/release-docker-build.log`. Frontend production build and local `marzban-release-candidate:v5.2.0` Docker build started; check outcomes before rerunning. CLI changes after build snapshot require final image rebuild after verification fixes.
- Exact next action: finish isolated actual installer/dispatch tests, including a v5.1.0 upgrade and failure/downgrade paths; complete release workflow gating, focused API/upload/browser verification, final immutable image build/runtime verification, then publication.
- Publication: private origin `smorad3363/Marzban-vNext`; GitHub authenticated. Private-repository installation requires repository read authentication; registry auth still to verify. No release/tag/image has been published.
- Dirty files: use `git status --short`; installer/version/docker-context changes and generated frontend build are task-owned. Preserve untracked `Final Autonomous Completion Prompt.md` locally, never publish it.
- Blocker: none currently; work remains in progress.

## Final release execution — 2026-09-05

- Current phase: A+B+C checkpoint — upload transport and UI safety complete; continue D installer/release integrity.
- Starting/current Git HEAD: `ecdaa04ac16356088a6ed4a925849a0d75667748` on `vnext-ui` (update with each checkpoint).
- Authority: user explicitly approved the complete `Final Autonomous Completion Prompt.md`, phases A–G, including final `v5.2.0` image and GitHub Release.
- Completed: unified uploader, ordered new/legacy split normalization, duplicate/missing/mixed/checksum checks, temporary cleanup; common canonical validation retained. UI raw error/code exposure removed from shared errors, form errors, bulk results, and node errors. Email/SMTP code and configuration unchanged.
- Dirty files: A+B+C source/tests and these notes pending checkpoint; user-owned untracked `Final Autonomous Completion Prompt.md` must be preserved locally and excluded from publication. Use `git status --short` for the exact post-checkpoint list.
- Constraints: Email/SMTP frozen; preserve reviewed Core/UI and all published history; no production data. Online restore stays fail-closed until a safe offline recovery path is used.
- Exact next action: commit A+B+C checkpoint; inspect installer install/update/version/health dispatch, correct exact release source and version integrity, and build disposable Linux Fresh Install/Upgrade verification.
- Verification: backup focused `14 passed`; UI error mapping `8 assertions passed`; existing UX contract passed; TypeScript check pending completion. Final consolidated verification remains pending after installer work.
- Publication: not yet versioned or released; target existing origin and configured GHCR namespace. Do not claim `COMPLETE_RELEASED_V5.2.0` until release work is complete.
- Blocker: none.

## UI review — 2026-09-05

- Branch: `vnext-ui`; starting commit `769e934121f697f7cd1bf9c9eed985fe57ef9b1f`.
- Scope: focused frontend review only; preserve reviewed Core and existing visual design.
- Fixed: Settings hash navigation, offline-only restore guidance, backup credentials/validation states, keyboard-accessible brand uploads, Plan creation entry points for Admins, Core creation-mode compatibility, bulk/renewal retry identity, transient-route recovery, light contrast, reduced motion, and Select RTL spacing.
- Verification plan: one focused Playwright matrix using disposable browser fixtures, desktop/mobile, RTL/LTR, Owner/Admin, critical workflows, themes, keyboard, and console. No production data.
- Browser matrix: critical workflows verified against disposable local fixtures; Admin Plan creation and Quick Renew requests captured. Bulk reconnect repeated the same payload and operation ID. Desktop/mobile, direction stress, light/dark, keyboard, Owner-only navigation, Settings, Nodes/Admins empty states, backup validation, and white-label inspected. See [[ASTRA_UI_REVIEW]] for limitations.
- Verification: final build/TypeScript passed; Plan inbound `14 assertions passed`; hierarchy `PASS`; updated UX contract passed. Direct follow-ups for Select RTL/LTR geometry and Admin quick/empty creation passed. No backend suite rerun.
- Review checkpoint: `checkpoint-ui-reviewed` on `vnext-ui`; reviewed Core remains unchanged at `30fdb79aed138b5c8eb7057814bee945a035ecf0` and is an ancestor of UI.
- Publication target: authenticated `origin`; push `vnext-core`, `vnext-ui`, and reviewed checkpoint tags without force. No Release or container image is created by this review.
- Exact next action: publication only if the local checkpoint is not yet present on origin; otherwise none. Resume from [[ASTRA_UI_REVIEW]] for a new task. Do not repeat completed Core or browser verification merely for reassurance.

## Technical review — 2026-09-05

- Branch: `vnext-ui`; verified `vnext-core` fixes are merged without replacing UI work.
- Review scope: technical/Core only. No UI redesign or production database access.
- Disposable MySQL: all three requested integration tests passed on `8.0.46` (port `33316`) and `26.7.0` (port `33317`). The final `26.7.0` run additionally covers new partial-migration and stale-wallet regressions. No production data accessed.
- Fixed: live migration errors/resumability, migration write window/downgrade guard, bulk replay and billing, central policy, node scope/stale state, backup validation/scheduling, and version-integrity gaps. Details: [[ASTRA_CORE_REVIEW]].
- Consolidated verification: `168 passed, 2 failed, 1 skipped, 1 deselected`; two stale collector fixtures corrected, affected file `3 passed`. The remaining skip is an intentionally disabled SQLite migration test; the three requested MySQL tests are not skipped.
- Dependencies: all 68 installed packages compatible; no upgrades. Bash syntax and downgrade guard passed.
- Merged-head follow-ups: IPv6 discovery startup fallback and legacy/panel retention isolation fixed on Core; affected regressions `2 passed`.
- Direct follow-ups: accounting retries `3 passed`, retained-group renewal `1 passed`, transport splitting `1 passed`. No consolidated suite repeat.
- Safety change: online restore fails closed with `offline_restore_required`; use isolated offline recovery as documented.
- Core fixes committed and tagged `checkpoint-core-reviewed` at `30fdb79aed138b5c8eb7057814bee945a035ecf0`; this commit is an ancestor of `vnext-ui`.
- Integration: live MySQL merged head `b8d5f0a3c721` verified; branding compatibility `4 passed`; dashboard/template diff against the original UI tip is empty.
- Test containers are stopped and retained for optional inspection; no production data was accessed or changed.
- Publication target: `origin` (`https://github.com/smorad3363/Marzban-vNext.git`), both branches and `checkpoint-core-reviewed`; no release or image is created by this review.
- Exact next action: none after publication. Resume from [[ASTRA_CORE_REVIEW]] only for a new task; do not rerun completed verification merely for reassurance.

## Previous implementation record (historical)

- Current phase: Original implementation/publication (historical; not rerun during this review)
- Phase status: Complete
- Branch: `vnext-ui`
- Last checkpoint: Core and UI roadmaps complete; focused final verification complete.
- Completed action: Implemented phases 1-13, including full white-label, Owner branding, premium responsive UI, grouped Owner Settings, exact checked-user bulk UI, Owner-only Plan navigation, light/dark behavior, and RTL/LTR support.
- Verification: consolidated backend `115 passed, 3 skipped`; one stale Owner-only Plan contract corrected and its failed path rerun `1 passed`; branding `4 passed`; frontend build and three UI contract scripts pass; Playwright Owner/Admin visibility, white-label, console, desktop/mobile, and RTL/LTR checks pass; migration head is `b8d5f0a3c721`.
- GitHub: published to private repository `https://github.com/smorad3363/Marzban-vNext`; `vnext-core`, `vnext-ui`, and all checkpoint tags are pushed; default branch is `vnext-ui`.
- The previous UI implementation remains intact. Current next action is listed in the review section above.
- Blocker: None
