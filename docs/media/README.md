# App recordings

The recordings follow the reader's path through the app: introduction, project creation, evidence review, and revision/export. They show the local production frontend, real Python backend, and an isolated PostgreSQL database. The walkthrough and project workflow were captured on 21 October 2026; evidence and revision review on 22 October 2026 (America/New_York; JSON timestamps are UTC). They do not establish which revision is deployed to Render.

| Asset | Contents |
| --- | --- |
| [Guided walkthrough](guided-walkthrough.mp4) | Account creation and the seven-step introduction provided by the current app |
| [Project workflow](project-workflow.mp4) | Sign in, create a project, enter a brief, source real parts, review a repaired conflict, export CSV, reopen and reload the saved board, visit analytics and memory, and sign out |
| [Exa evidence and connections](research-connections.mp4) | Inspect an actual retrieved document and its verification label, review limits, declare I²C controller/peripheral connections, confirm reference power connections, and revalidate |
| [Design review and revisions](design-review.mp4) | Open the report, change 3.3 V to 5 V, inspect the voltage conflict, compare revisions, restore, export JSON/CSV, and reload the workspace |
| [Walkthrough animated preview](walkthrough.gif) | A short excerpt of the guided walkthrough, sized for the main README |
| [Review animated preview](design-review.gif) | A short excerpt showing the requirement edit, new conflict and comparison |
| [Workflow capture metadata](capture.json) | Recording time, viewport, video durations, approximate chapters, file sizes, and verified run outcome |
| [Review capture metadata](review-capture.json) | Chapters, sizes, durations, reference-data provenance, Exa retrieval timestamps, and verification results |

The guided walkthrough intentionally replays the app's existing historical example. Its inventory and prices are from that example. It does not demonstrate fresh sourcing or a new AI response. Its captions now explicitly make that distinction.

The project workflow uses **basic planning with AI disabled** and live distributor requests. It accepts no warnings on the user's behalf. A completed workflow can still have unchecked facts; its result is not a manufacturing certification.

The **review and research clips** use a manually assembled reference board with an ESP32 module and SHT40 sensor. Measurements, prices and stock come from committed historical distributor fixtures, parsed by the current deterministic parser. The app's normal database, revision commands, validation, exports and restoration are real; HTTP responses are not mocked. The board retains a controller temperature failure and unknown checks. No warning is silently accepted to make the demonstration appear successful.

Exa Search and Contents were called during preparation, returning an Ampheo source page for SHT40-AD1B-R2. The visual retake reused that actual response, including its retrieval timestamp, and made no further Exa calls. The UI labels its text **Requires verification**; displaying a source does not establish its claims. Gemini/AI planning was disabled throughout capture. These clips do not demonstrate fresh live supplier stock, AI-generated planning, physical electrical verification, or restart recovery.

The MP4s are silent H.264 recordings at 1440 × 900 and 24 fps, with streaming metadata at the beginning of each file. Initial font-loading time is trimmed; the visible workflows are otherwise continuous and run at their original speed. The GIF is a reduced-frame-rate excerpt. If GitHub or your Markdown viewer does not show a video player, download the MP4 and open it locally.

## Verification

The capture script checks the actions it records against the real local server:

- `/api/ready` reports PostgreSQL persistence, and `/api/runtime` reports basic planning.
- Registration reaches onboarding, and all seven walkthrough steps can be completed.
- A new project runs to completion without automatically accepting questions or warnings.
- CSV export has the expected columns, and the completed board is available through the API.
- Reopening and reloading the project restores its completed board.
- Analytics and memory load; logout revokes the session.
- Both clips finish with no unexpected browser console errors or uncaught page exceptions.
- FFmpeg decodes every frame of both published MP4s successfully. Sampled frames are visually inspected for legibility and correct rendering.

The captured live design placed **3 of 3 parts** and resolved **1 conflict**, with **0 accepted warnings**. See [capture.json](capture.json) for its measured runtime and the full clip metadata. Frontend lint and production build passed; the build retains its existing non-failing bundle-size warning. [Check output](checks.log).

During preparation, the first capture attempt stopped because the project name and brief were identical and a text selector matched both. The capture script now targets the project card. Visual review also caught late-loading icon fonts in an early recording; capture now waits for fonts before the published clip begins. Neither discarded recording is linked in the README.

## Re-record

