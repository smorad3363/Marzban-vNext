# Marzban Next-Version Execution Roadmap — Codex / VS Code

## 0. START CONTRACT — CLEAN SOURCE ONLY

This file and `ROADMAP_FA.md` are the only project execution instructions.

**Do not use any previous clone, workspace, STATE file, ROADMAP, AGENTS instructions, Graphify output, terminal commands, patches, or previously modified source as the baseline.**

Authoritative upstream sources:

- Exact release ZIP: `https://github.com/smorad3363/Marzban/archive/refs/tags/v5.1.0.zip`
- Releases: `https://github.com/smorad3363/Marzban/releases`
- Repository: `https://github.com/smorad3363/Marzban`
- Required tag: `v5.1.0`
- Expected release commit: `c824e822a2f5e41d91b894aabd2a7b9c77a200d2`

### Mandatory bootstrap

1. Create a clean workspace.
2. Download the ZIP above again. Do not reuse a stale local source archive.
3. Verify the GitHub tag/commit against the expected release commit.
4. Extract into a new `Marzban-vNext` directory.
5. Before changing application source, move the release's existing `AGENTS.md` to:
   `docs/legacy/AGENTS.upstream-v5.1.0.md`
   because it contains stale baseline instructions.
6. Do not trust prior generated state/runbooks/Graphify output. Archive or regenerate them if present.
7. Generate a new concise root `AGENTS.md` from this roadmap.
8. Initialize Git from this exact clean source:
   - `git init`
   - base development branch: `vnext-core`
   - `upstream` remote: `https://github.com/smorad3363/Marzban.git`
   - first commit: `baseline: upstream v5.1.0`
   - tag: `baseline-v5.1.0`
9. Never copy modified code from another workspace into this project.

---

# 1. MINIMAL REQUIRED TOOLS AND SKILLS

Use the smallest non-overlapping toolset.

## Required local tools
- Git
- GitHub CLI (`gh`)
- Docker + Docker Compose
- Python 3.12
- Node.js/npm
- ripgrep (`rg`)

## Architecture / token reduction
### Graphify
- Build the architecture graph once at bootstrap.
- Afterwards, use targeted graph queries only for the current phase.
- If Graphify installation is unreliable or unavailable, do not block the project; use `rg`, `git grep`, import search, and focused call-site inspection.
- Never reread the entire repository every phase.

## Current documentation
### Context7
- Use only for version-sensitive library/API documentation.
- If Context7 is unavailable, use official documentation for that library only.
- Record durable version/API decisions once in `docs/CODEX/DECISIONS.md`.

## UI/UX design
### UI/UX Pro Max
- This is the only design skill to install/use.
- Do not install an overlapping second design skill.
- Use it only during the UI phase.

## Final browser verification
### Playwright CLI
- Install/enable only near final verification.
- Do not keep a parallel browser MCP stack loaded throughout development.

## Do not install unless a hard requirement later proves necessary
- Serena
- Taskmaster
- Beads
- Storybook
- Tailwind
- shadcn
- GSAP
- another chart library
- another UI design skill

Keep the current frontend stack:
- React 18
- Chakra UI
- Framer Motion
- ApexCharts

---

# 2. DETERMINISTIC RESUME AFTER INTERRUPTION

Agent memory is not authoritative.

Create:

- `docs/CODEX/STATE.md`
- `docs/CODEX/DECISIONS.md`
- `docs/CODEX/ARCHITECTURE.md`
- `docs/CODEX/CHANGELOG_WORK.md`

Keep `STATE.md` short and current:

- current phase
- phase status
- Git HEAD
- last checkpoint
- files currently being changed
- completed action
- exact next action
- real blocker, if any

### When a session crashes and the user only says "Continue"

Read only, in this order:

1. `AGENTS.md`
2. `docs/CODEX/STATE.md`
3. `git status`
4. last commit
5. unfinished diffs
6. one targeted Graphify/`rg` query for the exact next action

**Do not re-analyze the whole repository, full history, or previous runbooks.**

---

# 3. GIT STRATEGY AND UI-ONLY ROLLBACK

There are two major checkpoints.

## Core checkpoint
All non-visual technical work is performed on:

`vnext-core`

After core is complete:

- create a full core commit;
- tag: `checkpoint-core-complete`;
- write the checkpoint to STATE;
- **do not stop for approval**;
- immediately create:

