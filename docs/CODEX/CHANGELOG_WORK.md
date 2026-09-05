---
title: Codex Work Changelog
tags:
  - marzban-vnext
  - changelog
---

# Work Changelog

## Core checkpoint

- Bootstrapped from exact upstream `v5.1.0` commit and preserved both roadmaps.
- Added version-integrity CLI and safe resumable MySQL `26.7.0` logical migration.
- Removed the legacy setuptools workaround and pinned APScheduler compatibility.
- Separated commercial Plans from Access Groups with compatibility backfill and host propagation.
- Added Owner pricing/duration settings, three user-creation modes, billing restrictions, and compact Plan visibility.
- Added checked-selection multi-action bulk preview/execution with per-user outcomes.
- Preserved and verified node telemetry reconnect/stale-collector handling.
- Added Owner backup settings, portable checksummed MySQL/application archives, retention, validation, pre-restore backup, and restore orchestration.

## UI checkpoint

- Replaced visible legacy identity with neutral `Operations Console` fallbacks and Owner-configurable name, login title, description, logo, and favicon.
- Normalized slate/blue tokens, responsive navigation, light/dark behavior, focus treatment, touch targets, and reduced-motion handling.
- Added grouped Owner Settings for pricing, backup/restore, branding, and operational policy guidance.
- Connected exact checked-user selection to multi-action preview and execution with per-user failures.
- Restricted Plan and Settings navigation/routes to Owner while keeping Admin Plan summaries inside allowed workflows.
- Added dashboard backup status and preserved operational KPIs, activity, traffic, resource, node, and device surfaces.
- Verified desktop/mobile and RTL/LTR browser behavior with Playwright CLI.

## Bootstrap

- Verified `upstream` URL and fetched tags.
- Verified `v5.1.0` resolves to `c824e822a2f5e41d91b894aabd2a7b9c77a200d2`.
- Created `vnext-core` directly from `v5.1.0`.
- Created `baseline-v5.1.0` at the same baseline commit.
- Archived upstream `AGENTS.md` as `docs/legacy/AGENTS.upstream-v5.1.0.md`.
