# Frontend

React 19, TypeScript, Vite and Tailwind power the FUZE interface. Design state is reduced from typed SSE events; presentation timing remains in the frontend.

Use Node 24. From the repository root:

```sh
npm ci --prefix frontend
npm run dev --prefix frontend
```

Start the backend on port 8000. Vite forwards `/api` to it; no frontend environment file is needed. The production Docker image serves the build and API together. `VITE_API_URL` is an optional override for a deliberately separate API origin, not a place for credentials.

```sh
npm run lint --prefix frontend
npm run build --prefix frontend
```

For browser tests, install Chromium with `npx playwright install chromium` from `frontend/`, set `E2E_DATABASE_URL` to a disposable PostgreSQL database, and run `npm run test:e2e`. Playwright starts its own API and Vite servers on ports 8020 and 5175. `E2E_PYTHON` can override the backend virtual environment interpreter. Tests seed a test account; do not use a production database.

The stream client reconnects through `/threads/{id}/events?after={seq}`, ignores duplicates, and separates disconnecting a view from cancelling a run. Runtime capability messages distinguish basic planning, AI configuration, and recorded part data.

## README recordings

`scripts/record-demos.mjs` records and verifies the current local UI using Playwright. `scripts/encode-demos.mjs` turns successful captures into MP4s and the README GIF using FFmpeg. Run against a disposable PostgreSQL database; the project workflow uses real distributor requests. See [capture setup, provenance, and checks](../docs/media/README.md).
