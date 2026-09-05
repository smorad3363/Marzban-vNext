---
title: Codex Architecture Map
tags:
  - marzban-vnext
  - architecture
---

# Architecture Map

- `app/`: FastAPI application, models, services, API routes, migrations, and Xray integration.
- `cli/` and `marzban-cli.py`: management CLI.
- `scripts/`: installer, health checks, and development tooling.
- `dashboard/`: React/Chakra dashboard source.
- `tests/`: focused backend, migration, release-contract, and UI-adjacent tests.
- `docker-compose.yml` and `Dockerfile`: runtime deployment contract.
- `app/utils/marzhelp_policy.py`: shared Create/Edit/Renew/Bulk/Reset authorization and resource accounting.
- `app/utils/admin_plans.py` + `app/utils/access_groups.py`: commercial entitlement and independent network assignment.
- `app/utils/owner_pricing.py` + `app/utils/money_billing.py`: Owner presets, deterministic Toman prices, wallets, and ledgers.
- `app/utils/stage11_operations.py` + `app/routers/backup.py`: logical portable backup, transport preparation, validation, retention, and Owner restore flow.
- `app/device_limit/`: last-seen telemetry, node collector health/recovery, deterministic concurrent-IP warnings.

Detailed relationships come from targeted Graphify or `rg` queries; keep this note concise.
