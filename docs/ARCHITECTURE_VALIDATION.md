# Architecture and Exa verification

Verified locally on 22 October 2026, using Python 3.13, Node 24, Chromium and disposable PostgreSQL 16 databases. Production Neon data was not used. Real Exa requests were made only after implementation and the initial regression checks.

## Results

| Check | Result | Evidence |
| --- | --- | --- |
| Full backend suite | 829 passed, 5 opt-in external-service skips, no failures | [Output](validation/architecture/backend-final.log), [JUnit](validation/architecture/backend-final.xml) |
| Final targeted backend checks | 113 passed, including an additional candidate-ranking regression | [Output](validation/architecture/backend-targeted.log), [JUnit](validation/architecture/backend-targeted.xml) |
| Frontend lint | Passed | [Output](validation/architecture/frontend-lint.log) |
| Production frontend build | Passed | [Output](validation/architecture/frontend-build.log) |
| Full browser suite | 26 passed | [Output](validation/architecture/browser-final.log) |
| Final review workflow after summary fix | 1 passed | [Output](validation/architecture/browser-targeted.log) |
| Live Exa Search and Contents | Both succeeded; 10 search results and 1 retrieved datasheet | [Sanitized output](validation/architecture/exa-live.log) |
| Compose configuration | `docker compose config --quiet` exited 0 | Local configuration check |

The full suite and final targeted checks together cover 830 distinct passing backend tests. The targeted run verifies the final summary-default, revision-restore, normalization and candidate-ranking changes. No API credentials are included in these artifacts.

## What the tests establish

- A fabricated voltage cannot pass validation merely by citing a real parameter name. Equivalent SI units produce the same independent verdict. Unknown measurements remain unknown.
- An original accepted failure remains a failure in the report. New acceptance records do not cover changed evidence or another rail.
- Separate declared signal nets do not conflate controllers. Undeclared connections remain unknown. These tests do not validate physical pin wiring.
- Requirement changes produce immutable child revisions. Stale edits are rejected, cross-account reads return 404, restore creates a new revalidated revision, and project deletion removes its revisions.
- The saved graph, report and CSV export identify the same revision and preserve BOM quantities.
- Manually and automatically resumed BOM runs reuse already committed resolution/normalization work, continue event sequencing, and finish with an atomically stored revision. Stage results and Exa request reservations survive new execution contexts.
- Exa tests verify native request contracts, deduplication, request budgets, per-URL failures, missing-key behavior and rejection of unsuitable document URLs.
- The real browser edits a 3.3 V reference design to 5 V, observes a new voltage failure, compares the old revision, checks exports, and reopens the same conflicted workspace without console errors. Analytics uses a separate reference account.

[Review-page screenshot](validation/architecture/revision-report.png) · [Workspace after edit](validation/architecture/revision-workspace.png). These screenshots use an explicitly synthetic test component, not a manufacturer-validated circuit.

## Live Exa check

From `backend`, with `EXA_API_KEY` supplied through the ignored server environment:

```powershell
$env:CONTINUITY_RESEARCH_ENABLED = '1'
$env:CONTINUITY_RESEARCH_MAX_CALLS = '2'
$env:CONTINUITY_RESEARCH_BUDGET_USD = '0.04'
$env:CONTINUITY_FIXTURES = '0'
./.venv/Scripts/python.exe tools/check_research.py
```

The actual application adapter returned:

```text
configured: true
search: 10 documents, no error, reported cost USD 0.007
contents: 1 document, no error, partial false, reported cost USD 0.001
retrieved characters: 24000
verification: unverified
exit code: 0
```

The retrieved source was the [Sensirion SHT4x datasheet](https://sensirion.com/media/documents/33FD6951/6555C40E/Sensirion_Datasheet_SHT4x.pdf). Total reported cost was $0.008. This proves retrieval through the wired adapter; it does not turn retrieved prose into a verified electrical fact. Gemini was disabled during automated tests and was not subjected to a new live accuracy evaluation in this change.

## Reproduce automated checks

Set `CONTINUITY_TEST_DB` and `E2E_DATABASE_URL` to separate disposable PostgreSQL databases. Backend integration tests truncate their tables; browser setup replaces dedicated test accounts. Then run:

```powershell
$env:CONTINUITY_ENV = 'test'
$env:CONTINUITY_LLM_PROVIDER = 'disabled'
$env:CONTINUITY_RESEARCH_ENABLED = '0'
backend/.venv/Scripts/python.exe -m pytest backend/tests
npm.cmd --prefix frontend run lint
npm.cmd --prefix frontend run build
npm.cmd --prefix frontend run test:e2e
```

Configure `E2E_PYTHON` and `PLAYWRIGHT_BROWSERS_PATH` if your installation uses nondefault paths. GitHub Actions also runs the backend, build, lint and browser suites.

## Failures resolved and remaining limits

Initial checks exposed old tests accepting unverified model numbers, outdated checkpoint/migration expectations, a producer/revision persistence race, and missing summary defaults. A shared browser test account also changed the analytics reference corpus; review tests now use a separate account. These failures were fixed and retested. One targeted invocation used a nonexistent test filename; the corrected invocation passed.

The build retains a nonblocking JavaScript chunk-size advisory. LangGraph emits forward-compatibility notices for checkpoint types. Some PowerShell logs label stderr warnings `NativeCommandError` even when the process exits successfully; pass/fail results and exit codes govern the reported outcome.

No measured claim of general AI accuracy, electrical certification, schematic correctness or production load capacity is made. Manufacturer claims and supplier stock/prices can be incomplete or stale. Research documents need review. Thermal/battery assumptions and physical pin/timing/layout work remain visible limitations. Analytics and Memory retain original run observations; the selected revision report describes subsequent edits.

Render deployment and its Exa secret must be configured separately. Local success is not evidence that this revision is already deployed. Recovery only operates while the host is awake, uses one backend worker and can repeat an external call that crashed before its stage committed.
