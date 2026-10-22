# Review workflow and provider verification

Verified locally on 22 October 2026. This report covers the review redesign, unknown handling, revision navigation, planning contract and bounded Groq/Exa use. It does not establish that the manual Render deployment has been updated.

## What changed

- The report presents check counts, component groups, search/filters, actionable evidence gaps and questions about intended operating conditions. The chart is a distribution of checks, not a confidence score.
- Battery runtime/capacity, temperature, current margin and an optional component package limit are requested when absent or unconfirmed. Requirements are independent of the component search; unknown measurements are not filled with convenient defaults.
- An answer, requirement edit, connection edit or restore saves an immutable, revalidated revision and returns to the planning workspace. Its graph, BOM, summary and current check trace use that revision. A failed save keeps the form and answer available. Earlier revisions and the original run log are preserved.
- Current checks no longer suggest zero draw or full headroom when all load currents are missing. Known overloads still fail even when some loads remain unknown.
- Planning preserves battery operation separately from USB charging, rejects missing requested support functions, and uses strict JSON Schema for supported Groq GPT-OSS models. A switch category supports button sourcing. The fallback does not invent digital bus links to discrete controls or put the charger on the logic supply.
- Known supplier roles avoid unnecessary AI classification. Repeated model calls are coalesced within an execution; request/token limits and exhausted-quota handling stop repeated unsuccessful calls.
- Exa retrieval targets missing evidence on the selected candidate, uses an enriched datasheet URL when available, checks exact part identity and retains unverified source labels. Alternative discovery is bounded separately within the shared research allowance.

## Automated checks

Run from the repository root, using disposable local PostgreSQL databases:

```powershell
$env:CONTINUITY_TEST_DB = 'postgresql://USER:PASSWORD@127.0.0.1:55439/engine_tests'
backend/.venv/Scripts/python.exe -m pytest backend/tests -p no:cacheprovider -ra
npm.cmd --prefix frontend run lint
npm.cmd --prefix frontend run build
$env:E2E_DATABASE_URL = 'postgresql://USER:PASSWORD@127.0.0.1:55439/engine_browser'
npm.cmd --prefix frontend run test:e2e
```

Never point the test suites at production Neon: they clear or seed their test databases. Browser tests also need installed Playwright Chromium.

Final results and complete output:

| Check | Result | Output |
| --- | --- | --- |
| Backend, including PostgreSQL integration | 897 passed, 5 optional live-service tests skipped | [Backend log](validation/review/backend.log) |
| Chromium regression suite | 28 passed | [Browser log](validation/review/browser.log) |
| Frontend lint | Passed | [Build and lint log](validation/review/build.log) |
| Production frontend build | Passed; existing bundle-size warning | [Build and lint log](validation/review/build.log) |

Regression coverage includes immutable revisions and ownership, stale-write rejection, actual changed voltage findings in the restored workspace, confirmed requirement persistence, retry after a rejected edit, unknown current calculations, battery capacity and source preservation, requested functions, strict schema, model cache isolation/coalescing, bounded requests/tokens, quota circuit breaking and exact-part document matching. The browser tests check desktop and 390 px mobile layouts, filtering, return navigation, refresh and shared report/BOM revision IDs.

## Live provider checks

### Guided answers and batch confirmation

The subsequent review update adds example goals, **Write your own**, **Not sure yet**, and one **Confirm all & revalidate** action. Filled answers share one validated request and immutable revision; wholly blank questions remain open. Invalid ranges prevent the entire save, duplicate submissions are blocked while saving, and unsuccessful requests retain every draft. Mobile inputs and the bulk action stack vertically to avoid clipped values.

**None of these? Describe a change** previews answers to open questions using the configured model. Output is restricted to five requirement fields, supporting quotes must occur in the request, and the normal requirements validator checks bounds. The user reviews the proposal, adds it to the draft, then confirms the values. This is interpretation of user requirements, not verification of component facts. Unsupported requests remain visible. It does not replace components or edit a circuit through free text. AI downtime leaves direct answers usable. Production preview requests are limited to five per client address per minute, separately from sign-in attempts; direct saves are unaffected.

