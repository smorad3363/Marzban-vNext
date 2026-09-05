---
title: Astra Core Review
date: 2026-09-05
status: core-verified
tags:
  - marzban-vnext
  - core-review
related: "[[STATE]]"
---

# Technical Core review

## Scope and baseline

Reviewed the existing implementation, starting with `AGENTS.md`, `STATE.md`, `DECISIONS.md`, `FINAL_REPORT.md`, Git status, and the `baseline-v5.1.0..checkpoint-core-complete` Core changes. Subsequent inspection used targeted searches and affected files only. No repository rebuild, UI redesign, dependency upgrade, production database access, or full-repository test run was performed.

Review work starts from `vnext-core` commit `cb602a41277151223dc729754b66a82dc4736637`. The previous Core and UI checkpoints remain unchanged. Existing UI work will receive these fixes by merge, not replacement.

## Confirmed issues and small fixes

| Area | Root cause | Fix and focused evidence |
| --- | --- | --- |
| MySQL migration | Integer preset primary key implicitly became AUTO_INCREMENT, which MySQL rejects in its CHECK constraint. | Explicit `autoincrement=False` in migration and model; live migrations exercised. |
| Migration recovery | Child tables/indexes were skipped after partial nontransactional DDL; long migrated names could truncate away version identity; host backfill depended on inbound backfill completion. | Independent existence checks, idempotent seeds/member inserts, version ID before truncated name; live partial-table recovery regression. |
| Downgrade | Foreign-key supporting index was dropped before its constraint. | Drop constraint first; live downgrade/re-upgrade paths. |
| Migration targeting | Alembic overwrote an explicitly supplied database URL with runtime configuration. | Honor explicit URL and escape interpolated environment credentials; disposable tests target only their supplied schemas. |
| Installer data safety | Application writers remained active after the migration snapshot; newer source versions were not refused. | Stop the application before dumping while retaining MySQL; reject unknown/newer source versions before mutation. Executed Bash version-guard regression and ordering contract. |
| Version integrity | A matching image tag did not prove the container used the current image content; CLI installation could stamp a failed installation. | Compare container image ID with expected image ID; validate script syntax and propagate installation failure. |
| Selected bulk operations | `operation_id` was returned but not persisted, allowing retry to repeat changes. Broad exception handling could misreport transaction failures. | Durable job/target results committed with user mutations, request fingerprint conflict checks, actor serialization, deleted-user snapshots, domain-only per-target failure handling. Replay regression. |
| ALLOCATED_TRAFFIC | Bulk preview quoted Toman charges but edits never debited the wallet. | Central adjustment pricing and wallet debit in the existing update transaction; matching preview, no-op replay, insufficient-wallet rollback regressions. Duration extensions price the final traffic allowance for the purchased extension; traffic-only additions price added bytes. |
| Duration and USER_CREDIT | Duration validation existed only in selected routes; USER_CREDIT direct-edit restrictions depended on a separately mutable creation-mode field. | Central preset validation on nonlegacy updates and unconditional USER_CREDIT Plan-only direct-edit enforcement. Legacy compatibility behavior is retained. |
| USED_TRAFFIC | Money-enabled administrators were excluded from Plans despite permitted Plan provisioning. | Keep scoped Plan access and skip upfront allocated purchase charges; consumption remains billed by the usage path. Regression verifies no purchase ledger debit. |
| Concurrent accounting | ORM instances loaded before a lock could retain stale wallet/user values; hourly ledger queries could use an old MySQL snapshot. Deadlock detection inspected the wrapper exception instead of driver error code. | Refresh locked user/wallet state, current-read hourly ledgers, driver error codes `1205`/`1213`; live preloaded-wallet concurrency plus existing credit/renewal concurrency checks. |
| Access Groups | Node selections were stored but never enforced by Xray; batched user responses ignored Access Groups; disabled hosts could be assigned. | Enforce node scope for startup/reconnect and live add/update, including device slots; batch-resolve group host scopes; validate host availability. Main core is excluded when explicit node IDs are selected. Scope/config-copy and existing Plan regressions. |
| Node/collector recovery | Failed disconnect retained cached session/API state; a collector could keep consuming a replaced node or old process/session. | Clear cached state in `finally`; retire collectors when source identity/generation changes. Disconnect, replacement, public-IP/NAT regressions. |
| Backup integrity | Extra ZIP members were not checksummed, duplicates were accepted, manifest/decompression size was unbounded, and `.env` could be treated as a directory. | Require complete member checksums, reject duplicate/unsafe paths, bound members/expanded bytes/manifest, prevalidate every restore destination, handle dotfiles, restrict generated archive permissions. Archive and prevalidation regressions. |
| Backup execution | Persisted schedule/destination settings were not connected to backup jobs; manual backups never delivered to configured destinations. | Scheduled period claims prevent duplicate worker generation; local complete archives use configured email/Telegram delivery, retain files and record failure on delivery errors. Mocked scheduling/delivery-failure regression; no external messages sent during tests. |
| Online restore | A `.maintenance` marker did not stop API, scheduler, or accounting writers during nontransactional SQL import. | Fail closed with HTTP `409`, code `offline_restore_required`, before upload or database mutation. Online restore is intentionally disabled; offline recovery is required. |
| Startup discovery | An IPv6 response from the last IPv4 discovery service raised `AddressValueError` during app/Alembic import. | Treat invalid address responses as discovery failures and continue fallback; deterministic startup regression. |
| Backup retention | Legacy 48-hour cleanup also matched newly delivered panel archives, overriding configured count retention. | Restrict legacy cleanup to its encrypted SQL format; panel archive retention remains controlled by configured policy. |

