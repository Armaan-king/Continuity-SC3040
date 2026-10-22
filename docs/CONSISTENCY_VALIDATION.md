# Naming and pipeline consistency — 22 October 2026

The existing layout, navigation, panels, colors, and interaction pattern are retained. This update corrects the data and state shown within them.

## Changes

- Project names describe the brief's subject instead of copying its first six words. Naming runs in the backend as part of thread creation, requires no additional AI call, and keeps the complete original prompt. Manual renames are preserved. The workspace replaces its hard-coded board label with the saved project name.
- Migration 003 adds `projects.name_source`. Its one-time conversion repairs untitled projects and conversational titles exactly matching the legacy prefix algorithm. This is deliberately conservative: unrelated custom names remain unchanged. Legacy data did not record name provenance, so matching old prefix titles are inferred; subsequent manual edits are explicitly recorded.
- Final graph snapshots preserve parts, edges, and unresolved conflicts through completion and reopening. Uploaded BOMs now restore their graph even though that pipeline has no LangGraph checkpoint. Older event recordings also remain readable.
- Completed runs distinguish failed checks, warnings, unchecked facts, and missing parts. The project list marks these runs as needing review. Zero conflicts no longer displays a flashing error indicator.
- Clarification questions remain retryable after a failed submission, and repeated errors render without duplicate React keys. The run manager publishes terminal events after persistence and lease release, preventing an immediate answer from racing the previous execution's cleanup.
- A replaced component immediately updates its BOM row and CSV export, retaining the saved quantity. Changed checks for another slot replace earlier warnings or stale rail values; completion does not duplicate evaluations in analytics.

## Verification

| Check | Result | Log |
| --- | --- | --- |
| Backend, including PostgreSQL integration | **796 passed, 5 skipped**, 53.37 seconds | [Output](validation/consistency/backend-final.log), [JUnit](validation/consistency/backend-final.xml) |
| Chromium, real local API and PostgreSQL | **25 passed**, 30.0 seconds | [Output](validation/consistency/browser-final.log) |
| Frontend lint | **Passed**, no warnings | [Output](validation/consistency/frontend-lint.log) |
| TypeScript and production build | **Passed** | [Output](validation/consistency/frontend-build.log) |

The five skips are optional external-service tests. The suite uses deterministic parts/model fixtures. New browser coverage uses the real backend for naming, manual-name preservation, and question restoration; controlled event streams exercise repeated submission failures, unresolved final conflicts, and replacement/export behavior. Backend integration coverage validates uploaded BOM graph restoration and the final snapshot against saved data. No Gemini key or production database was used by these tests.

The final workspace screenshot was inspected to verify the existing three-panel layout, project label, conflict display, and review summary. It is a controlled regression scenario, not a manufactured board or a fresh supplier result.

## Issues encountered during verification

- PowerShell blocked the `npm.ps1` shim; verification used `npm.cmd` without changing system execution policy.
- The first compile caught an unused brief-screen prop after naming moved to the backend. The prop was removed; final lint/build passed.
- The initial backend pass reported an unwritable pre-existing pytest cache. The final run used a new workspace-local cache and completed without warnings.
- Vite still reports its existing main-bundle size advisory (approximately 645 kB before compression). Browser startup logs also report LangGraph's warning about future strict checkpoint deserialization. Neither caused a test failure; the locked dependencies were unchanged.

## Deployment

In the existing Render service, choose **Manual Deploy → Deploy latest commit** after this update is pushed. Startup applies migration 003 to the configured PostgreSQL database. No new environment variables or Gemini settings are required. The hosted deployment itself is separate from these local checks.

New briefs receive the new titles immediately. Existing matching conversational titles are repaired once during migration; other existing names remain available through the current Rename project action.
