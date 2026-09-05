---
title: Astra UI Review
date: 2026-09-05
status: complete
tags:
  - marzban-vnext
  - ui-review
---

# Focused UI review

## Scope and baseline

Reviewed the completed `vnext-ui` implementation starting at `769e934121f697f7cd1bf9c9eed985fe57ef9b1f`, after `checkpoint-core-reviewed` (`30fdb79aed138b5c8eb7057814bee945a035ecf0`). Read the requested project notes and UI delta first, then followed targeted component and integration searches. No backend, database, dependency, framework, or product-wide redesign changes. React, Chakra UI, Framer Motion, and ApexCharts remain in place.

The installed UI/UX quality checklist guided interaction, accessibility, and responsive checks; its landing-page suggestions were not adopted for this operational dashboard. Playwright CLI was used for rendered verification. Durable notes use Obsidian-compatible Markdown.

## Confirmed issues and limited fixes

| Root cause | Smallest scoped correction | Verification |
| --- | --- | --- |
| Settings section links replaced the fragment consumed by `createHashRouter`. | Scroll and focus the section without changing the route. | Desktop and mobile Backup/Branding/Access Group section navigation retained `#/settings/`; focus reached the selected section. |
| UI still promised online restore, pre-restore backup, and maintenance despite Core intentionally rejecting online restore. | Remove execution control and endpoint call; retain archive validation with explicit offline-only guidance and pending state. | Local fixture upload/validation; no restore action; no production database accessed. |
| Backup delivery UI omitted SMTP authentication/TLS controls; background query updates could replace drafts. | Expose existing credential/TLS fields, retain blank-secret semantics, and initialize drafts without overwriting subsequent edits. | Email settings submitted; focus/refetch preserved an unsaved branding draft. Real email/Telegram delivery was not exercised. |
| Branding upload labels were not keyboard-operable buttons; settings queries could silently leave blank sections. | Native labeled file controls, upload busy state, loading placeholders and retryable query errors. | Rendered file controls and settings inspected; upload file transport itself was not sent to a real backend. |
| Plan-only Admin creation navigated to Owner-only Plan management. Same bug existed in toolbar, empty state, and quick action. New Core `FORM_ONLY`/`BOTH` modes were not recognized. | Reuse one compact scoped Plan creation dialog at all three entry points, with optional Access Group and price summary; recognize Core modes; exclude `USER_CREDIT` from Form creation/edit-limit controls. Keep Owner management routes guarded. | Admin mobile Plan creation sent Plan and Access Group IDs; quick and empty entry points opened the same dialog; Owner route redirected Admin back to dashboard. |
| Bulk preview remained valid while selections/amounts changed; requests generated fresh IDs on retry and could be reapplied after success. | Bind preview to serialized selected IDs/actions; retain execution identity; freeze the submitted payload; disable completed execution. | Changing amount disabled Apply until refreshed preview. Induced connection loss produced two identical requests with the same operation ID and selected IDs; successful result disabled Apply. |
| Quick Renew generated a new idempotency key on each mutation and allowed dismissal/selection changes while pending. | Retain request identity, lock pending/ambiguous request controls, and show Plan price before confirmation. | Owner/Admin renewal exercised; captured request contains stable-key field. Cross-retry key retention additionally checked by the focused source contract. |
| Router treated service outages as login failures, clearing a valid session. | Recoverable route error with Retry; only unauthenticated cases use Login. | Injected `/admin` HTTP 503 retained token; Retry returned to Settings; no application exception. |
| Light surfaces reused pale dark-theme text; mobile menu icon inherited an unsuitable foreground. | Theme-aware secondary text and readable menu icon with 44px target. | Actual 390px light screenshots inspected; no horizontal overflow in RTL/LTR direction stress. |
| Select theme horizontal padding and direction-dependent icon positioning allowed text/arrow overlap. | Reserve logical end padding and use logical icon inset. | Targeted RTL/LTR visual follow-up after theme change. |
| CSS reduced-motion rules did not cover Framer layout/transform animation. | Add `MotionConfig reducedMotion="user"`; keep existing CSS reduction and nonanimated ApexCharts. | Browser reduced-motion emulation and source inspection; no chart/motion library replacement. |

