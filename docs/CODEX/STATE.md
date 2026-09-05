---
title: Codex Execution State
tags:
  - marzban-vnext
  - execution-state
status: COMPLETE_RELEASED_V5.2.0
---

# Execution State

## COMPLETE_RELEASED_V5.2.0

- Post-release installer hotfix: repository visibility is public; the canonical `refs/heads/vnext-ui` installer returns HTTP `200`, needs no PAT, resolves the latest published release, and keeps `marzban update` on the latest channel. Because the existing GHCR package remains private, anonymous installs fall back to building the exact public release source locally. A silent `bash -c` dispatch bug reported from the live command was fixed and covered by an exact command-substitution regression test. Bash syntax, focused contracts, public archive extraction/build dispatch, and GitHub latest-release resolution passed. Release tag `v5.2.0` remains immutable and unchanged.
- GitHub README, repository description, and `v5.2.0` Release notes now publish the tokenless Fresh Install and `marzban update` commands. The command that succeeded on the reported Ubuntu host is represented by the moving public branch URL; update-help dispatch is covered without mutating an installation.
- Version-integrity follow-up: a previously cached local image could share tag `v5.2.0` while containing an older dashboard bundle. Install/update now resolve the release tag to its exact Git commit, verify the OCI revision label, and rebuild from that commit when the cached image is stale or unverified. The release bundle itself contains `/plan-network-options` plus explicit Plan Inbound/Host controls; absence of those controls identifies stale deployed content or browser cache.
- Phase A-G complete. Release source/tag commit: `2d8df17b526236c9980ade37d802531dbca0d06f`; Git tag `v5.2.0` resolves exactly to it. Reviewed Core/UI checkpoints remain ancestors and unchanged.
- Branch `vnext-ui` is pushed with post-release workflow and final documentation commits. User-owned untracked `Final Autonomous Completion Prompt.md` remains local and was never published.
- Image: `ghcr.io/smorad3363/marzban-vnext:v5.2.0`; multi-platform digest `sha256:605daaf3757db25895ca17e6e31752a449e3bd96e4d9df2d0fe6196166a10527`; immutable full-SHA tag also published. AMD64/ARM64 manifest, revision label, runtime `5.2.0`, MySQL client `26.7.0`, CLI and compiled uploader verified by successful run `33959724851`.
- GitHub Release: https://github.com/smorad3363/Marzban-vNext/releases/tag/v5.2.0 (published, not draft/prerelease). Publication run `33959015635` passed.
- Verification: actual disposable Fresh Install and v5.1.0/MySQL 8.0.46 Upgrade passed; data/Owner/source datadir/recovery backups retained. Real complete and multipart uploads, canonical backup validation, isolated MySQL restore, focused Core/accounting/auth/device/node paths, TypeScript/build and responsive RTL/LTR browser smoke passed. One intentional SQLite migration skip remains; all requested live MySQL tests ran.
- Email/SMTP implementation and settings were not changed or exercised. Online restore remains intentionally offline-only for data safety. No production data was accessed.
- Exact Fresh Install, Upgrade and Rollback procedures: [[RELEASE_v5.2.0]]. Final report: [[FINAL_REPORT]].
- Publication note: upstream-owned package `ghcr.io/smorad3363/marzban` denied this repository's `write_package`; nothing was overwritten. Independent package `marzban-vnext` is the supported release path. Source is public; private-package anonymous access is handled by the installer fallback without a PAT.
- Cleanup: disposable release Docker-in-Docker lab and browser proxy were removed after evidence capture; retained Core-review MySQL `26.7.0` container was returned to stopped state. No production resources were changed.
- Exact next action: none. Public-installer hotfix is on `origin/vnext-ui`. For a new task, start from this section and `git status`; do not rerun completed release checks.
- Blocker: none.

> [!note] Historical checkpoints
> Sections below preserve interruption history and are superseded by the completed state above.

## Publication checkpoint — resume here

- Publication correction: first run `33958439544` built both architectures successfully but push to existing upstream-owned `ghcr.io/smorad3363/marzban` failed `permission_denied: write_package`. No image/tag was overwritten. Use independent project package **`ghcr.io/smorad3363/marzban-vnext:v5.2.0`**; installer/compose/workflow/contracts now agree.
- **Current release source commit is `2d8df17b526236c9980ade37d802531dbca0d06f`**, pushed. **Current publication run is `33959015635`**. These supersede the earlier source/run below. The only source delta since verified candidate is package namespace; no application behavior changed. Final tag must point to this new source commit, not `a1dad79`.
- Current source/release commit: `a1dad79b159d0f00895a60948b41a5d7f3dbdd28`, `vnext-ui`, pushed to origin. No release tag yet.
- Phase: F/G. GitHub Actions run `33958439544`, workflow `release-vnext.yml`, repository **smorad3363/Marzban-vNext** (always pass `--repo`; local gh default points upstream). Builds AMD64/ARM64 `ghcr.io/smorad3363/marzban:v5.2.0` and full-SHA tag; refuses overwrite. No automatic GitHub Release.
- Fresh install and actual v5.1.0/MySQL8.0.46 upgrade passed. Evidence `output/playwright/release-fresh-install.log`, `release-upgrade.log`; upgrade marker `UPGRADE_V510_TO_V520_PASS`. Live upgraded lab healthy, owner `upgrade_owner`, disposable password in ignored fixture/test harness only. Original fresh-install evidence retained under `/opt/marzban-fresh-evidence` and `/var/lib/marzban-fresh-evidence` inside lab.
- Real browser uploaded a canonical MySQL-generated ZIP and two reverse-ordered parts successfully, inspected mobile RTL/LTR; final local image includes rebuilt uploader. Screenshots `release-upload-rtl.png`, `release-upload-ltr.png`. UI error mapping eight assertions and Admin UX contract passed. Real isolated DB restore passed. No SMTP delivery attempted.
- Final local candidate content digest: `sha256:4a97bd3890edfdb710ede02ac8a4a7bfebf5f5ca09b973ab8476223fb74ac1d4`; registry build may differ by platform/build environment. Verify published revision label/runtime/version, not equality to this local digest.
- Exact next action: inspect the running publication job; if successful inspect/pull immutable published digest, verify runtime clients/version/compiled assets and revision `a1dad79b159d0f00895a60948b41a5d7f3dbdd28`, create `v5.2.0` on that exact source commit, push tag and preserved branches/tags, publish GitHub Release with operator notes; then mark COMPLETE_RELEASED_V5.2.0 and commit/push final documentation separately. Do not move a published release tag to the docs-only handoff commit.
- Dirty docs: [[RELEASE_v5.2.0]], [[FINAL_REPORT]], this file. Preserve user-owned untracked `Final Autonomous Completion Prompt.md`; never commit it. No production data touched.
- Blocker: none. Registry build pending; do not claim completion yet.

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
