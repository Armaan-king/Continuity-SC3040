# Free hosting: Render + Neon

This repository configures one Render Free Docker web service and an external Neon Free PostgreSQL database. Both accounts must be owned/configured by the maintainer; the repository cannot create credentials automatically.

## Deploy

1. Open a Neon project and database in your own account (or create them). Click **Connect**, select the database branch, database, and role, and turn **Connection pooling off**. Copy the **direct** PostgreSQL URL with its TLS parameters, including `sslmode=require`, rather than a `-pooler` hostname. Keep the full URL in deployment secrets.
2. Push this revision to `Armaan-king/Continuity-SC3040`.
3. In Render, choose **New → Blueprint** and connect that repository. Use Git branch `main` and Blueprint path `render.yaml`. Render reads the configuration, builds `Dockerfile`, and creates `continuity-sc3040` on the Free instance type. The Dockerfile supplies the build and start commands.
4. Set `DATABASE_URL` to the Neon connection string when prompted. The Blueprint sets production mode and secure, same-site session cookies.
5. The Blueprint selects Groq's `openai/gpt-oss-120b`; supply `GROQ_API_KEY` privately. For Gemini instead, set `CONTINUITY_LLM_PROVIDER=gemini`, `CONTINUITY_LLM_MODEL=gemini-3.8-flash`, and `GEMINI_API_KEY`, and remove any old base-URL override or set it to `https://generativelanguage.googleapis.com/v1beta/openai/`. Add optional `EXA_API_KEY` for research. Without model credentials the service uses basic planning. Do not set a browser `VITE_*` key.
6. Wait for the deployment health check at `/api/ready`. Open the Render URL, register an account, create a project, and reload it to verify persistence. `/api/runtime` describes AI configuration and persistence without exposing credentials.

No separate static frontend service, CORS wildcard, build-time API hostname, or cross-site cookie is required. Existing split deployments can be retired after verifying this one. The new Blueprint does not automatically delete old services.

## Switch the existing Render service to Groq

For the existing manually deployed service, open **Render → Continuity-SC3040 → Environment** and set:

| Key | Value |
| --- | --- |
| `CONTINUITY_LLM_PROVIDER` | `groq` |
| `CONTINUITY_LLM_MODEL` | `openai/gpt-oss-120b` |
| `CONTINUITY_LLM_BASE_URL` | `https://api.groq.com/openai/v1` |
| `CONTINUITY_LLM_MAX_TOKENS` | `4096` |
| `GROQ_API_KEY` | Your private Groq API key |

Replace any Gemini model/base-URL values above. Remove `CONTINUITY_LLM_API_KEY` if it contains an older provider's key; the dedicated Groq key takes precedence in this version. A stored `GEMINI_API_KEY` is not selected when the provider is explicitly `groq`. Keep the existing `DATABASE_URL`, `EXA_API_KEY`, production mode and cookie settings. Neon needs no changes.

Save the environment and **manually deploy the latest commit**. Updating GitHub or the local ignored `backend/.env` does not update the running service automatically. No GitHub account linking is required for an already configured public-repository service.

The review and prompt update needs no additional Neon changes or mandatory environment variables. Optional application usage limits have these defaults:

| Key | Default | Scope |
| --- | --- | --- |
| `CONTINUITY_LLM_MAX_CALLS` | `40` | Model HTTP attempts per execution, including retries |
| `CONTINUITY_LLM_TOKEN_BUDGET` | `120000` | Estimated reserved tokens, replaced with actual reported usage after success |
| `CONTINUITY_RESEARCH_MAX_CALLS` | `6` | Exa requests per run, persisted across restarts |
| `CONTINUITY_RESEARCH_BUDGET_USD` | `0.12` | Exa estimated request budget, not a provider billing cap |
| `CONTINUITY_EVIDENCE_MAX_PARTS` | `3` | Batched specification extractions per execution; each has one model attempt and at most 2,048 output tokens |

Keep the existing Groq and Exa keys. Groq planning uses strict JSON Schema output on GPT-OSS; missing or invalid plans still fail explicitly into the basic fallback. Saving an answer or requirement revalidates stored components without additional model/research requests and returns to the updated planning workspace. Old reports retain their historical results; a saved edit creates a new revision using the current rules. New LLM executions get a fresh allowance, while saved stages avoid repeating completed work.

The source-fact update requires a **manual Render deployment** and no Neon migration or mandatory new variables. New designs and BOM imports can use validated research facts. Existing saved revisions retain their original evidence; requirement edits do not launch paid research. For a smaller research allowance, set `CONTINUITY_EVIDENCE_MAX_PARTS=1`, `CONTINUITY_RESEARCH_MAX_CALLS=2` and `CONTINUITY_RESEARCH_BUDGET_USD=0.04`; this may leave more components unresolved. Do not increase limits just to clear unknown results.

After deployment, open `/api/runtime`. The `ai` object should report:

```json
{"configured":true,"provider":"groq","model":"openai/gpt-oss-120b","mode":"ai"}
```

This confirms selection, not successful inference. Run a new small design and check the planning trace. HTTP 400 means the provider rejected the request; the safe explanation now distinguishes known output-format, model, token-limit and JSON-generation errors without displaying the provider's raw response. HTTP 401/403 indicates a key or permission problem; HTTP 404 indicates a model/endpoint problem; HTTP 429 indicates rate/token quotas; HTTP 5xx indicates a provider-side availability problem. The app retries a short numeric `Retry-After` once (up to 30 seconds); longer waits use the explicit fallback.

