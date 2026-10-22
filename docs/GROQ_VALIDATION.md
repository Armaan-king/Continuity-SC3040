# Groq configuration and landing-page verification

Verified locally on 22 October 2026. The configured provider is `groq`, the model is `openai/gpt-oss-120b`, and the endpoint is `https://api.groq.com/openai/v1`. The credential is stored only in the ignored local backend environment file; it is absent from source, example configuration and logs.

## Changes

- Updated the Groq default from the older Llama model to GPT-OSS 120B, which Groq lists on its Free Plan.
- Groq requests use JSON output, `max_completion_tokens`, and supported reasoning effort values. GPT-OSS uses low effort by default; other Groq models do not receive an unsupported reasoning option.
- A dedicated provider key takes precedence over a leftover generic key. Explicit endpoint/model overrides remain available.
- Short numeric `Retry-After` values are respected for the one bounded retry. Waits above 30 seconds use the fallback. Error messages distinguish quota, availability, authentication and model/endpoint failures without echoing provider response bodies.
- Docker Compose forwards the Groq key and token budget. Render's Blueprint selects Groq and requests the secret separately. Existing services need their environment settings updated and a manual deployment; see [deployment instructions](DEPLOYMENT.md#switch-the-existing-render-service-to-groq).
- Removed the complete hackathon YouTube section, its video ID and its unused placeholder from the landing page. The interactive graph and in-app guided walkthrough remain.

## Results

| Check | Result | Evidence |
| --- | --- | --- |
| Provider, normalization and replan tests | 98 passed | [Output](validation/groq/backend-targeted.log) |
| Planner tests | 52 passed | [Output](validation/groq/planner.log) |
| Live Groq measurement extraction | PASS: 1.8 V minimum and 3.6 V maximum | [Output](validation/groq/live-ai.log) |
| Live Groq board planning | PASS: regulator, MCU, sensor, display; USB 5 V supply | [Output](validation/groq/live-ai.log) |
| Local API runtime route | HTTP 200, configured Groq / GPT-OSS 120B; credential absent | [Output](validation/groq/runtime.log) |
| Landing-page Chromium test | 1 passed, no collected console errors | [Output](validation/groq/landing-browser.log) |
| Frontend lint and production build | Passed | [Lint](validation/groq/frontend-lint.log), [Build](validation/groq/frontend-build.log) |
| Docker Compose configuration | `docker compose config --quiet` exited 0 | Local configuration validation |

The two live AI smoke cases completed together in 4.44 seconds through the application's actual adapter and planner. They ran after implementation and offline regression checks. They did not query Exa, source distributor parts, or access production Neon data. Browser verification used the existing disposable local PostgreSQL database with AI and research disabled.

The new request tests cover provider endpoint/key selection, model overrides, JSON mode, token limits, reasoning compatibility, cooldown timing, long quota waits and secret-safe error messages. No full database suite was rerun for this provider and landing-page change; the earlier [engine verification](ENGINE_VALIDATION.md) records that broader run.

One initial targeted test invocation referenced a nonexistent `test_plan.py` and ran no tests; the corrected invocations above passed. The frontend retains a nonblocking bundle-size advisory, and browser logs contain terminal-color warnings. No unresolved test failures remain in these checks.

## Billing and deployment limits

The model is listed on Groq's [Free Plan limits](https://console.groq.com/docs/rate-limits): 30 requests/minute, 1,000/day, 8,000 tokens/minute and 200,000/day at verification time. The API key alone does not identify the billing plan. These checks establish working inference, not that the account is on a free plan. No billing settings were changed. Stay on Groq's Free Plan for free usage; selecting this model on a paid account does not force free billing.

The token budget includes reasoning and output, and a board run makes several calls. Quota and capacity errors remain possible. No general accuracy benchmark or full live component-sourcing run was performed. The running Render service is not changed by local verification or a Git push; it needs the documented environment update and manual deployment. Neon requires no changes.
