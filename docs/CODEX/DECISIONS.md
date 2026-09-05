---
title: Codex Decisions
tags:
  - marzban-vnext
  - decisions
---

# Decisions

- Existing Git repository is authoritative per bootstrap override; no clone or ZIP download.
- `upstream` remains `https://github.com/smorad3363/Marzban.git`.
- Baseline is exact annotated tag `v5.1.0`, resolving to `c824e822a2f5e41d91b894aabd2a7b9c77a200d2`.
- Core work remains on `vnext-core`; complete UI redesign remains isolated on `vnext-ui`.
- MySQL default is pinned to `mysql:26.7.0`; no floating `latest` release images.
- Existing React 18, Chakra UI, Framer Motion, and ApexCharts stack remains.
- Commercial Plans contain product terms and price; Access Groups own network scope and propagate independently.
- Admin Plan management is removed; Admins consume compact Plan summaries only in create and quick-renew workflows.
- Checked user IDs are the sole target source for redesigned bulk operations; legacy scoped jobs remain API-compatible.
- Panel backups use a logical MySQL dump inside a checksummed portable archive; raw MySQL directories are never the backup format.
- Restore archives must validate format, safe paths, and every member checksum before mutation. The 2026-09-05 review disables online restore because a marker does not quiesce API/scheduler/accounting writers. Offline recovery must preserve the source and validate an isolated restore before switching.
- ALLOCATED_TRAFFIC Form edits use the same centralized quote for preview and execution: added bytes at remaining-duration pricing, or the final allowance at the purchased extension preset. Wallet debit and user mutation share a transaction; no refund is created by an edit.
- Explicit Access Group node selections exclude the main core and other nodes; no node selection retains legacy unrestricted placement. Enforcement covers all device-slot credentials and reconnect configuration.