`vnext-ui`

## UI checkpoint
All White-Label and UI/UX work must exist only on:

`vnext-ui`

This separation is mandatory so the entire UI redesign can be discarded later without losing any core fixes.

Never commit UI redesign changes onto `vnext-core`.

---

# 4. AUTONOMOUS EXECUTION POLICY

After the user says **Start**:

- proceed through all phases without follow-up questions;
- do not pause before UI;
- checkpoint core and immediately continue into UI;
- avoid unrelated refactors;
- avoid open-ended bug hunting;
- do not repeatedly run full test suites during implementation;
- do not rewrite healthy features without a requirement;
- prefer the smallest coherent diff;
- do not re-litigate decisions already locked by this roadmap;
- if an external blocker such as missing GitHub authentication exists, continue development and record only the push blocker in STATE.

---

# PHASE 1 — INSTALLER / CLI / VERSION INTEGRITY

Eliminate mismatch between management CLI, Docker image, and runtime app version.

Requirements:

- no release deployment via `latest`;
- installing `v5.1.0` must run the exact corresponding application image;
- install/update must reliably pull and recreate the application;
- installed CLI must belong to the same release;
- tagged release documentation must never silently install the master installer;
- `set-owner` and `mysql-upgrade` must ship with the same release;
- add:
  `marzban version`

It must report at least:

- CLI version
- runtime app version
- configured Docker image/tag
- running Docker image/tag
- immutable image digest

Installation/update must not report success while these disagree.

---

# PHASE 2 — PINNED MODERN MYSQL + SAFE MIGRATION

New installations must use a modern exact MySQL version.

Default target:

`mysql:26.7.0`

Never use `mysql:latest`.

Implement:

- startup database preflight;
- existing data-version detection;
- prevention of unsupported direct data-directory downgrade;
- pre-migration backup;
- logical dump/restore for incompatible cross-series transitions;
- clean fresh-install path;
- explicit existing-install migration path;
- resumable migration state;
- never overwrite existing database data automatically;
- clear recovery errors.

---

# PHASE 3 — DEPENDENCY COMPATIBILITY

Make only required dependency changes.

- upgrade the APScheduler path that still imports deprecated `pkg_resources`;
- remove the deprecated dependency path;
- remove any `setuptools<81` workaround only after it is truly unnecessary;
- do not perform broad package modernization.

---

# PHASE 4 — SEPARATE PLAN FROM NETWORK ACCESS

## Plan
Commercial entitlement only:

- traffic
- duration
- price
- required commercial constraints

## Access Group
Network access:

- nodes
- inbounds
- hosts
- routing/access settings

Relationship:

`User → Access Group → Nodes / Inbounds / Hosts`

The user separately retains the commercial Plan relationship.

### Propagation

Changes to:

- Host
- Inbound
- Node membership

must propagate to active users in the same Access Group.

Changes to:

- price
- traffic
- duration

must not retroactively rewrite existing user entitlement.

Use Plan Archive instead of destructive deletion to preserve billing/history.

---

# PHASE 5 — CENTRAL ADMIN POLICY + BILLING + USER CREATION

Implement one backend policy source used by:

- Create
- Edit
- Renew
- Quick Renew
- Bulk
- Reset Usage
- direct API

UI reflects backend policy; it does not replace enforcement.

## USED_TRAFFIC

Creation modes:

- Plan Only
- Form Only
- Both

Default:

`Plan Only`

Accounting is based on actual consumed traffic.

Reset Usage must not erase administrator consumption already accounted for.

## ALLOCATED_TRAFFIC

Creation modes:

- Plan Only
- Form Only
- Both

Default:

`Plan Only`

Primary accounting is the administrator Toman wallet.

### Plan
Use the Plan price.

### Form
Use:

`GB × PricePerGB × DurationMultiplier`

Example:

30GB × 1,000 × 1.1 = `33,000 تومان`

Admin cannot reduce user traffic.

Unlimited traffic must not be created through unrestricted manual Form.

## USER_CREDIT

Always:

`Plan Only`

Never show the manual creation Form.

Usage reset must not affect user-count credit.

---

# PHASE 6 — DURATION / PRICING SETTINGS

Admins cannot enter arbitrary duration values.

Default Owner-managed presets:

- 1 day → `0.8`
- 7 days → `0.9`
- 30 days → `1.0`
- 60 days → `1.1`