The planning-diagnostics and cancellation update requires a manual code deployment, with no new environment variables or Neon migration. The configured `groq` label alone does not verify the base URL; check it against the table above. The same plant brief succeeded locally in one request, but that does not prove a deployed request will succeed. Earlier HTTP 400 logs without the provider reason cannot identify the original rejection. [Test results and limits](PROVIDER_CANCELLATION_VALIDATION.md).

Groq lists this model on its [Free Plan](https://console.groq.com/docs/rate-limits), currently 30 requests/minute, 1,000/day, 8,000 tokens/minute and 200,000/day. Stay on Groq's Free Plan for free usage. The app cannot infer your billing plan from an API key, and the model ID does not make a paid account's calls free. It never upgrades your Groq plan or purchases credits. Free-tier capacity and availability are not guaranteed.

For direct local Python use, store the same variables in the gitignored `backend/.env`. For Docker Compose, supply them through the root `.env` or shell environment; `compose.yaml` forwards the Groq key and token budget. Do not put the key in `render.yaml` or commit it.

## Finding the right Neon project

Neon displays a project name, while its CLI accepts a separate project ID with `--project-id`. A generated onboarding prompt may contain IDs belonging to the project and organization selected when that prompt was generated. Those values are not app settings and are not names you must recreate.

Use the project shown in your Neon dashboard. If an ID from a copied prompt does not match anything accessible, check the selected account and organization; you can also use a different project you own. Project IDs are available in the project's settings. The app does not require a particular project ID, organization ID, or database branch name.

The Neon branch selected in **Connect** can be `main`, `production`, or another branch you intentionally created. It is separate from the repository's Git branch. Copy that branch's direct connection URL into Render's `DATABASE_URL` field, without a `psql` prefix, surrounding quotes, or `DATABASE_URL=`. This Python backend already uses psycopg and reads that variable; installing a JavaScript database driver or running `neon init` is not required for this deployment.

If you used Neon's CLI to pull credentials into `.env.local`, those local values do not automatically reach Render. Set the secret on the Render service as well. [Neon connection instructions](https://neon.com/docs/connect/connect-from-any-app) and [Render Blueprint instructions](https://render.com/docs/infrastructure-as-code) describe the dashboard flows.

## Verify the deployed service

Replace the example host with the actual URL displayed by Render:

```powershell
Invoke-RestMethod 'https://YOUR-SERVICE.onrender.com/api/ready'
Invoke-RestMethod 'https://YOUR-SERVICE.onrender.com/api/runtime'
```

Readiness should return `status: ready` and `persistence: postgres`. Runtime reports whether AI is configured. Then register a new account, create a project, complete or intentionally stop a design, reload the project, and check its saved state. Export the BOM of a completed run. These steps verify more than database connectivity alone.

For Gemini, `/api/runtime` should show `ai.configured: true`, `ai.provider: gemini`, and your selected model (by default `gemini-3.8-flash`). This confirms configuration only. Run a design and check that planning succeeds without the basic-planning warning or a `planning failed` log. If Google returns HTTP 404, verify the model, base URL, and model access for the Google project associated with your key. Google currently limits [Gemini 2.5 access](https://ai.google.dev/gemini-api/docs/deprecations) to previous active users; a missing model is not evidence that AI was manually disabled. The endpoint and model used here follow [Google's compatibility guide](https://ai.google.dev/gemini-api/docs/openai).

The pytest and browser integration suites clear or seed data. Run them only against separate disposable databases, never the Neon database holding users' projects.

## Free-tier behavior

[Render Free](https://render.com/docs/free) suspends idle services and has an ephemeral filesystem. Expect a cold start on the first visit. Neon also scales idle compute down; the pool checks connections before handing them out. [Neon plans](https://neon.com/pricing) and [Groq rate limits](https://console.groq.com/docs/rate-limits) describe the applicable quotas; free does not mean unlimited or an availability guarantee.

Accounts, sessions, projects, events, findings and checkpoints use PostgreSQL. Normalization/parts caches are disposable. Do not configure a local SQLite file or place PostgreSQL data inside the Render web container.

## Updates and recovery

Deploy one worker. Startup applies versioned migrations and checksums, including migration 004 for design revisions, durable run inputs/stages, and research request reservations. Back up Neon before schema changes. If a process exits during a run, its lease expires; while the app is awake it attempts recovery of eligible abandoned runs at most twice. Saved BOM stages can now resume too. Explicitly cancelled runs are excluded from automatic recovery; failed runs can be continued manually. Nothing executes while Render is asleep.

For the existing service, add `EXA_API_KEY` in Render's Environment settings, save, and deploy the new commit. Local `.env` files are not uploaded. Confirm `/api/runtime` reports `research.configured: true`; this indicates a key is present, not that it has been accepted. A completed request updates `research.last_request`. New completed projects expose **Review design**. Older projects do not gain invented evidence or retroactive revisions.

Check `/api/health` for process liveness and `/api/ready` for database readiness. Production startup refuses to run if PostgreSQL is absent or secure cookies are disabled. If deployment fails, inspect Render startup logs; never paste database passwords or API keys into issue logs.

The Docker image can also be run with `docker compose up --build` for a local production-build smoke test. This uses local development cookies because localhost is HTTP.
