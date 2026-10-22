# Free hosting: Render + Neon

This repository configures one Render Free Docker web service and an external Neon Free PostgreSQL database. Both accounts must be owned/configured by the maintainer; the repository cannot create credentials automatically.

## Deploy

1. Open a Neon project and database in your own account (or create them). Click **Connect**, select the database branch, database, and role, and turn **Connection pooling off**. Copy the **direct** PostgreSQL URL with its TLS parameters, including `sslmode=require`, rather than a `-pooler` hostname. Keep the full URL in deployment secrets.
2. Push this revision to `Armaan-king/Continuity-SC3040`.
3. In Render, choose **New → Blueprint** and connect that repository. Use Git branch `main` and Blueprint path `render.yaml`. Render reads the configuration, builds `Dockerfile`, and creates `continuity-sc3040` on the Free instance type. The Dockerfile supplies the build and start commands.
4. Set `DATABASE_URL` to the Neon connection string when prompted. The Blueprint sets production mode and secure, same-site session cookies.
5. For Gemini, set `CONTINUITY_LLM_PROVIDER=gemini` and `GEMINI_API_KEY`. The default model is `gemini-3.8-flash` at `https://generativelanguage.googleapis.com/v1beta/openai/`. Remove old `CONTINUITY_LLM_MODEL` and `CONTINUITY_LLM_BASE_URL` overrides, or set them to these values, then save and deploy. Add optional `EXA_API_KEY` for research. Existing Groq configuration remains supported. Without model credentials the service uses basic planning. Do not set a browser `VITE_*` key.
6. Wait for the deployment health check at `/api/ready`. Open the Render URL, register an account, create a project, and reload it to verify persistence. `/api/runtime` describes AI configuration and persistence without exposing credentials.

No separate static frontend service, CORS wildcard, build-time API hostname, or cross-site cookie is required. Existing split deployments can be retired after verifying this one. The new Blueprint does not automatically delete old services.

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