Manage these presets and multipliers in Owner Settings, not the Admin creation form.

Unlimited duration requires explicit Owner permission.

Format all Toman values for readability:

`200,000 تومان`

not:

`200000`

---

# PHASE 7 — PLAN VISIBILITY + QUICK RENEW

## Owner
Can:

- view Plans
- create Plans
- edit Plans
- archive Plans

## Admin
Must not see a Plan management menu or Plan management page.

Admin may only see compact Plan summaries in:

1. Create User
2. Quick Renew next to a user

Example summary:

`30GB • 30 days • 33,000 تومان`

Quick Renew:

- choose Plan;
- show concise change summary;
- validate billing/policy;
- apply.

---

# PHASE 8 — BULK USER OPERATIONS

Redesign bulk behavior.

- checked users are the actual operation targets;
- remove redundant Admin/All Users re-selection;
- compatible actions may be combined;
- incompatible actions cannot be selected together;
- before execution show:
  - user count
  - traffic change
  - duration change
  - status change
  - cost
- return per-user:
  - Success
  - Failed
  - Reason

Use the exact same policy engine as single-user actions.

Bulk operations must never bypass Create/Edit/Renew restrictions.

---

# PHASE 9 — RELIABLE DEVICE / IP ACTIVITY TRACKING

Do not infer a physical device only from short-lived accepted-log counts.

V1:

- activity primarily based on `last_seen`;
- track User + IP + Node + timestamps;
- node collector heartbeat;
- reliable auto-reconnect;
- replace stale collectors;
- distinguish telemetry loss from client inactivity;
- expose last log/telemetry timestamp per node;
- avoid unexplained activity disappearance after master restart;
- make concurrent device warnings stable and deterministic.

Never treat IP as a guaranteed physical Device ID.

Prepare the data model for future V2:

**Device Token / Device Slot**

Do not implement V2 client-side identity unless it becomes a real requirement.

---

# PHASE 10 — INTEGRATED BACKUP & RESTORE

Merge useful native Marzban backup behavior with useful Marzban-Backup behavior.

Remove unrelated support for:

- Sanaei
- 3x-ui
- Hiddify
- other non-Marzban systems

## Backup content

- logical MySQL dump
- required panel/application files
- configuration
- required certificates/data
- manifest
- app/database version metadata
- timestamp
- checksum

Do not use raw MySQL data-directory copying as the primary DB backup format.

## Destinations

- Local
- Telegram
- Email
- Telegram + Email

### Telegram
Split only when transport size limits require it.

### Email
Send the complete backup as one file; do not split it.
If SMTP attachment limits prevent delivery, report a clear error and preserve the complete local backup.

## Schedule presets

- 15m
- 30m
- 1h
- 3h
- 6h
- 12h
- 24h

Retention is configurable.

## Owner-only Backup Settings

Configure:

- Telegram bot/chat
- SMTP
- From/To
- schedule
- retention
- destinations

## Panel Restore

Owner only.

Flow:

`Upload → Validate → Checksum → Pre-Restore Backup → Maintenance → Restore DB + Files → Migrations → Start → Health Check`

Invalid backup archives must never begin restore.

---

# CORE CHECKPOINT — DO NOT STOP

After Phase 10:

1. update STATE;
2. inspect the diff;
3. commit all core work;
4. create tag:
   `checkpoint-core-complete`
5. preserve `vnext-core`;
6. create branch:
   `vnext-ui`
7. **immediately continue into the UI phases without requesting approval.**

---

# PHASE 11 — COMPLETE WHITE-LABEL

Remove all user-visible references to:

- Marzban
- Heisenberg
- previous fork branding
- previous developer branding
- obsolete GitHub/credit branding

Cover:

- Login
- Dashboard
- page title
- metadata
- Footer
- Logo
- Favicon
- Manifest
- Loading
- Error pages
- Empty states
- About/version UI

Preserve legally required license/copyright notices in source distribution; remove obsolete product branding only from visible UI.

Owner-configurable branding:

- Panel Name
- Logo
- Favicon
- Login Title
- Optional Description

---

# PHASE 12 — PROFESSIONAL UI/UX REDESIGN

Design target:

**Modern Premium Operations Dashboard**

Avoid generic AI-dashboard styling, excessive neon, visual clutter, or animation for its own sake.

Keep the existing stack:

- React
- Chakra UI
- Framer Motion
- ApexCharts

