# Provider diagnostics and cancellation follow-up

Verified locally on 22 October 2026. Production Neon was not accessed. One bounded Groq request was made; no Exa requests were made.

## What the production logs established

The planner reported `groq: provider returned HTTP 400`, followed by candidate-classification failures. The old client discarded the provider's explanatory error fields, so those logs cannot establish the precise rejection. The provider label describes configuration, not a verified destination. The maintainer confirmed Render uses `https://api.groq.com/openai/v1` and an output limit of `4096`; both match the successful local check. Public runtime reported `openai/gpt-oss-120b`.

Cancellation also failed with a ContextVar token created in a different context, followed by attempts to persist with a revoked execution lease. That was a separate, reproducible application bug missed by the earlier tests.

## Changes

- Close the event producer in the task that iterates it, including when cancellation lands while saving a yielded event. Its task-local tokens can then be reset correctly.
- Represent revoked leases with a dedicated exception. Expected revocation stops writes and releases admission without logging a validation failure or overwriting cancellation. Unrelated persistence and cleanup failures still produce diagnostics.
- Translate known provider errors into fixed, safe explanations. Never copy raw error messages, generated text or credentials into the trace. Repeated pipeline calls after a nonretryable provider error use the execution's cached failure instead of additional HTTP requests.
- Display planning fallback as an amber warning with the safe reason. Historical generic fallback messages also use the warning icon.
- Align the planner's example with its required JSON schema: every slot includes `topology`, with `null` for nonregulators. This removes a prompt contradiction; it is not proof of the original production 400's cause.
- Use word boundaries in basic component recognition. `maximum` no longer requests an IMU; an operating-temperature requirement alone no longer requests a temperature sensor. Ambient-light sensing is now recognized by the fallback.

## Verification

The final full backend suite passed **1,045 tests**, with **5 optional live-service skips**, in 34.78 seconds. [Backend output](validation/provider-cancellation/backend.log). The suite includes disposable PostgreSQL integration tests; paid external-service tests remain disabled.

The [browser output](validation/provider-cancellation/browser.log) records **15 passing Chromium tests** in 29.3 seconds. They cover cancellation acknowledgement, retry, immediate cancellation, a real API/database stop followed by a new project, authentication, saved designs, export, navigation, stream reconnection and the amber provider warning. Model calls and Exa are disabled for browser tests.

TypeScript and the production build passed, and frontend lint passed. The existing nonfatal large-bundle advisory remains.

The new server regressions exercise both brief and BOM producers while their consumer is blocked saving a yielded frame, revocation during a BOM save, and revocation before local Stop reaches the worker. They check cleanup, admission release, absence of misleading error logs, and protection against stale writes. A first version of one test double did not fence `save_progress` like the real store; the test double was corrected before final verification.

## Bounded live planner check

Run from the repository root with the backend virtual environment:

```sh
python backend/tools/check_planner.py
```

This is opt-in and consumes provider quota. It reads the ignored backend environment configuration, makes at most one model HTTP attempt, caps output at 4,096 tokens, and limits the execution allowance to 10,000 tokens. It performs no component sourcing, Exa retrieval or database writes. It does not alter the stored key or environment file.

The [recorded live result](validation/provider-cancellation/planner.log) is:

```json
{
  "passed": true,
  "provider": "groq",
  "model": "openai/gpt-oss-120b",
  "http_attempts": 1,
  "accounted_tokens": 3273,
  "failure": null
}
```

The returned plan contained charging, regulation, a low-power controller, an ambient-light sensor, an OLED, a button and an LED. It passed the local structural checks. This checks the request and planning contract only; it does not establish battery power-path suitability, protection circuitry, selected component compatibility or hardware safety. No full live sourcing run was made.

## Deployment and remaining limits

Manually deploy the new Git commit on Render. The confirmed base URL and token limit need no change, and Neon needs no migration. Local success is not proof of production success; the new failure explanation remains available if Render encounters another rejection. The exact cause of the earlier HTTP 400 remains unconfirmed because its detailed response was discarded.

The supported deployment still uses one backend worker. Stop cannot reverse provider requests already accepted or billed. The fallback remains deliberately limited and labelled for review.
