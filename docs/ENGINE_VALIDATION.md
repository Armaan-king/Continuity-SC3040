# Rule-engine verification

Verified locally on 22 October 2026 using Python 3.13, Node, Chromium and disposable PostgreSQL 17 databases. The ruleset is `2026-10-engine-2`. Production Neon data and live model/research credentials were not used.

## Results

| Check | Result | Evidence |
| --- | --- | --- |
| Full backend suite, including PostgreSQL integration | 873 passed, 5 optional external-service skips | [Output](validation/engine/backend.log), [JUnit](validation/engine/backend.xml) |
| New engine regression cases, included above | 40 passed | `backend/tests/test_engine_hardening.py` |
| Full Chromium suite | 26 passed | [Output](validation/engine/browser.log) |
| Frontend lint | Passed | [Output](validation/engine/frontend-lint.log) |
| Production frontend build | Passed | [Output](validation/engine/frontend-build.log) |

The final command summaries were:

```text
873 passed, 5 skipped, 1 warning in 53.26s
26 passed (49.0s)
vite v8.2.0 building client environment for production...
1103 modules transformed.
built in 1.12s
```

## What changed and what the tests establish

- Invalid or contradictory numerical facts become explicit UNKNOWN findings. Evaluation excludes them without altering the source snapshot. Invalid operating requirements cannot silently pass or crash arithmetic.
- Cascaded supply calculations retain known loads when another downstream load is unknown. All outputs of a source contribute to upstream demand. Invalid cycles, ambiguous supplies and duplicate connections are reported.
- A guessed converter efficiency cannot alone prove an input-current failure. An overload still fails when even ideal conversion exceeds the limit. Typical-only currents propagate uncertainty upstream.
- Buck/boost direction is checked against the declared rails. Unknown thermal load produces a finding rather than disappearing from the report.
- Explicit signal nets validate controller roles and protocol support. Independent I2C nets each require two GPIO; one shared bus requires two. Multiple controllers without assignments remain unchecked.
- BOM quantities survive round trips through saved revisions, contribute once to current demand, and appear in the calculation evidence. Older repeated-member BOM documents remain readable without double counting.
- Deterministic repair constraints use the actual rail, current demand plus margin, and both temperature limits. Candidate comparison considers the whole board. A repeated search cannot place the same already-tried component again.
- In the controlled demo, the current repair selects the buck directly after comparing candidates. It avoids the old intermediate LDO thermal failure, reducing that fixture from three repair steps to two. This is a regression result for this fixture, not an accuracy benchmark across arbitrary designs.
- Browser checks cover authentication, projects, naming, clarification retries, saved boards, reports, requirement edits, immutable revisions, export, analytics and walkthrough compatibility.

## Reproduce

Set `FUZE_TEST_DB` and `E2E_DATABASE_URL` to **separate disposable local databases**. Backend integration tests truncate tables; browser setup seeds dedicated accounts. From the project root:

```powershell
$env:FUZE_LIVE = '0'
$env:FUZE_RESEARCH_ENABLED = '0'
$testScratch = Join-Path (Get-Location) ('.local/pytest-engine-' + [guid]::NewGuid().ToString('N'))
backend/.venv/Scripts/python.exe -m pytest backend/tests --basetemp $testScratch
npm.cmd --prefix frontend run lint
npm.cmd --prefix frontend run build
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path (Get-Location) '.local/playwright'
npm.cmd --prefix frontend run test:e2e
```

The browser path above uses this workspace's existing Chromium installation. On a fresh machine, install Playwright Chromium into that path first, or use Playwright's default installation path instead.

## Failures corrected and remaining limits

The initial regression pass exposed a conflict between duplicate-connection validation and the older BOM quantity encoding. Quantities are now explicit, with compatibility handling for old documents. It also exposed repeated repair candidates and narration occurring after candidate normalization; both were corrected. Demo assertions were updated to reflect the shorter repair path. A topology fixture had a typical current larger than its declared peak and was corrected to represent its intended load.

The first browser invocation could not locate Chromium in the default user cache. The final invocation used the installed workspace browser and all 26 tests passed. The backend retains a nonblocking pytest-cache permission warning. The frontend retains its bundle-size advisory; browser logs retain LangGraph checkpoint compatibility notices and terminal-color warnings.

The UI layout and AI provider configuration are unchanged. Exa retrieves evidence and candidates; Gemini handles model-backed planning, interpretation and proposals; deterministic checks decide electrical findings. This change does not resolve provider HTTP 429/503 responses or improve the breadth of the basic planner. No new live provider test was performed.

These are constraint checks against available facts, not a schematic simulator or hardware certification. Pin-level wiring, timing, I2C address conflicts, package layout, converter quiescent currents, detailed operating profiles and per-output ratings of multi-output regulators are not fully modeled. A published rating is still a source claim, and an UNKNOWN result still needs review.

No Render environment changes or Neon SQL migrations are required for this upgrade. Deploy the new code manually on Render. Existing reports retain their original ruleset until a new run or a revision edit/restore revalidates the design.