Do not migrate frameworks.

## Unified design system

Before page redesign, normalize:

- color tokens
- spacing
- typography
- radius
- shadow/elevation
- motion timings
- component states
- dark/light behavior
- RTL/LTR rules

## Dashboard priorities

- Online Users
- Active Users
- Traffic
- Wallet/Credit
- Node health
- Node online/offline
- Device warnings
- Backup status
- Important events

Use charts only when they communicate useful operational information.

## Settings

Group into:

- General
- Users
- Admin Policies
- Plans & Pricing
- Access Groups
- Nodes
- Backup & Restore
- Branding
- System

Move rare controls into Advanced/collapsible sections.

## Users

- professional data table
- clear Search/Filter
- organized Bulk actions
- Quick Renew
- clear status
- Device/IP summary
- consolidated action menu

## Admins

Keep creation/edit forms short and understandable.

Do not place duration presets or multipliers in the Admin form.

## Plans for Admins

No Plan management menu/page.

Only compact Plan selection cards/summaries inside:

- Create User
- Quick Renew

## Motion

Use Framer Motion for:

- page transitions
- drawers/modals
- card/KPI entrance
- hover/focus feedback
- useful chart entrance
- success/error feedback

Motion must remain subtle, fast, and functional.

Respect `prefers-reduced-motion`.

## Responsive

Fully support:

- Desktop
- Tablet
- Mobile
- RTL
- LTR

---

# PHASE 13 — ONE CONSOLIDATED FINAL VERIFICATION

Do not repeatedly run the full suite before this phase.

Run one focused integrated final verification covering only critical paths:

1. fresh install
2. version integrity
3. MySQL preflight/migration
4. Owner / set-owner
5. Admin policy
6. Create/Edit/Renew/Quick Renew/Reset
7. Plan + Access Group propagation
8. Bulk operations
9. Device/IP collector reconnect
10. Backup
11. Restore round trip
12. Owner/Admin UI visibility
13. White-label
14. responsive RTL/LTR
15. restart + health status

Use Playwright CLI for browser verification.

No open-ended bug-hunting loop.

If something fails:

`Root cause → Fix → rerun only the failed path and direct dependency`

Write:

`docs/CODEX/FINAL_REPORT.md`

---

# PHASE 14 — AUTOMATIC GITHUB UPLOAD

Run after UI and final verification.

## Remote policy

Always preserve:

`upstream = https://github.com/smorad3363/Marzban.git`

For push:

1. run `gh auth status`;
2. if `TARGET_GITHUB_REPO` is defined, use it as `origin`;
3. otherwise, if GitHub CLI is authenticated:
   - resolve the authenticated username with `gh api user`;
   - look for a private repository named `Marzban-vNext`;
   - if it does not exist, automatically create it as **Private**;
   - set it as `origin`;
4. after UI completion, push both branches:
   - `vnext-core`
   - `vnext-ui`
5. push tags:
   - `baseline-v5.1.0`
   - `checkpoint-core-complete`
   - `checkpoint-ui-complete`
6. if permitted, set the final UI branch as the default branch of the new repository.

If GitHub authentication is unavailable:

- do not stop development;
- finish all local commits/tags;
- record `PUSH_BLOCKED_GITHUB_AUTH` in STATE and FINAL_REPORT;
- never invent credentials or tokens.

---

# UI-ONLY ROLLBACK CONTRACT

If the user later says:

**"Rollback the UI"**

do not revert any core fix.

Rollback baseline:

`checkpoint-core-complete`

or branch:

`vnext-core`

Only changes introduced on `vnext-ui` are disposable.

---

# TOKEN CONTROL

Always retrieve context in this order:

`STATE → targeted Graphify/rg query → relevant files → official docs only if needed`

Never:

`read entire repo → read all history → analyze everything again`

Store locked decisions once in `DECISIONS.md`.

Keep `ARCHITECTURE.md` concise.

Do not dump huge logs into context; extract only relevant segments.

---

# DEFINITION OF DONE

The project is Done only when:

- all phases are complete;
- UI starts automatically after the core checkpoint;
- core and UI are independently rollbackable;
- consolidated final verification is complete;
- `STATE = COMPLETE`;
- Git history is understandable;
- Core and UI checkpoint tags exist;
- when GitHub auth exists, both branches and all checkpoint tags are pushed to GitHub.
