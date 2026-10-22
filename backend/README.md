# Backend

FastAPI serves the application API, LangGraph orchestrates design work, and PostgreSQL stores accounts, projects, checkpoints and events. Electrical rules live in `fuze/engine` and do not depend on a model.

## Setup

From the repository root:

```sh
python -m venv backend/.venv
# Windows PowerShell:
backend/.venv/Scripts/python.exe -m pip install -r backend/requirements.lock
backend/.venv/Scripts/python.exe -m pip install --no-deps -e backend
# macOS/Linux: use backend/.venv/bin/python instead.
```

Copy `backend/.env.example` to `backend/.env`. Leave `DATABASE_URL` blank for temporary local mode, or start PostgreSQL with `docker compose up -d database` and set:

```dotenv
DATABASE_URL=postgresql://fuze:local-development-only@localhost:5432/fuze
```

Start the API with the virtual environment Python:

```sh
python -m fuze --api-only --reload
```

Start Vite separately as described in the frontend README. After building the frontend, `python -m fuze` serves both applications at port 8000. This entry point also chooses the event loop needed by psycopg on Windows. One worker is the supported deployment configuration.

## Configuration

`backend/.env.example` documents the supported settings. `/runtime` reports public capabilities without revealing secrets; `/health` is liveness and `/ready` checks database availability. API documentation is at `/docs` in API-only mode or `/api/docs` in the combined server.

Use `FUZE_ENV=production` for deployments: it requires PostgreSQL and secure cookies. Same-origin hosting uses `SameSite=Lax`. `FUZE_ORIGINS` is needed only for additional explicitly trusted frontends.

AI calls have bounded token output, timeouts, and two total attempts. Provider failures fall back to basic planning or conservative specification parsing. Parts network requests have their own timeout/cache policy. A whole run has a configurable time limit and active-run capacity.

## Migrations

Startup applies migrations automatically. To run them separately:

```sh
python -m fuze.api.migrations
```

`schema.sql` is the frozen baseline; append changes under `fuze/api/migrations/`. Migration checksums detect accidental edits. Back up a deployed database before schema upgrades.

## Tests

```sh
python -m pip install pytest pytest-cov
python -m pytest backend/tests
```

Set `FUZE_TEST_DB` to a disposable database for database tests. Never use production: integration fixtures truncate application tables. The normal suite replaces AI and distributor responses with deterministic fixtures. It does not prove current distributor availability or model quality. Live smoke results are recorded separately in `docs/VALIDATION.md`.

To update the production lock intentionally, use `uv pip compile backend/pyproject.toml --python-version 3.13 --output-file backend/requirements.lock`, then run the checks before committing it.
