# Continuity

Continuity turns a circuit-board brief or an existing bill of materials into a workspace for sourcing parts, inspecting electrical checks, and reviewing repairs. It brings the board graph, validation trace, and BOM together, with saved projects, findings, and analytics.

Continuity is a design-assistance application. A completed run can still contain unchecked specifications or accepted warnings; consult the evidence shown for each rule before using a board.

## See the current app

[![Current interface replaying the guided walkthrough](docs/media/walkthrough.gif)](docs/media/guided-walkthrough.mp4)

| Recording | What it shows | Data and AI mode |
| --- | --- | --- |
| [Project workflow](docs/media/project-workflow.mp4) | Sign in, create a board, source parts, inspect checks, export CSV, reopen the saved board, and review analytics and memory | Real local app and PostgreSQL; live distributor lookup; basic planning with AI disabled |
| [Guided walkthrough](docs/media/guided-walkthrough.mp4) | Create an account and follow the seven-step introduction to the graph, trace, conflict evidence, BOM, and memory | The app's built-in recorded example; historical parts and prices |

These are screen recordings of the current production build, not a public deployment. The GIF previews the guided walkthrough. Open the MP4 links for the full recordings; download the file if your Markdown viewer does not offer playback. [Recording details and reproduction](docs/media/README.md).

![A completed project with its graph, validation trace, and bill of materials](docs/media/workspace.png)

## Use the app

1. **Create an account.** With PostgreSQL configured, your projects are private to your account. New accounts enter the guided walkthrough; finish or skip it to reach your projects.
2. **Start a project.** Use the **New project** button and describe the board, including its supply, functions, operating conditions, and production quantity when known. A small first example is `USB temperature and humidity sensor`.
3. **Or attach a BOM.** The brief screen accepts `.csv` and `.txt` files up to 1 MB, with at most 100 parts. You can attach a BOM without a brief.
4. **Follow the validation trace.** Parts arrive in the graph as sourcing and checks proceed. Open the findings and evidence; answer clarification questions when the run needs input.
5. **Review and export.** Inspect selected parts, prices, failures, warnings, and unchecked facts before choosing **Export BOM**. Completion describes the workflow, not a guarantee that a board is ready to manufacture.
6. **Return to your work.** Reopen a project to restore its board, trace, and BOM. **Memory** connects parts, boards, and earlier findings; **Analytics** summarizes validation outcomes across your designs.

Closing a browser view does not cancel its server-side run. The event stream reconnects using its saved cursor; you can explicitly cancel a run or recover an eligible checkpoint after interrupted execution. Fresh sourcing needs internet access to the distributor.

## Start locally

Install Docker Desktop, then run from the repository root:

```sh
docker compose up --build
```

Open **http://localhost:8000**. This starts the app and a persistent local PostgreSQL database. Sign up to save projects. Compose credentials are for local development only. AI is disabled by default; no API key is needed to start.

To stop the local services while keeping the database volume, run `docker compose down`.

For development without the app container, see [backend setup](backend/README.md) and [frontend setup](frontend/README.md). Python 3.13 and Node 24 are the reference runtimes. Without `DATABASE_URL`, the app provides a temporary, single-user workspace without accounts or saved projects; restarting loses its data. Production refuses to start without a database.

## Capabilities and limits

- Account registration, login/logout, private projects, and project deletion.
- Natural-language briefs and CSV/text BOM input (up to 100 parts).
- Distributor searches, source-backed specifications, deterministic validation, repair loops, and clarification questions.
- Board and BOM restoration, CSV export, recorded findings, and validation analytics.
- Runs that continue across browser disconnections, event replay without duplicates, explicit cancellation, and eligible checkpoint recovery after interrupted execution.
- A clearly identified recorded walkthrough for exploring the interface.

Compatibility verdicts come from Python rules. Missing specifications remain unchecked. Supplier availability and prices can change after a run, and accepted warnings remain warnings. The walkthrough and test fixtures are historical examples, not fresh sourcing or AI output. The supported deployment uses one backend worker; load testing and multi-replica operation are outside the current verification.

## AI options

The Python rule engine makes the compatibility decisions. Optional AI plans functional blocks, interprets specifications, proposes repairs, and interprets clarification answers. The app uses an OpenAI-compatible client protocol; that does not mean it calls OpenAI.

| Mode | Configuration | API key |
| --- | --- | --- |
| Basic rules | `CONTINUITY_LLM_PROVIDER=disabled` | None |
| Local Ollama | `CONTINUITY_LLM_PROVIDER=ollama` | None; download a model locally |
| Groq | `CONTINUITY_LLM_PROVIDER=groq`, `GROQ_API_KEY` | Required; provider quotas apply |
| Gemini | `CONTINUITY_LLM_PROVIDER=gemini`, `GEMINI_API_KEY` | Required; provider quotas apply |
| Existing Z.ai setup | `CONTINUITY_LLM_PROVIDER=zai`, `ZAI_API_KEY` | Required |
| Compatible endpoint | `custom`, base URL, model, generic key | Provider-dependent |