Use the backend virtual environment from [backend setup](../../backend/README.md), install the [frontend dependencies](../../frontend/README.md), install Playwright Chromium, and have FFmpeg with `libx264` available on `PATH` (or set `FFMPEG` to its executable path).

From the repository root in PowerShell, prepare a **disposable** database. The `createdb` command is needed only once:

```powershell
docker compose up -d --wait database
docker compose exec database createdb -U continuity continuity_media

npm.cmd --prefix frontend run build
Push-Location frontend
npx.cmd playwright install chromium
Pop-Location

$env:DATABASE_URL = 'postgresql://continuity:local-development-only@localhost:5432/continuity_media'
$env:CONTINUITY_ENV = 'development'
$env:CONTINUITY_LLM_PROVIDER = 'disabled'
$env:CONTINUITY_FIXTURES = '0'
$env:CONTINUITY_FIXTURE_DIR = Join-Path (Get-Location).Path '.local/readme-recording/parts-fixtures'
.\backend\.venv\Scripts\python.exe -m continuity --port 58002
```

Leave that server running. In a second PowerShell terminal at the repository root:

```powershell
$env:DEMO_URL = 'http://127.0.0.1:58002'
node frontend/scripts/record-demos.mjs
node frontend/scripts/encode-demos.mjs
```

The scripts create a new throwaway account and projects on each run. They restrict recording to a local URL, but you must still ensure that server uses a disposable database. Live supplier responses may change; if a run needs input or fails, recording stops for inspection instead of silently accepting a warning. Raw captures, exported CSV, failure screenshots, and supplier fixtures stay under ignored `.local/readme-recording/`. Successful videos, screenshots, and sanitized metadata are written to `docs/media/`.

Review the videos, screenshots, metadata, and README together before committing. Stop the local app with `Ctrl+C` when finished.

## Review feature verification

The capture assertions verify that the 5 V edit creates new voltage failures; the original revision remains available; restore creates a new child revision at 3.3 V; report, CSV and reloaded workspace identify the same revision; and saved signal connections are revalidated. Exa content remains unverified after connection confirmation. Both recordings finish without unexpected browser errors. FFmpeg decodes every published MP4/GIF frame, and sampled chapter frames are inspected for legibility. [Review capture checks](review-checks.log).

The review video is approximately 40 seconds; evidence and connections approximately 28 seconds. Exact durations and chapter timestamps are in [review-capture.json](review-capture.json). Visual review prompted a retake to bring the evidence and connection controls into view more clearly. Only the verified final takes are published.

### Re-record the review features

Use a local PostgreSQL database whose name contains `media`, the installed backend environment, Playwright Chromium, and FFmpeg. Build the frontend and start the combined server as described below. The fixture seeder only accepts a local database URL and creates a new throwaway account/project without deleting existing accounts.

```powershell
# Use your local disposable media database, not production Neon.
$env:DATABASE_URL = 'postgresql://continuity:local-development-only@localhost:5432/continuity_media'
$env:CONTINUITY_ENV = 'development'
$env:CONTINUITY_LLM_PROVIDER = 'disabled'

# EXA_API_KEY must be available in the server environment or backend/.env.
# Preparation makes at most two Exa requests; never paste a key into this script.
backend/.venv/Scripts/python.exe backend/tools/seed_review_demo.py

# In another terminal, run the combined app using the same DATABASE_URL.
# backend/.venv/Scripts/python.exe -m continuity --port 58002
$env:DEMO_URL = 'http://127.0.0.1:58002'
node frontend/scripts/record-review-demos.mjs
node frontend/scripts/encode-review-demos.mjs
```

For a visual retake after a successful export, run the seeder with `--reuse-research` to use the previously exported Exa response without additional calls. Credentials, raw recordings and full exported source text remain under ignored `.local/review-recording/`; only the videos, posters, GIF, sanitized checks and metadata are published. `FFMPEG` and `PLAYWRIGHT_BROWSERS_PATH` may point to local installations.

## Other images

`workspace.png`, `projects.png`, `analytics.png`, `memory-current.png`, `walkthrough-poster.png`, and the refreshed `landing.png` are screenshots from these sessions. Older `conflict.png`, `repaired.png`, `memory.png`, `codebuddy-usage.png`, and `icon.png` remain for historical documents and attribution; the main README does not present those as fresh recordings.
