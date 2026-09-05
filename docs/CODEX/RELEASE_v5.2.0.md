---
title: Release v5.2.0
status: complete
tags: [marzban-vnext, release]
---

# v5.2.0 operator handoff

- Release commit: `2d8df17b526236c9980ade37d802531dbca0d06f`.
- Git tag: `v5.2.0` at that exact commit.
- Image: `ghcr.io/smorad3363/marzban-vnext:v5.2.0` (AMD64 and ARM64), digest `sha256:605daaf3757db25895ca17e6e31752a449e3bd96e4d9df2d0fe6196166a10527`.
- Release location: https://github.com/smorad3363/Marzban-vNext/releases/tag/v5.2.0

## Fresh Install

Run in a root shell on a clean Linux host with Docker/Compose available. The repository is private: authenticate GitHub CLI with repository read access first (`gh auth login`). If the container package is private, authenticate Docker to GHCR separately with a credential that has `read:packages`; never paste credentials into public logs.

```bash
: "${GHCR_TOKEN:?Export a GitHub token with read:packages access}"
printf '%s' "$GHCR_TOKEN" | docker login ghcr.io -u YOUR_GITHUB_USERNAME --password-stdin
export GH_TOKEN="$(gh auth token)"
installer=$(mktemp /tmp/marzban-install.XXXXXXXX)
gh api -H 'Accept: application/vnd.github.raw+json' \
  'repos/smorad3363/Marzban-vNext/contents/scripts/marzban.sh?ref=v5.2.0' > "$installer"
bash -n "$installer" && bash "$installer" install --version v5.2.0 --database mysql
rm -f "$installer"
unset GH_TOKEN
marzban create-owner YOUR_USERNAME
marzban version
```

## Upgrade from v5.1.0

Keep an independent verified off-host backup before starting. Run as root. This explicitly installs the new installer first: an old CLI may otherwise select the old repository or unsafe update behavior.

```bash
: "${GHCR_TOKEN:?Export a GitHub token with read:packages access}"
printf '%s' "$GHCR_TOKEN" | docker login ghcr.io -u YOUR_GITHUB_USERNAME --password-stdin
export GH_TOKEN="$(gh auth token)"
installer=$(mktemp /tmp/marzban-update.XXXXXXXX)
gh api -H 'Accept: application/vnd.github.raw+json' \
  'repos/smorad3363/Marzban-vNext/contents/scripts/marzban.sh?ref=v5.2.0' > "$installer"
bash -n "$installer" && bash "$installer" update --version v5.2.0
rm -f "$installer"
unset GH_TOKEN
marzban version
```

MySQL is pinned to `26.7.0`. Older supported MySQL versions migrate by logical dump into `/var/lib/marzban/mysql-26.7.0`; the original datadir is retained. Never mount an old raw datadir into a new MySQL series. Upgrade stops application writers and retains `/opt/marzban/pre-update.*` plus logical migration backups. Allow disk space for source, target and backups. Failed migration/health checks require inspection; do not blindly restart an older application against a migrated schema.

## Rollback / offline recovery

`marzban rollback v5.1.0` intentionally refuses an application downgrade. Do not downgrade Alembic or repoint the old image at the upgraded database.

1. Stop writers on the failed installation:

   ```bash
   docker compose -f /opt/marzban/docker-compose.yml -p marzban stop marzban
   ```

2. Preserve the entire failed installation and both MySQL datadirs. Select the printed **pre-update** snapshot; verify it:

   ```bash
   cd /opt/marzban/pre-update.REPLACE_WITH_EXACT_SNAPSHOT
   sha256sum -c SHA256SUMS
   ```

3. On a separate empty Linux recovery host, restore the snapshot's `.env`, `docker-compose.yml` and `data.tar.gz`. Start only the snapshot's original MySQL version with an **empty** datadir. Import `database.sql` using that MySQL container's root client. The snapshot contains application schema/data, not MySQL system tables.
4. Start the matching `v5.1.0` application image, verify login, users, balances, configuration and subscriptions before switching traffic. Never restore over the live installation. Keep the failed installation until recovery is accepted.

Canonical panel ZIP/multipart uploads are validated in the UI; actual restoration remains offline-only. They are not a license to overwrite a running database. Email/SMTP settings and delivery behavior are unchanged.

## Verification

- Actual disposable Linux fresh install and CLI dispatch passed.
- Actual `v5.1.0`/MySQL `8.0.46` upgrade to `v5.2.0`/`26.7.0` passed; sentinel, Owner, old datadir and recovery dumps retained; Alembic head `b8d5f0a3c721`.
- Focused consolidated Core: 62 passed, two stale test contracts corrected, affected three checks passed; one intentional SQLite skip. All three live MySQL migration tests ran. Renewal fixture updated to Owner-managed/Plan-only policy; affected retry accounting passed.
- Real backup generation and isolated empty-DB restore passed. HTTP full/split upload, missing-part and Owner-boundary checks passed. Real browser full/split uploads and mobile RTL/LTR passed; no page exceptions in that smoke.
- TypeScript and production Vite bundle generated; pinned MySQL client binaries execute inside the final local image. Existing Vite chunk/directive warnings remain.
- Published-image gate `33959724851` passed: manifest contains AMD64/ARM64; revision, runtime `5.2.0`, MySQL client `26.7.0`, CLI and compiled uploader match. Publication workflow `33959015635` passed. No real email/Telegram delivery or production data was used.

Known limitations: private source requires authentication; offline-only Restore; Persian-first interface with some English labels; existing Access Group management remains Owner API-only. Reviewed Core/UI evidence is preserved in [[ASTRA_CORE_REVIEW]] and [[ASTRA_UI_REVIEW]].
