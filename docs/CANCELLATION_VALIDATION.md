# Confirmed run cancellation

Verified locally on 22 October 2026. No Groq or Exa requests were made, and production Neon was not used.

## Defects and behavior

Previously, the UI immediately became idle after Stop while its cancellation request ran in the background. HTTP errors and network failures were discarded. Stopping before the initial response also had no thread ID to cancel. A second task cancellation could interrupt server cleanup and leave the account's local admission slot occupied. Recovery could claim a previously selected input without rechecking its cancelled flag.

The corrected flow:

1. Display **Stopping…** and disable repeated clicks while awaiting confirmation.
2. If necessary, wait for the creation response to identify the run, instead of aborting an unidentified server job.
3. Persist cancellation and revoke the execution lease under the admission account lock. Preserve already completed results.
4. Await local worker cleanup; repeated requests join the same cleanup. Disconnecting a cancellation request does not cancel that cleanup again.
5. Display **Stopped** only after success. Failed requests show **Retry Stop**, retaining the run ID.

Paused runs can also be stopped. Recovery checks cancellation when claiming work, including inputs selected before Stop. Explicit user continuation may clear the flag. New starts in the same browser are rejected while cancellation is still pending. As before, leaving a page alone disconnects its subscription without cancelling the server job.

## Verification

- Full backend suite against disposable PostgreSQL: **1,030 passed, 5 optional live-service skips** in 58.18 seconds. [Output](validation/cancellation/backend.log).
- Final browser run: **11 passed** in 27.7 seconds, covering four cancellation scenarios and seven existing navigation/auth/export/walkthrough scenarios. [Output](validation/cancellation/browser.log).
- The five existing consistency browser tests also passed in the earlier targeted run.
- TypeScript and production build passed; frontend lint passed. The existing nonfatal bundle-size advisory remains.

The cancellation tests cover delayed acknowledgement, a rejected stop and retry, stopping before creation headers arrive, and a real local API/database flow that stops one project and starts another. Server regressions cover duplicate Stop requests during cleanup, a disconnected cancellation request, producer-close failure, account isolation, revoked worker writes, recovery rejection, explicit continuation, and preserving completed runs.

Initial verification caught an asynchronous mock-emitter type mismatch and a test double that did not reproduce request-ID deduplication. Both were corrected. The first browser invocation looked in the default browser-cache location; subsequent runs used the existing workspace browser installation. No dependencies or browsers were installed.

## Deployment and limits

Manually deploy the new Git commit on Render, then refresh the website. No environment-variable changes or database migration are required. If a previously unconfirmed stop left a run active, reopen that run, press Stop, and wait for Stopped before starting the next design.

Supported hosting remains one Uvicorn worker. A lease revoked on another instance is noticed on that worker's next renewal; this change does not introduce distributed instant cancellation. Cancellation also cannot undo provider work already accepted or billed before Stop.
