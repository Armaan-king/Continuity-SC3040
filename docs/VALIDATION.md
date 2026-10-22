# Validation report — 21 October 2026

For the later naming and pipeline fixes, see the [22 October consistency verification](CONSISTENCY_VALIDATION.md). This report records the earlier architecture implementation.

Implementation was completed before testing began. Failures found during verification were corrected and affected checks were rerun. Logs include unsuccessful attempts as well as the final results.

## Final results

| Check | Result | Evidence |
| --- | --- | --- |
| Backend, including real PostgreSQL integration | **779 passed, 5 skipped**, 38.98 seconds | [pytest output](validation/backend-tests-final.log), [JUnit XML](validation/backend-junit.xml) |
| Final runtime and run-manager regression | **20 passed** | [targeted output](validation/final-targeted.log) |
| Windows migration CLI | **Passed** | [migration output](validation/migration-cli.log) |
| Chromium browser suite | **20 passed**, 30.7 seconds | [browser output](validation/browser-tests-final.log) |
| Frontend lint | **Passed, no warnings** | [lint output](validation/frontend-lint-final.log) |
| TypeScript and production frontend build | **Passed** | [build output](validation/frontend-build-final.log) |
| Docker deployment image | **Built successfully** | [Docker output](validation/docker-build-final.log) |
| Current, keyless distributor lookup | **Passed** | [live parts output](validation/live-parts-final.log) |
| Real local AI extraction and planning | **Passed**, 10.73 seconds | [AI output](validation/ai-smoke-example.log) |
| Combined app with real parts and basic planning | **Completed**, 58.1 seconds | [deployment smoke](validation/deployment-smoke.log) |
| Combined app with real parts and local AI | **Completed**, 31.6 seconds | [AI deployment smoke](validation/ai-deployment-final.log) |

The five skips are opt-in external-service tests. One of those was subsequently run live and passed; the other four were not exercised against current external services. The standard suite uses deterministic model/parts fixtures. The two deployment design runs used real distributor data, and the AI smoke tests used real local inference, not mocked responses.

## End-to-end observations

The basic-planning design selected three parts, emitted 17 checks, performed one repair, and saved a BOM. The final AI-assisted design selected three parts, emitted 17 checks, and completed without accepting any test warnings. Both verified saved-board retrieval, CSV export, project deletion, and session revocation. Completion means the workflow finished; individual rule results can still be unchecked or warning-level.

The separate AI adapter test extracted the explicit 1.8–3.6 V range and produced a USB-powered regulator/MCU/sensor/display plan. The tested local configuration is `fuze-local`, created from Qwen 2.5 7B with an 8192-token context using [backend/Modelfile](../backend/Modelfile). No paid API key was used. This checks integration on representative prompts, not general model accuracy.

The browser suite exercised login/logout, private projects, analytics, saved graph/BOM restoration, exports, the guided walkthrough, local-mode entry, and stream reconnection. PostgreSQL tests covered ownership, migrations, expired leases, stale-worker write rejection, persisted cursors, and run continuation.

## Failures found and addressed

- The legacy mock event source needed the new reconnect/disconnect interface.
- Windows needed a selector event loop for psycopg, and explicit UTF-8 for recorded distributor data.
- Existing tests assumed no-key measurements were always unknown and completed checkpoints could be resumed. Fixtures now reflect conservative measurement parsing and rejection of already-finished checkpoints.
- Live AI testing exposed missing rails and a microcontroller used as a regulator. Planning now checks structure, requests one correction, and falls back if still invalid. A complete schema example and the tested local model configuration improved the sample result. Earlier failed model runs remain in `validation/ai-smoke*.log`.
- Replaying an already-answered question could leave the interface paused; later events now clear that historical pause.
- The initial sandboxed pytest run could not use its default temporary location and was stopped. Subsequent runs used workspace-local temporary directories.
- A generated pytest cache had inaccessible Windows permissions. Docker verification used a clean copy of the same current source files under ignored `.local/docker-context`, excluding secrets, dependencies, and test caches. The production image was built by the repository Dockerfile.

## Reproduce

Use Python 3.13, Node 24, PostgreSQL 16, and Chromium. Backend dependencies are locked in `backend/requirements.lock`; frontend dependencies use `frontend/package-lock.json`.

```sh
python -m pytest backend/tests --junitxml=test-results/backend.xml
npm --prefix frontend run lint
npm --prefix frontend run build
npm --prefix frontend run test:e2e
python backend/tools/smoke_ai.py
python backend/tools/smoke_deployment.py --url http://localhost:8000 --live-design
```

Use the backend virtual environment interpreter. Set `FUZE_TEST_DB` and `E2E_DATABASE_URL` to disposable databases. Configure Ollama before the AI smoke test. The deployment smoke creates test accounts and projects; with `--live-design`, it may explicitly accept up to three warnings on its disposable board to exercise question/resume behavior. It logs each such acceptance.

## Remaining external setup and limitations

No Render or Neon deployment was performed: live hosting requires the maintainer's Render/Neon accounts and database secret. The Blueprint, Docker image, readiness endpoint, and instructions are prepared and locally verified. Groq, Gemini, and Z.ai were not called because no provider keys were supplied.

Vite reports a non-failing warning for the roughly 643 kB main JavaScript bundle (about 196 kB gzip). Free-host cold starts, service quotas, external parts availability, and model reliability remain operational constraints. This run did not test long-duration load, multiple replicas, or destructive database disaster recovery.
