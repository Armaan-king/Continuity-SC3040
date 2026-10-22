<div align="center">
  <h1>FUZE</h1>
  <p><strong>From an electronics idea to a design you can inspect, question and refine.</strong></p>
  <p>Component discovery · Evidence-backed checks · Guided repairs · Saved revisions</p>
  <p>
    <a href="https://continuity-sc3040.onrender.com">Open the app</a> ·
    <a href="#walk-through-fuze">Watch the workflow</a> ·
    <a href="#start-locally">Run locally</a> ·
    <a href="docs/DEPLOYMENT.md">Deployment guide</a>
  </p>
</div>

FUZE turns a plain-language electronics brief or an existing bill of materials into an interactive component graph, a validation report and an exportable BOM. It combines AI planning, catalogue and web sourcing, and deterministic electrical rules to help you find conflicts and understand what still needs evidence.

**Best suited to:** component-level electronics concepts, sensor boards, small embedded devices and early BOM reviews. A completed run means the modeled checks have finished; it does not certify a working physical circuit.

> **Demo availability:** The hosted app uses Render's free tier and is not always immediately available. It sleeps after 15 minutes without traffic and typically takes about a minute to wake. Allow the initial loading page to finish. [Render's free-service behavior](https://render.com/docs/free#spinning-down-on-idle).

## Explore

[Walkthrough](#walk-through-fuze) · [Pipeline](#how-the-pipeline-works) · [Validation](#checks-evidence-and-unknowns) · [Local setup](#start-locally) · [AI and research](#configure-ai-and-research) · [Hosting](#hosting) · [Architecture](#architecture-and-data) · [Verification](#verification)

## Walk through FUZE

Follow the same path you would in the app: start a project, source parts, inspect the findings, revise the design and return to saved work. Each preview below opens a video.

### 1. Get familiar with the workspace

Create an account to save private projects. The guided introduction explains the three connected views:

| View | What it helps you understand |
| --- | --- |
| **Component graph** | The selected components, modeled power and signal connections, and where a conflict occurs. |
| **Validation trace** | What the run searched, selected, checked and attempted to repair, including provider activity. |
| **Bill of materials** | Resolved part numbers, quantities, available pricing and CSV export. |

[![Guided introduction to the graph, validation trace and bill of materials](docs/media/walkthrough.gif)](docs/media/guided-walkthrough.mp4)

*Watch: account creation and the guided workspace introduction. This walkthrough replays a recorded example with historical parts and prices.*

### 2. Describe a device or import a BOM

Choose **New project** and describe what you want to build. You can start naturally:

> I want a small USB-powered desk monitor that measures temperature and humidity and shows the readings on a display.

Add supply voltage, operating temperature, production quantity or other constraints when you know them. FUZE creates a short project name while preserving the full brief. **Rename project** keeps your chosen name consistent across the workspace, projects and memory.

Already have components in mind? Upload a **CSV or text BOM**, up to **1 MB and 100 part rows**. Quantities are retained; an accompanying brief is optional. A parts list does not establish the wiring, so inferred power and signal connections need review.

**Sourcing covers both catalogue and web candidates.** With Exa configured, normal searches look beyond JLCPCB, including manufacturer and distributor pages. An external product can enter the shortlist without a JLCPCB/LCSC listing. FUZE compares up to three eligible candidates against the current board, then shows the selection and its available evidence. Unsupported stock, prices and specifications stay unknown.

[![Project sourcing, component graph, validation findings and bill of materials](docs/media/workspace.png)](docs/media/project-workflow.mp4)

*Watch: create a project, source parts, inspect checks, export and reopen it. This recording uses live distributor lookup with basic planning and AI disabled.*

### 3. Review the findings and close information gaps

Open **Review design** after a completed run. The review brings together the check distribution, component findings, requirement questions and source evidence. Search by component or filter **PASS**, **FAIL** and **UNKNOWN** outcomes. The distribution graphic is a count of results, not an AI confidence score.

The next action depends on what is missing:

| Missing information | How FUZE handles it |
| --- | --- |
| **Your design intent** | Asks about runtime, operating temperature, current margin and other applicable requirements. Inferred or assumed values are labelled for confirmation. |
| **A component specification** | Groups evidence tasks by component and identifies the datasheet facts needed to evaluate the affected checks. |
| **A power or signal connection** | Directs you to **Requirements** to review the power tree and declare controller/peripheral connections. |
| **An exact component** | Keeps the unresolved function visible and directs you back to the workspace. |

Use suggested answers, **Write your own**, or **Not sure yet**. Example choices describe possible goals; they are not measured component facts. A battery's capacity must come from the selected battery, and supplier search cannot decide how long you want the device to run.

**Confirm all & revalidate** saves all filled values, including displayed proposals, in one revision. Blank fields remain open. You can also confirm a single answer. Invalid batches do not partially save, and failed saves retain your entries.

For a written answer, **None of these? Describe a change** asks the configured model to propose supported requirement values. You review the preview and choose **Use these answers** before confirming. Unsupported requests are identified. Direct form answers and suggested options need no AI call.

[![Source documents, evidence labels and declared signal connections](docs/media/research-connections-poster.png)](docs/media/research-connections.mp4)

*Watch: inspect retrieved Exa text, declare an I²C connection and revalidate. The recording retains the document's Requires verification label; displaying a document alone does not establish its specifications.*

### 4. Repair, redesign and compare

A conflict pauses the workflow for a decision when automatic repair cannot resolve it. The available controls let you address the component or connection responsible:

| Action during a paused run | What happens |
| --- | --- |
| **Choose a different component** | Select a candidate for any slot, including a controller or supply behind another component's conflict. Previous selections can also be restored. |
| **Try & revalidate** | Apply your choice and check the whole board again. A failed trial stays visible, and you can choose again beyond the automatic retry limit. |
| **Search for more components** | Search the enabled sources again, excluding previously considered identifiers. |
| **Research alternatives with Exa** | Run an explicit web comparison and present identified products with supporting passages and source links before selection. |
| **Redesign the device** | Request a new structural plan, or describe the change yourself. Confirmed requirements and reusable parts are preserved where applicable. |
| **Accept a finding or stop** | Record an explicit acceptance where offered, or stop to change the brief. An accepted failure retains its original result. |

These controls operate in **paused runs**. They do not guarantee a feasible replacement exists. Empty searches pause too; continuing without a part leaves an unresolved function.

Written redesign instructions are validated before application. A valid redesign replaces the graph and reruns the checks. An unavailable, invalid or unchanged proposal leaves the existing design in place with an explanation. A missing controller can be addressed directly instead of repeatedly replacing its peripherals.

For a completed design, **Requirements** edits revalidate the saved component snapshot. They do not silently start another sourcing run. Successful edits return you to the planning workspace with the revised graph, BOM and results loaded.

[![Requirement edits, changed findings, revision comparison and export](docs/media/design-review.gif)](docs/media/design-review.mp4)

*Watch: answer a requirement question, return to planning, change the supply, inspect the resulting conflict, compare revisions, restore and export.*

**Revision history** compares saved versions. Restoring an earlier version creates a new, revalidated revision rather than erasing history. The selected revision's graph, review report and CSV/JSON exports share the same saved design and evidence.

### 5. Reopen projects and learn from past runs

**Projects** restores saved boards, traces and BOMs. **Memory** connects parts to earlier boards and findings. **Analytics** summarizes rule outcomes, repair effectiveness, recurring conflicts and supply exposure. Analytics excludes unfinished runs and the guided example; rates can be withheld when unchecked results dominate.

Closing the browser disconnects the view, not the server-side run. Reopening reconnects to saved events. Use **Stop** to cancel: **Stopping…** means acknowledgement is pending, **Stopped** confirms cancellation, and **Retry Stop** means the request failed and work may still be active.

<details>
<summary><strong>About the recordings</strong></summary>

All four videos were captured locally with PostgreSQL and AI planning disabled. The guided example and reference boards contain historical data; the project workflow used live distributor lookup. The research clip reuses an actual Exa retrieval and retains its verification status.

The recordings show the FUZE interface and the core workflow. They do not demonstrate every sourcing or recovery capability, and the manually deployed public instance may run an earlier revision. See [recording provenance, chapters and reproduction](docs/media/README.md).

</details>

## How the pipeline works

The model, search services and rules have separate responsibilities.

| Layer | Responsibility |
| --- | --- |
| **Groq or another configured model** | Interpret the brief, propose a structured plan, classify ambiguous roles, locate specification passages, suggest repairs and interpret written changes. |
| **JLCPCB catalogue through the [pcbparts.dev adapter](backend/fuze/parts/mcp.py)** | Retrieve catalogue identities, structured specifications, available stock and prices. |
| **Exa Search and Contents** | Discover products across the web and retrieve source documents, including products outside the catalogue. |
| **Python parsers and evidence checks** | Normalize units and protocols, establish source applicability, and decide which extracted facts can enter validation. |
| **Deterministic rule engine** | Evaluate the board model and explain passes, failures, warnings and unknowns. |
| **You** | Confirm design intent, review ambiguous evidence, select alternatives and decide whether to accept a finding. |

~~~mermaid
flowchart TD
    Input["Brief or imported BOM"] --> Plan["Structured requirements and board model"]
    Plan --> Sources["Catalogue and Exa discovery"]
    Sources --> Evidence["Normalize specifications and check source evidence"]
    Evidence --> Compare["Compare candidates against the board"]
    Compare --> Rules["Run deterministic checks"]
    Rules -->|Conflict or unresolved choice| Decision["Repair proposal or user decision"]
    Decision -->|Try a saved candidate| Compare
    Decision -->|Search again| Sources
    Decision -->|Redesign| Plan
    Rules -->|Run completed| Revision["Save design revision, report and BOM"]
    Revision --> Edit["Confirm requirements or connections"]
    Edit -->|Revalidate saved components| Rules
~~~

**Planning.** The planner uses a versioned prompt and structured schema. Local checks validate referenced slots and rails and preserve explicitly requested functions. Battery operation is distinguished from USB charging. Missing battery capacity and runtime targets remain unanswered. Supported Groq GPT-OSS models use [strict structured output](https://console.groq.com/docs/structured-outputs); schema validity still does not prove a circuit is correct.

**Discovery and selection.** Catalogue and Exa searches contribute independently when enabled and within budget. Normal web discovery requests up to four pages and two identified products per slot. Candidate identity includes manufacturer and model, and external-only products retain their source links. Up to three candidates are evaluated against the same board; hard failures and unknowns rank ahead of commercial preferences. Requested functions marked as pinned also receive an initial comparison.

Web discovery establishes product identity and supporting source text; it is not an exhaustive electrical comparison of every search result. Setting `FUZE_FIXTURES=1` explicitly selects recorded supplier fixtures instead of live inventory lookup.

**Evidence enrichment.** The leading candidate can receive additional research, then be revalidated and reranked. At most one fallback is researched within the same allowance. Completed parsing and research are reused during comparison. A source search returning HTTP 200 does not mean it produced usable products or admitted specifications.

**Repair.** The reviewer can propose a swap, changed power topology, added part, rail adjustment or requirement change. The rules evaluate the result. Automatic repair is bounded; further explicit user choices are available when it pauses. Reusing a retrieved alternative does not require another web search.

**Persistence.** Generated boards, imported BOMs and later edits use a shared design-validation service. It creates the saved revision used by the review, board view and exports. Revalidation of requirements and connections uses those saved facts without new research charges.

## Checks, evidence and unknowns

### What the engine evaluates

Checks apply only when the model contains the necessary components, connections and conditions.

| Check | Scope |
| --- | --- |
| **Data integrity** | Invalid, contradictory or unusable measurements and requirements. |
| **Power topology** | Incomplete or ambiguous power trees and converter direction. |
| **Voltage overlap** | Component operating ranges and regulator output coverage for modeled rails. |
| **Interface role match** | Declared protocol support and controller/peripheral roles. |
| **Pin budget** | Available GPIO and modeled signal demand, including declared nets and chip-select pressure. |
| **Current budget** | Load quantities, margins and demand propagated through cascaded rails. |
| **Thermal dissipation** | Conversion losses and estimated temperature rise under stated assumptions. |
| **Availability** | Exact component resolution, published stock, lifecycle and lead-time information where available. |
| **Temperature rating** | Published operating ratings against the requested temperature range. |
| **Footprint** | Individual component package dimensions against a requested limit. |
| **Energy budget** | Estimated battery runtime from stated capacity and modeled demand. |
| **Rail coverage** | Components missing from the modeled power connections. |

These checks are implemented in [the engine](backend/fuze/engine), independently of the model provider.

### Read the results correctly

| Result | Meaning |
| --- | --- |
| **PASS** | The modeled constraint is satisfied using the available facts and assumptions. Inspect any warning detail and the validation scope. |
| **FAIL** | The model has a concrete incompatibility under the supplied conditions. |
| **UNKNOWN** | Required evidence, a connection or an operating condition is missing or contradictory. This is not a pass. |
| **Accepted finding** | A user decision is recorded; the original outcome remains visible. |
| **Not applicable** | The rule emitted no applicable result for this model. It is not added to the pass count. |

Missing current is not zero current. Missing controller interface data is not proof that the interface is absent. Typical-only current, assumed efficiency and unconfirmed power connections can leave dependent results unknown even when a partial calculation looks acceptable.

The review separates **requirements you can answer** from **facts that need evidence**. It does not copy a supplier's operating limits into your design goals just to make the checks pass. Requirements retain their stated, inferred, assumed or confirmed basis.

### How retrieved text becomes a usable fact

Exa retrieves source material. The configured model can propose claims from that text. A separate admission process checks each proposed fact:

1. **Publisher:** Is the source a recognized manufacturer domain for this component?
2. **Identity:** Does the document apply to the exact ordering code or, where permitted, its manufacturer family?
3. **Passage:** Is the supporting quotation present in retrieved full text?
4. **Measurement:** Can code independently parse the stated parameter, units and value?
5. **Conditions:** Is it an operating specification, rather than an absolute maximum, storage rating, optional capability or unsupported condition?
6. **Consistency:** Does it conflict with other admitted evidence?

Exact-part evidence is required for numeric and package-dependent facts. A supplier-linked manufacturer family datasheet may establish positive interface support. Unknown publishers, ambiguous variants, snippets alone and unparseable tables remain reviewable leads.

Admitted facts retain the URL, passage, retrieval time, document hash, identity basis and validator version. Conflicting measurements invalidate the affected field or range and produce an unknown finding. Evidence cards distinguish **facts used by checks**, **conflicting claims excluded from checks**, and **claims needing review**.

Current and efficiency extracted from research remain review-only in this implementation because operating-mode and load matching are not yet implemented. Source admission checks attribution and parsing; it does not independently measure hardware.

### Protocol and unit normalization

The parser recognizes explicit I2C, IIC, I²C, I₂C and TWI forms, as well as supported Unicode units and range formatting. Original source strings are retained. USART can establish asynchronous UART support; the reverse does not establish synchronous USART capability. CAN-FD remains distinct from CAN.

Part numbers are not fuzzy-matched. Unknown names, ambiguous numbers and suspected typos remain unresolved rather than being silently converted to a different specification. [Source and parsing verification](docs/SOURCE_FACTS_VALIDATION.md).

### What still needs engineering review

FUZE does not verify pin-by-pin schematic wiring, signal timing, PCB routing, physical footprints against a finished layout, enclosure fit or manufacturing readiness. Package-size checks apply to components, not total board dimensions.

Battery discharge range, sleep/alarm duty cycles, charger load sharing, protection and real thermal conditions need separate analysis. One nominal power tree does not establish every operating mode. Broad web discovery also does not add complete validation models for arbitrary server modules, RAM assemblies or other complex systems.

Stock and prices are snapshots. A BOM subtotal excludes items without prices. Zero failures, an accepted risk or a completed run should always be read alongside remaining unknowns and validation scope.

## Start locally

### Docker: app and database together

Install Docker Desktop, then run from the repository root:

~~~sh
docker compose up --build
~~~

Upgrading an existing local Compose database? Use the [compatibility override](docs/FUZE_UPGRADE.md#local-development) to retain its existing volume and credentials.

Open **http://localhost:8000** and create an account. [compose.yaml](compose.yaml) starts the application and a persistent local PostgreSQL database. AI is disabled by default, so no model key is needed to start. Fresh supplier lookup still requires internet access.

~~~sh
docker compose down
~~~

This stops the services while retaining the database volume. Compose credentials are for local development.

<details>
<summary><strong>Develop with Python and Vite separately</strong></summary>

The reference runtimes are **Python 3.13**, **Node 24** and **PostgreSQL 16**.

From the repository root:

~~~sh
python -m venv backend/.venv
npm ci --prefix frontend
docker compose up -d database
~~~

Install the backend with its virtual-environment interpreter.

**Windows PowerShell**

~~~powershell
backend/.venv/Scripts/python.exe -m pip install -r backend/requirements.lock
backend/.venv/Scripts/python.exe -m pip install --no-deps -e backend
Copy-Item backend/.env.example backend/.env
~~~

**macOS / Linux**

~~~sh
backend/.venv/bin/python -m pip install -r backend/requirements.lock
backend/.venv/bin/python -m pip install --no-deps -e backend
cp backend/.env.example backend/.env
~~~

Configure the local database in `backend/.env`:

~~~dotenv
DATABASE_URL=postgresql://fuze:local-development-only@localhost:5432/fuze
~~~

Start the API with `backend/.venv/Scripts/python.exe -m fuze --api-only --reload` on Windows, or `backend/.venv/bin/python -m fuze --api-only --reload` on macOS/Linux. In a second terminal:

~~~sh
npm run dev --prefix frontend
~~~

Vite proxies `/api` to port 8000. After building the frontend, `python -m fuze` serves the combined app. Use the virtual-environment interpreter for that command too.

Without `DATABASE_URL`, development mode is temporary and single-user: there are no accounts, durable projects or restart recovery. Production requires PostgreSQL. See [backend setup](backend/README.md) and [frontend setup](frontend/README.md).

</details>

## Configure AI and research

### Where settings belong

| How you run FUZE | Server configuration location |
| --- | --- |
| Python directly | `backend/.env` |
| Docker Compose | Root `.env`; [compose.yaml](compose.yaml) defines which variables are forwarded |
| Hosted Render service | **Environment** in the Render dashboard |

The Python package is `fuze`, and app settings use the `FUZE_*` prefix. Existing `CONTINUITY_*` settings remain supported during upgrades; an explicitly set `FUZE_*` value wins. Provider keys belong on the server, never in browser code or `VITE_*` variables. See the [upgrade guide](docs/FUZE_UPGRADE.md) for existing Render, Neon and local Docker installations.

### Groq and Exa

This repository's Render configuration selects Groq's **`openai/gpt-oss-120b`**. Exa is configured independently.

~~~dotenv
FUZE_LLM_PROVIDER=groq
FUZE_LLM_MODEL=openai/gpt-oss-120b
FUZE_LLM_BASE_URL=https://api.groq.com/openai/v1
FUZE_LLM_MAX_TOKENS=4096
GROQ_API_KEY=your-private-groq-key

FUZE_RESEARCH_ENABLED=1
EXA_API_KEY=your-private-exa-key
~~~

Ensure the model is allowed by both the Groq organization and the project that owns the key. Creating a key does not override [model permissions](https://console.groq.com/docs/model-permissions). Remove stale model or endpoint overrides when changing providers.

A board uses multiple requests. Free-plan eligibility, account billing and available quotas are separate; check [Groq's limits](https://console.groq.com/docs/rate-limits) and your own account. FUZE's client uses an OpenAI-compatible protocol, which does not mean the requests go to OpenAI.

### Other supported modes

| Provider setting | Purpose | Credential |
| --- | --- | --- |
| `disabled` | Limited rule-based planning and deterministic checks | None |
| `ollama` | Local model inference | No hosted key required |
| `gemini` | Google's OpenAI-compatible endpoint | `GEMINI_API_KEY` |
| `zai` | Z.ai model endpoint | `ZAI_API_KEY` |
| `custom` | A compatible endpoint with an explicit base URL and model | `FUZE_LLM_API_KEY`, when required |
| `auto` | Select a configured Groq, Gemini or Z.ai key; otherwise basic mode | Provider-specific |

See [provider defaults](backend/fuze/llm.py) and [environment examples](backend/.env.example). Model availability is controlled by the provider. Basic planning supports a limited vocabulary and keeps its limitations visible; it is not equivalent to a successful AI plan.

<details>
<summary><strong>Run a local model with Ollama</strong></summary>

The repository includes a Qwen 2.5 7B configuration with an 8,192-token context:

~~~sh
ollama pull qwen2.5:7b
ollama create fuze-local -f backend/Modelfile
~~~

Set `FUZE_LLM_PROVIDER=ollama` in the relevant environment file. When the app runs in Docker Desktop and Ollama runs on the host, also set:

~~~dotenv
FUZE_LLM_BASE_URL=http://host.docker.internal:11434/v1
~~~

The host needs enough memory to run the model. This is a local-development option, not a model deployment inside the free Render container. See [Ollama's compatibility documentation](https://docs.ollama.com/api/openai-compatibility).

</details>

### Cost and request controls

Normal sourcing and explicit alternative research share the Exa allowance.

| Setting | Default | What it bounds |
| --- | --- | --- |
| `FUZE_LLM_MAX_CALLS` | `40` | Model HTTP attempts per execution, including retries |
| `FUZE_LLM_TOKEN_BUDGET` | `120000` | Estimated reserved tokens, reconciled with reported usage |
| `FUZE_LLM_MAX_TOKENS` | `4096` | Maximum requested output tokens per model call |
| `FUZE_RESEARCH_MAX_CALLS` | `6` | Exa requests per run |
| `FUZE_RESEARCH_BUDGET_USD` | `0.12` | Estimated Exa request allowance per run |
| `FUZE_EVIDENCE_MAX_PARTS` | `3` | Component evidence-extraction attempts per execution |

Evidence extraction batches up to eight claims from at most two documents, uses at most 2,048 output tokens and gets one model attempt. Known component categories skip unnecessary role-classification calls. Duplicate work is reused, and provider cooldowns and exhausted quotas are respected.

Exa reservations and results persist with the run across recovery. LLM allowances apply per execution and can reset after a restart. A crash before a stage commits may repeat work. These controls reduce waste; they are not a guaranteed dollar billing cap.

A smaller research allowance may leave more slots without web candidates or source facts. The trace reports skipped work instead of implying every component was researched. Exa product discovery can still operate when Groq is unavailable; admitting additional electrical facts may require the model.

### See what actually ran

The timeline, application logs and `/api/runtime` distinguish configuration from observed activity.

| Signal | Interpretation |
| --- | --- |
| `ai_request` logs | Model request starts and outcomes, provider/model, reported tokens and sanitized failure reasons |
| `exa_request` logs | Actual Exa HTTP outcomes, retrieved document counts, partial results and reported cost when available |
| `exa_discovery` logs | Number of product identities admitted from returned sources |
| `ai.activity` / `research.activity` | Process-local activity counters and recent outcomes |
| `research.sourcing = web_and_catalogue` | Catalogue and web sourcing are enabled, with fixture replay off |

A cache hit is not a new API call. A successful HTTP response is not proof that a plan or a source fact passed validation. Recorded fixtures are labelled separately from live supplier results. Activity counters are not the provider's billing ledger.

If planning fails, the trace keeps a visible warning and uses the limited basic plan where possible. HTTP 400, authentication failure, model permission errors, rate limits and provider outages need different fixes. See [deployment diagnostics](docs/DEPLOYMENT.md#verify-the-deployed-service), [AI activity verification](docs/AI_ACTIVITY_VALIDATION.md) and [Exa activity verification](docs/MULTI_SOURCE_VALIDATION.md).

## Hosting

The supported deployment is **one Render Docker web service plus Neon PostgreSQL**. The built frontend and `/api` share an origin, and the backend runs one worker. The Docker image handles the build and startup migrations.

**The existing demo is deployed manually.** A GitHub push does not update the hosted service. Use Render's **Manual Deploy** when an application update is ready. Services deployed from a public repository URL without a connected Git provider require manual deployment. [Render deployment behavior](https://render.com/docs/deploys).

For a new instance:

1. Create or select a Neon database and copy its **direct PostgreSQL connection string**, including SSL parameters.
2. Deploy this repository as a Render Docker web service. [render.yaml](render.yaml) provides the service settings for a Blueprint deployment.
3. Set `DATABASE_URL`, the production cookie settings and your selected provider credentials. Use `/api/ready` as the health-check path.
4. Verify database readiness, then register, save a project and reopen it to check persistence. Check provider receipts separately.

Neon project IDs, display names and database branches are separate concepts. FUZE needs `DATABASE_URL`; no particular Neon project ID or branch name is required.

Accounts, projects, revisions, events and checkpoints live in PostgreSQL. Render's local filesystem is ephemeral. Keep the current service URL unless you deliberately change hosting; the FUZE repository rename does not require new database or provider credentials. See the [complete deployment guide](docs/DEPLOYMENT.md).

## Architecture and data

### Application boundaries

~~~mermaid
flowchart LR
    UI["React and TypeScript workspace"] <-->|HTTP and SSE| API["FastAPI"]
    API --> Runs["Run manager and LangGraph"]
    Runs --> AI["Configured model provider"]
    Runs --> Sources["Catalogue and Exa adapters"]
    Runs --> Design["Canonical design and Python rules"]
    API --> Design
    Design --> Revisions["Immutable revisions"]
    Revisions --> DB[("PostgreSQL")]
    Runs --> DB
    API --> DB
~~~

The browser displays typed events and saved documents. The backend owns provider calls, identity checks, validation, account access and persistence. Model responses enter validated application structures; they do not write rule verdicts.

| Code area | Responsibility |
| --- | --- |
| [frontend/src/app](frontend/src/app) | Workspace, projects, review, requirements, history, memory and analytics |
| [api](backend/fuze/api) | HTTP routes, authentication, ownership, runs, streaming and persistence |
| [graph](backend/fuze/graph) | Workflow orchestration, candidate selection and recovery decisions |
| [planner](backend/fuze/planner) | Structured plans, requirements, topology and redesign |
| [parts](backend/fuze/parts) | Catalogue lookup, Exa, document extraction, parsing and evidence admission |
| [engine](backend/fuze/engine) | Pure Python constraints, power calculations and repair policy |
| [designs](backend/fuze/designs) | Shared design documents, guidance, revision comparison and answer proposals |

For prompt work, start with [the planning contract](backend/fuze/planner/prompt.py), [its schema](backend/fuze/planner/schema.py), [repair review](backend/fuze/reviewer.py), [clarification interpretation](backend/fuze/interpret.py) and [evidence extraction](backend/fuze/parts/evidence.py).

### What is stored

| Record group | Contents |
| --- | --- |
| **Accounts and sessions** | Password hashes, onboarding state, hashed session tokens and expiry |
| **Projects and threads** | Ownership, names, original briefs, run status and execution leases |
| **Events and findings** | Ordered replayable activity, board/BOM updates and rule/repair observations |
| **Design revisions** | Components, quantities, requirements, connections, evidence, report, parent revision and content hash |
| **Part facts** | Reusable sourced component properties; board-specific verdicts are not intrinsic part facts |
| **Inputs, stages and checkpoints** | Original inputs, cancellation state and completed work used for eligible recovery |
| **Research requests** | Deduplicated reservations, results and reported costs for the run |
| **Migration records** | Applied SQL versions and checksums |

Each account may have one active run. Execution leases reject writes from an obsolete worker. Events are saved before publication, so reconnecting can replay from the last received sequence instead of starting a second run.

Eligible interrupted inputs can recover from checkpoints while the host is awake. Explicit cancellation excludes automatic recovery. This is bounded recovery, not a guarantee that every external request runs exactly once or that work continues while the host is asleep.

Revision commands check ownership and the current project revision; stale edits are rejected rather than overwriting newer work. Restores create revisions. Historical run traces, Memory and Analytics retain original observations; **Review is authoritative for the selected edited revision**.

Startup applies ordered, checksum-checked migrations under a PostgreSQL advisory lock. Add a new migration instead of editing an applied one. The supported setup is a single worker; horizontal scaling would require additional queueing and global resource controls. [Architecture and data-model details](docs/ARCHITECTURE.md).

Accounts currently support registration, login and logout. Email verification, password-reset email and organization roles are not implemented.

## Verification

### Automated checks

After installing the development dependencies, use the backend virtual-environment Python:

~~~sh
python -m pip install pytest pytest-cov
python -m pytest backend/tests
npm --prefix frontend run lint
npm --prefix frontend run build
~~~

For Chromium tests, install the browser from `frontend/` and run the suite:

~~~sh
npx playwright install chromium
npm run test:e2e
~~~

Set `FUZE_TEST_DB` for PostgreSQL integration tests and `E2E_DATABASE_URL` for browser tests. Both must identify disposable databases: the suites truncate or seed data. Do not point them at production Neon. Playwright starts dedicated local app servers. [GitHub Actions](.github/workflows/ci.yml) also runs backend, database, lint, build and Chromium checks.

### Recorded verification

These are dated local results, not promises about current provider availability or hardware correctness.

| Area | Recorded evidence |
| --- | --- |
| **Independent web sourcing and Exa receipts** | [1,123 backend tests passed, 5 optional external-service tests skipped](docs/MULTI_SOURCE_VALIDATION.md); production build passed; bounded live Exa/Groq checks recorded |
| **Repeated swaps and redesign recovery** | [1,072 backend tests and 15 targeted browser tests passed](docs/RECOVERY_VALIDATION.md) |
| **Source admission and protocol parsing** | [Accepted/rejected evidence, normalization and live-check limitations](docs/SOURCE_FACTS_VALIDATION.md) |
| **Review questions and saved revisions** | [897 backend tests and 28 browser tests passed](docs/REVIEW_VALIDATION.md) |
| **Provider failures and cancellation** | [Request validation, fallback and cleanup checks](docs/PROVIDER_CANCELLATION_VALIDATION.md) |
| **Video workflows** | [Capture assertions, media verification and data provenance](docs/media/README.md) |

### Optional live diagnostics

These commands consume configured provider quota; they are separate from the normal automated suite. Run from the repository root with the backend interpreter:

~~~sh
python backend/tools/check_planner.py
python backend/tools/check_sourcing.py
python backend/tools/check_research.py
~~~

| Diagnostic | External work |
| --- | --- |
| `check_planner.py` | At most one model request; no sourcing, Exa or database writes |
| `check_sourcing.py` | Live supplier lookup, at most one Exa request and one model request; no database writes |
| `check_research.py` | At most one Exa Search and one Contents request |

A successful adapter check proves the exercised connection and response handling. It does not prove a complete device design, and it may admit zero usable electrical facts.

## Further reading

| Guide | Contents |
| --- | --- |
| [Deployment](docs/DEPLOYMENT.md) | Render, Neon, provider configuration, budgets and hosted diagnostics |
| [Architecture](docs/ARCHITECTURE.md) | Boundaries, database tables, leases, recovery and migrations |
| [Source facts](docs/SOURCE_FACTS_VALIDATION.md) | Admission rules, parser behavior, evidence limits and live-check scope |
| [Component recovery](docs/RECOVERY_VALIDATION.md) | Manual swaps, wider search, redesign and repeat validation |
| [Backend](backend/README.md) / [Frontend](frontend/README.md) | Development commands and component-specific setup |
| [Recordings](docs/media/README.md) | Video chapters, historical inputs, capture scripts and verification |

## License and attribution

[MIT](LICENSE). Original copyright notices, assets and engineering documents are retained. Historical proposals and SWE deliverables describe earlier versions; this README describes the current implementation.
