# Normal web sourcing and Exa activity verification

Validated locally on October 22, 2026. The existing workspace layout is unchanged.

## What changed

Normal sourcing now runs JLCPCB and Exa discovery independently and merges their
shortlists. Exa runs even when the supplier returns six candidates. External products
do not need an LCSC identifier; their stock and price remain unknown unless supplied
by a matching catalogue record. Candidate comparison retains both sources when
eligible candidates exist. A pinned requested function also gets an initial comparison
instead of taking the first hit. Repairs and user-selected trials reuse the same adapter.

Exa discovery requests up to four pages and two identified products. Full text is
requested because downstream checks need context beyond snippets, capped at 24,000
characters per document. This normally saves a second Contents request. Exact
identifiers and source quotations establish a candidate lead; only the existing
source/identity/passage/parser checks can admit electrical facts. Manufacturer authority
remains explicitly checked, and unsupported publishers stay reviewable leads.

Exa receipts reach application logs, the design SSE timeline, saved events, and
runtime status. They distinguish successful HTTP responses from admitted products,
partial/empty retrieval, cache reuse, errors, cancellation and budget skips. Keys,
queries, retrieved text and raw provider error bodies are excluded from receipts.
Counters describe the current process/configuration, not account billing history.

The existing six-request/$0.12 estimated research allowance remains shared across
discovery and evidence. Parallel identical requests share a response; database-backed
reservations survive restarts. Reported costs above the reservation reduce the remaining
local allowance. Completed source extraction is reused during reranking.

## Automated checks

The complete suite ran against the disposable local PostgreSQL database, not Neon.
Provider HTTP responses were simulated in the automated suite.

```text
python -m pytest --basetemp=../.local/pytest-multi-source-db-1 -o addopts='--strict-markers' -q
1123 passed, 5 skipped in 34.48s

npm run build
tsc -b && vite build
1108 modules transformed
built in 1.13s
```

The five skips are opt-in external-service tests. The build retains its existing
nonfatal large-bundle warning. No UI styling changed, so browser layout tests were
not repeated. The earlier database-free run passed 1,020 tests with 106 skips;
later tests added coverage for the live-response parsing case and persistent caching.

New coverage includes a full catalogue alongside independent web products, either
provider failing, deduplication without losing evidence, external-only normalization,
source quotation rejection, typographic quotation wrappers, missing full text,
pinned-function comparison, checkpoint serialization, recovery offers, parallel
deduplication, durable allowances, real SSE receipts, HTTP failures, partial/empty
results, cancellation, changed credentials, and redaction.

Two early SSE tests failed because the test transport also intercepted the local ASGI
client. The fixture now preserves explicitly supplied transports. The application
pipeline was then exercised successfully by those tests.

Full output: [multi-source-tests.log](validation/multi-source-tests.log).

## Bounded live check and its limits

Two Exa searches succeeded with HTTP 200, four source documents each, and reported
cost of **$0.007 each ($0.014 total)**. The first attempt admitted no candidates. A
second captured response exposed the problem: Exa wrapped source quotations in curly
quotes and cited feature text whose model identifier appeared elsewhere on the same
page. Discovery had unnecessarily required the identifier inside the quoted feature.

The corrected parser was tested against that captured public response without another
Exa request. It admitted **EDMS105N** and **MAX28200** as external product leads.
One live Groq request using `openai/gpt-oss-120b` then completed source extraction for
MAX28200: **HTTP 200, 3,008 reported tokens**. No production database or user project
was accessed or changed. Groq monetary cost was not returned by the app.

That extraction admitted **zero electrical fields** under the source checks. Stock,
price and interfaces remained unknown; this result is not evidence of a compatible
controller or a finished circuit. The original smoke assertion treated zero admitted
fields as failure; the reusable check now tests provider wiring separately from fact
admission. The successful response and the unresolved evidence are both retained in
the [live-check output](validation/multi-source-live.log). Positive and rejected fact
admission are covered separately by automated tests.

The reusable opt-in command is `python tools/check_sourcing.py` from `backend`.
It permits at most one Exa search and one model request, performs live supplier lookup,
and makes no database writes. It consumes provider quota. A successful exit verifies
retrieval/candidate/model wiring, not hardware correctness or guaranteed future service
availability. No additional paid full-board run was performed.

## Deployment

Manually deploy the new commit on Render. Keep the existing `EXA_API_KEY` and Groq
settings, leave research enabled, and keep fixture mode off. No new environment
variables or Neon migration are required. Confirm `research.sourcing` is
`web_and_catalogue` at `/api/runtime`; then new runs show source comparisons and Exa
receipts. Old runs retain their historical selections and events. See
[deployment instructions](DEPLOYMENT.md).
