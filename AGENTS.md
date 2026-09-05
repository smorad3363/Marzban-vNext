# Marzban vNext execution

`ROADMAP_CODEX_EN.md` is the primary execution contract. `ROADMAP_FA.md` is the
product reference. Preserve both files.

## Resume order

1. Read `docs/CODEX/STATE.md`.
2. Inspect `git status --short --branch` and the last commit.
3. Inspect unfinished diffs.
4. Use one targeted Graphify query or `rg` search for the exact next action.

Do not re-analyze the full repository on resume.

## Git contract

- Upstream: `https://github.com/smorad3363/Marzban.git`
- Baseline: tag `v5.1.0`, commit `c824e822a2f5e41d91b894aabd2a7b9c77a200d2`
- Core branch: `vnext-core`
- Core tag: `checkpoint-core-complete`
- UI-only branch: `vnext-ui`
- UI tag: `checkpoint-ui-complete`
- Never place UI redesign work on `vnext-core`.

## Work rules

- Complete roadmap phases in order without routine approval pauses.
- Keep changes focused; avoid broad refactors and dependency upgrades.
- Enforce policy in backend; UI only reflects backend policy.
- Treat MySQL migration, billing, backup, restore, and access propagation as
  correctness-critical.
- Update `docs/CODEX/STATE.md` after every meaningful completed action.
- Record durable decisions in `docs/CODEX/DECISIONS.md` once.
- Run only focused checks during implementation and one consolidated final
  verification in Phase 13.
- Preserve license and source copyright notices during white-label work.

## Database safety

- Start with read-only diagnostics when live database access exists.
- Use committed, resumable migrations. Never overwrite existing database data.
- Require logical backup before incompatible MySQL cross-series migration.
- Do not run destructive production database operations without explicit approval.

## Completion

Done requires completed Core and UI branches/tags, `STATE = COMPLETE`, focused final
verification, and GitHub push when authentication is available.