## Focused verification record

One bounded Playwright review matrix, followed only by checks of affected fixes. Local Vite and a disposable loopback fixture API (`127.0.0.1:3000` / `127.0.0.1:3001`) were used. Early fixture setup errors were tooling issues, corrected before relevant assertions. No production server, database, real user, payment, backup, or external delivery target was used.

- Desktop: 1440 × 1000; mobile: 390 × 844.
- RTL Persian layout and forced LTR direction stress; light/dark surfaces; reduced motion.
- Login branding/form rendering, dashboard metrics/charts, Owner/Admin navigation and Owner-route rejection.
- Owner Form dialog focus containment/Escape; scoped Plan creation with Access Group; Quick Renew; checked-user Bulk Actions and reconnect identity.
- Settings section navigation, credentials/settings submission, mock backup creation/archive validation, branding name/title update, Access Group summary.
- Owner Plans page, Admins empty state, Nodes empty dialog; existing tables/cards/forms inspected in those views.
- Transient route failure/recovery, retained session and draft; final captured recovery state reports `errors: []`; final browser error console returned zero error messages.
- Final build/TypeScript passed. Plan inbound selection: `14 assertions passed`; Admin hierarchy authorization: `PASS`; Admin UX contract: passed. A callback return-type error was corrected before successful compilation. The old UX source contract incorrectly required the broken Owner-only creation route; it was updated and its affected check passed. Subsequent build regeneration covered only the final Select/entry-point fixes; no unrelated suite was repeated.
- Final Select geometry: `32px` end padding; icon at `x=41` in RTL and `x=325` in LTR within the same `x=33..357` control. Rendered follow-up passed. Admin quick-action and empty-state dialog follow-ups passed.

The bulk failure fixture closed a connection; browser/network recovery replayed before the expected failure toast, so that toast assertion timed out. Captured requests prove identical payload and identity; the rendered successful result and disabled Apply control were inspected separately. This is not claimed as a manually clicked failed-request retry test. Similarly, a direct-route assertion initially ran before navigation settled; the final URL confirmed the guard. No full matrix rerun was performed for these harness timing assumptions.

Local, ignored evidence lives in `output/playwright/`: `review-owner-desktop.png`, `review-mobile-light-rtl.png`, `review-mobile-light-ltr.png`, `review-settings-desktop.png`, `review-mobile-settings-final.png`, `review-admin-mobile-create.png`, `review-nodes-empty.png`, `review-admins-empty.png`, `review-plans.png`, fixture scripts, and `review-build.log`. These are disposable evidence, not application dependencies or real backups.

## Explicit limits and remaining product gaps

- This is frontend fixture integration, not live end-to-end backend verification. Core evidence remains in [[ASTRA_CORE_REVIEW]]; it was not rerun.
- Access Group create/edit/archive remains Owner API-only. This review adds discoverable summaries and group selection to scoped user creation, not a new group-management application.
- The existing product is Persian-first and pins `fa` in i18n. LTR checks stress layout direction; they do **not** establish a complete English translation or language-switching workflow. Existing mixed-language labels remain; a translation rollout would exceed this focused repair.
- Backup validation used a deliberately fake archive against a fake validator; it proves UI request/state behavior, not archive integrity. Actual restore is offline-only and was not executed.
- No screen-reader certification, exhaustive contrast audit, device laboratory, long-list performance benchmark, real collector reconnect, or email/Telegram delivery test is claimed. Existing charts retain labeled numeric summaries and disabled animation; no speculative chart rewrite.
- Vite retains existing large-chunk and ignored `use client` directive warnings. No dependency churn or broad code-splitting refactor was introduced.

## Handoff

Review changes belong only to `vnext-ui`. `checkpoint-core-reviewed` and `vnext-core` remain unchanged. Completion/tag/publication state is recorded in [[STATE]]. Preserve this report and resume only its explicitly unfinished verification/publication steps after interruption.
