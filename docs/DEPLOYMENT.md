# Free hosting: Render + Neon

This repository configures one Render Free Docker web service and an external Neon Free PostgreSQL database. Both accounts must be owned/configured by the maintainer; the repository cannot create credentials automatically.

## Deploy

1. Create a Neon project and database. Copy its **direct** PostgreSQL connection string with TLS enabled (`sslmode=require`), not a `-pooler` hostname. Keep the password in deployment secrets.
2. Push this revision to `Armaan-king/Continuity-SC3040`.
3. In Render, create a Blueprint from that repository. Render reads `render.yaml`, builds `Dockerfile`, and creates `continuity-sc3040` on the Free instance type.
4. Set `DATABASE_URL` to the Neon connection string when prompted. The Blueprint sets production mode and secure, same-site session cookies.
5. Optionally set `CONTINUITY_LLM_PROVIDER=groq` and `GROQ_API_KEY` to a developer key from Groq. Without credentials the service uses basic planning. Do not set a browser `VITE_*` key.
6. Wait for the deployment health check at `/api/ready`. Open the Render URL, register an account, create a project, and reload it to verify persistence. `/api/runtime` describes AI configuration and persistence without exposing credentials.

No separate static frontend service, CORS wildcard, build-time API hostname, or cross-site cookie is required. Existing split deployments can be retired after verifying this one. The new Blueprint does not automatically delete old services.

## Free-tier behavior

[Render Free](https://render.com/docs/free) suspends idle services and has an ephemeral filesystem. Expect a cold start on the first visit. Neon also scales idle compute down; the pool checks connections before handing them out. [Neon plans](https://neon.com/pricing) and [Groq rate limits](https://console.groq.com/docs/rate-limits) describe the applicable quotas; free does not mean unlimited or an availability guarantee.

Accounts, sessions, projects, events, findings and checkpoints use PostgreSQL. Normalization/parts caches are disposable. Do not configure a local SQLite file or place PostgreSQL data inside the Render web container.

## Updates and recovery

Deploy one worker. Startup applies versioned migrations and checksums. Back up Neon before schema changes. If a process exits during a run, its lease expires; reopen the project and continue an eligible saved checkpoint. A failed BOM-only run must be submitted again.

Check `/api/health` for process liveness and `/api/ready` for database readiness. Production startup refuses to run if PostgreSQL is absent or secure cookies are disabled. If deployment fails, inspect Render startup logs; never paste database passwords or API keys into issue logs.

The Docker image can also be run with `docker compose up --build` for a local production-build smoke test. This uses local development cookies because localhost is HTTP.
