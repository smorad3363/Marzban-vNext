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
- Restore validates format, safe paths, and every checksum before maintenance or database mutation, and always creates a pre-restore backup.
