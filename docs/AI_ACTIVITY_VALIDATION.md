# AI and supplier activity verification

Validated locally on October 22, 2026. No paid Groq or Exa requests were made.

The deployed startup command configured Uvicorn's loggers but left application
loggers at `WARNING`. Successful AI requests were logged at `INFO` and therefore
absent from Render's output. The reproduction printed:

```text
Application logger threshold before fix: WARNING
Application logger threshold after fix: INFO
```

Application logging now records safe request starts and outcomes. Design SSE events
carry provider response receipts; `/api/runtime` exposes process-local request counts
and the latest outcome. A received JSON response and an accepted plan are distinct.
Fixture replay, live supplier requests, and specification cache/parser use are labelled.

Validation used the actual OpenAI-compatible SDK against an in-process HTTP transport.
Tests cover success, HTTP 400/401/403/429/503, malformed JSON returned with HTTP 200,
cancellation, retries, cache hits, changed credentials, failing observers, live versus
recorded supplier selection, and the actual design HTTP/SSE pipeline. Test responses
are simulated; these tests do not establish that Render's saved key succeeds at Groq.

```text
python -m pytest --basetemp=../.local/pytest-ai-full-2 -o addopts='--strict-markers' -q
991 passed, 106 skipped in 21.26s

npm run build
tsc -b && vite build
1108 modules transformed
built in 1.24s
```

Database and opt-in external-service tests were skipped. The frontend retains its
existing nonfatal bundle-size warning. The first test invocation could not use the
sandbox's default temporary directory; subsequent runs used the ignored workspace
temporary directory. The first broader run caught a cancellation-context error in the
new observer; its cleanup was moved into the owning task and the full suite above passed.

The final pytest output is saved in [ai-activity-tests.log](validation/ai-activity-tests.log).
Deployment instructions and receipt semantics are in [DEPLOYMENT.md](DEPLOYMENT.md).
These receipts cannot retrospectively prove which provider calls an older saved run made.