| Check | Result | Output |
| --- | --- | --- |
| Focused backend/provider/revision/security regressions, disposable PostgreSQL | 64 passed | [Backend log](validation/review/guided-backend.log) |
| Complete Chromium regression suite | 33 passed | [Browser log](validation/review/guided-browser.log) |
| Review suite after mobile layout correction | 8 passed | [Mobile verification](validation/review/guided-mobile.log) |
| Frontend lint and production build | Passed; existing bundle-size warning | [Build log](validation/review/guided-build.log) |
| One capped live Groq preview, no database writes | Correctly interpreted all five supplied answers; 1 request, 1,089 accounted tokens; no Exa calls | [Live output](validation/review/guided-live.log) |

Browser proposal responses are controlled fixtures; revision saves, PostgreSQL persistence, revalidation and workspace restoration use the real application. Backend tests cover ownership, stale revisions, invalid/duplicate/unsupported fields, missing supporting quotes, provider failure and preview throttling. An initial backend test invocation could not access the system pytest temporary directory; rerunning with a workspace-local `--basetemp` passed. Desktop and 390 px mobile screenshots were inspected.

### Earlier planning and retrieval checks

`backend/tools/check_review_providers.py` deliberately makes a small planning/retrieval check, without creating production projects or sourcing a whole board. It caps Groq at three attempts and 24,000 accounted tokens, and Exa at two requests with a $0.04 estimated allowance. It reads secrets from the existing private environment and prints no credentials.

```powershell
backend/.venv/Scripts/python.exe backend/tools/check_review_providers.py
# Retest planning without spending additional Exa calls:
backend/.venv/Scripts/python.exe backend/tools/check_review_providers.py --planning-only
```

- **Final Groq planning:** `openai/gpt-oss-120b` returned a valid bicycle-alarm plan in **one request, 3,509 reported tokens**. It included charger, regulator, MCU, accelerometer, button, LED and buzzer, preserved the **3.7 V battery input**, and left unspecified battery capacity unknown. [Final planning output](validation/review/live-planning.log).
- **Exa:** Search, Contents and repeat-query cache reuse passed using **two requests**, one retrieved document, and **$0.008 in provider-reported cost**. That is a reported request cost, not a claim about the account's billing plan. [Combined initial check](validation/review/live-initial.log).
- **Supplier lookup:** one bounded live `tactile switch` lookup returned two exact switch candidates. This checked the new search vocabulary; it did not validate their electrical suitability for a bicycle alarm.

The initial compact planning prompt failed structural checks while Exa succeeded. A complete JSON example improved structure, but subsequent inspection caught omitted support functions and invalid tier labels. The final contract, function checks and strict schema addressed those observed failures. The last planning-only log predates the smoke script's display-only correction: its `research_pass: false` means research was skipped (`exa_requests: 0`), not that a new Exa request failed. The script now emits `null` for skipped checks.

These are smoke checks, not a benchmark establishing optimal prompts across all hardware briefs. Provider availability and free-tier quotas may change independently.

## Visual workflow and recordings

The README retains its introduction → design → evidence → refinement → return-to-work sequence. The review and research clips were re-recorded against the production frontend and disposable PostgreSQL database. They exercise actual save, revalidation, return to workspace, history/restore, exports and connection editing. A previously saved Exa response was reused for recording, so visual retakes made no additional research calls. See [recording details and checks](media/README.md#review-feature-verification).

Desktop/mobile screenshots and sampled video chapters were inspected. Initial recording attempts caught an ambiguous bus-check selector and an outdated expected sentence; the assertions were corrected before publishing the final clips. An earlier full browser run had one Vite development-server `ERR_CONNECTION_REFUSED` failure; the subsequent complete runs passed. An earlier pytest run also reported an unwritable optional cache directory; the final command disables that cache.

## Remaining limits

This engine checks the modeled concept and its available evidence. It does not verify physical signal pins, timing or PCB layout. A battery concept still needs separate discharge/charging/load-sharing analysis and sleep/alarm duty cycles; those are the next substantial architecture improvements. Research text does not automatically become a verified numerical rule input. Saving requirements revalidates selected components; it does not automatically replace them or launch a fresh AI plan. Missing evidence stays visible.

The production build retains a non-failing bundle-size warning. Browser checks also surface existing LangGraph serializer warnings about explicitly allowing custom checkpoint types in a future dependency version. These warnings did not fail the current run.

No new database migration is required for this update. Keep the current Render Groq/Exa keys and Neon URL, then manually deploy the new Git commit. Optional application request/token limits are documented in [deployment configuration](DEPLOYMENT.md#switch-the-existing-render-service-to-groq).