## Verification

- Disposable Docker MySQL `8.0.46`, container `marzban-core-review-mysql-20260905`, loopback port `33316`: all three previously skipped integration tests passed individually after migration fixes.
- Disposable Docker MySQL `26.7.0`, container `marzban-core-review-mysql26-20260905`, loopback port `33317`: all three integration tests passed. Coverage includes fresh/legacy/partial migration, downgrade/re-upgrade, credit/renewal/wallet concurrency, bulk idempotency/index plans, outbox claims, and isolated restore. Its default EXPLAIN output differs from MySQL 8; tests explicitly request `FORMAT=TRADITIONAL` without weakening index assertions.
- Consolidated focused Core run: `168 passed, 2 failed, 1 skipped, 1 deselected`. The two failures were collector fixtures lacking node registration; corrected fixture file rerun: `3 passed`. No full-suite repeat was performed.
- Additional directly affected accounting retry regressions: `3 passed`, covering SQLAlchemy-wrapped MySQL errors rather than raw driver exceptions.
- Final affected-path follow-ups: retained/archived Access Group renewal `1 passed`; transport splitting `1 passed`. Renewals reapply the current retained group and reject archived groups before charging.
- Merged-head follow-up fixes: startup IPv6 discovery and legacy/panel retention isolation `2 passed`. These were committed back to Core before final integration/publication.
- The skipped test is the deliberately disabled SQLite full-migration test, not one of the three requested live-MySQL tests. The extra MySQL refund test was not part of this bounded verification and was explicitly deselected.
- Dependency verification: `uv pip check --python .venv-win/Scripts/python.exe` reports all 68 installed packages compatible. APScheduler remains pinned at `3.11.0`; no dependency changes.
- Focused Python compilation, full installer Bash syntax, and executable Bash downgrade-guard check passed. Existing UI assets/source remain untouched.

## Operational limits and recovery

1. Do not use the HTTP restore endpoint for live replacement. It now returns a clear safety error. Stop every application writer, preserve the existing database/files, restore the verified logical dump into an isolated database first, run migrations there, and validate before switching configuration and restarting. Cross-series rollback must use the preserved source data/logical backup, never start an older server against newer raw data files.
2. Offline restore helpers validate integrity and destinations, but arbitrary SQL dumps are trusted Owner input, not a sandbox. Production restore was deliberately not executed.
3. SMTP and Telegram delivery were tested with mocks. Real destination credentials, network delivery, multi-process crash recovery, and a complete Linux installer deployment were not exercised.
4. Xray resets usage counters when samples are fetched. Database deadlocks now retry correctly, but this review does not claim atomic durability across a process crash between remote counter reset and database commit. Eliminating that distributed failure window requires a separate durable telemetry protocol, beyond these bounded fixes.
5. No speculative indexes, schema redesign, or large-data benchmark claims were added. Existing bounded queries/index plans were retained and live concurrency paths tested. New schema fixes are in the existing unreleased Core migration; production operators should not manually edit applied migration history.

## Handoff

Core verification is complete. Pending review commit, `checkpoint-core-reviewed`, and merge into `vnext-ui`. See [[STATE]] for the exact next action. MySQL/SQL skills guided locking/current-read and migration checks; Obsidian Markdown guided this durable handoff format.
