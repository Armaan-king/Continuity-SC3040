# Current app recordings

Captured on 21 October 2026 (America/New_York; the JSON timestamps are UTC). These videos show the local production frontend, real Python backend, and an isolated PostgreSQL database. They do not imply that a public Render deployment exists.

| Asset | Contents |
| --- | --- |
| [Project workflow](project-workflow.mp4) | Sign in, create a project, enter a brief, source real parts, review a repaired conflict, export CSV, reopen and reload the saved board, visit analytics and memory, and sign out |
| [Guided walkthrough](guided-walkthrough.mp4) | Account creation and the seven-step introduction provided by the current app |
| [Animated preview](walkthrough.gif) | A short excerpt of the guided walkthrough, sized for the main README |
| [Capture metadata](capture.json) | Recording time, viewport, video durations, approximate chapters, file sizes, and verified run outcome |

The project workflow uses **basic planning with AI disabled** and live distributor requests. It accepts no warnings on the user's behalf. A completed workflow can still have unchecked facts; its result is not a manufacturing certification.

The guided walkthrough intentionally replays the app's existing historical example. Its inventory and prices are from that example. It does not demonstrate fresh sourcing or a new AI response. Its captions now explicitly make that distinction.

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

## Other images

`workspace.png`, `projects.png`, `analytics.png`, `memory-current.png`, `walkthrough-poster.png`, and the refreshed `landing.png` are screenshots from these sessions. Older `conflict.png`, `repaired.png`, `memory.png`, `codebuddy-usage.png`, and `icon.png` remain for historical documents and attribution; the main README does not present those as fresh recordings.