For a local AI server, create the tested Qwen 2.5 7B configuration (about a 4.7 GB download, with an 8192-token context):

```sh
ollama pull qwen2.5:7b
ollama create continuity-local -f backend/Modelfile
```

Set `CONTINUITY_LLM_PROVIDER=ollama` in `backend/.env` when running Python directly. For the Docker app, set it in the root `.env` together with `CONTINUITY_LLM_BASE_URL=http://host.docker.internal:11434/v1`. Run Ollama on the host and allow the container to reach it. Do not try to run a model inside the free Render container.

For hosted AI, configure the provider and key as server environment variables. Never put secrets in `VITE_*`, source files, or the browser. With no configured key, `auto` selects basic planning. It recognizes a limited set of functions and parses explicit measurements; missing facts remain unchecked.

[Groq setup](https://console.groq.com/docs/quickstart), [Ollama compatibility](https://docs.ollama.com/api/openai-compatibility), [xAI requirements](https://docs.x.ai/developers/quickstart). Grok is not a keyless free API.

## Hosting

The deployment target is **one Render Free Docker web service plus Neon Free PostgreSQL**. The frontend and `/api` share one origin, simplifying sessions and deployment. See the complete [deployment guide](docs/DEPLOYMENT.md). `render.yaml` is a deployable Blueprint; it is not evidence that this revision is already live.

1. In your Neon project, click **Connect**, select the branch and database you want to use, and disable **Connection pooling** to copy the direct PostgreSQL URL with its SSL parameters.
2. In Render, choose **New → Blueprint**, connect `Armaan-king/Continuity-SC3040`, select Git branch `main`, and use `render.yaml`.
3. Supply the Neon URL as Render's secret `DATABASE_URL`, then deploy. The Dockerfile builds the frontend and starts the backend; startup applies database migrations.
4. Open the URL assigned by Render. `/api/ready` should report `{"status":"ready","persistence":"postgres"}`. Register, save a project, and reload it to verify persistence.

**Neon project names and IDs are different.** A value passed to the CLI as `--project-id` is a Neon identifier, not the display name you must find or create. Use a project visible in your own account and organization. Its database branch need not be named `production`, and it is independent of the GitHub branch `main`. This app only needs `DATABASE_URL`; no project or organization ID is hard-coded.

Free services have cold starts and quotas. Render's local filesystem is ephemeral; accounts, projects, events, findings, and graph checkpoints belong in PostgreSQL. Part lookup caches can be recreated.

## Architecture and database

```mermaid
flowchart LR
    UI[React workspace] <-->|HTTP and event stream| API[FastAPI]
    API --> Runs[LangGraph runs and Python rules]
    API --> DB[(PostgreSQL)]
    Runs --> Parts[Distributor lookup]
    Runs --> AI[Optional AI provider]
    Runs --> DB
```

The top-level layout is unchanged: `frontend/`, `backend/`, `docs/`, and the existing SWE deliverables remain in place. See [architecture and data model](docs/ARCHITECTURE.md) for component boundaries, tables, execution leases, recovery, migrations, and limitations.

Database changes are ordered, checksum-checked SQL migrations. New deployments and existing schemas are upgraded under a PostgreSQL advisory lock. Never modify an applied migration; add the next numbered file.

## Verification

```sh
python -m pytest backend/tests
npm --prefix frontend run lint
npm --prefix frontend run build
npm --prefix frontend run test:e2e
```

Use the backend virtual environment's Python. Set `CONTINUITY_TEST_DB` to a disposable PostgreSQL database to include integration tests. The store tests truncate their test tables. Browser tests need a separate disposable `E2E_DATABASE_URL`; they seed a known account. See [validation report](docs/VALIDATION.md) for the results produced during this update. GitHub Actions repeats backend, database, frontend, and Chromium checks.

The recorded implementation verification produced **779 backend passes, 5 optional external-service skips, and 20 browser passes**. Separate smoke runs exercised real parts lookup and local Ollama inference. These are dated results, not a claim that external services always succeed. [Current media capture checks](docs/media/README.md#verification) document the README recordings separately.

## License and attribution

[MIT](LICENSE). Existing copyright notices and the original project's assets and engineering documents are retained. Historical plans in `docs/` and the SWE submission folders describe earlier versions; this README and the architecture/deployment guides describe the current implementation.
