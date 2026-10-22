# FUZE rename verification

Validated locally on 22 October 2026. This update changes the product identity across the repository while retaining compatibility with existing deployments and saved data. It does not claim that the manually deployed Render instance is already updated.

## Application and upgrade checks

| Check | Result |
| --- | --- |
| Complete backend suite with disposable PostgreSQL | 1,134 passed; 5 live-service tests skipped |
| Chromium browser suite | 41 passed |
| Frontend lint and production build | Passed; existing non-failing bundle-size warning remains |
| New and legacy Compose configuration | Both parse successfully |
| Existing SQL migration contents | All four previously applied scripts unchanged |
| Installed Python package and legacy Render entry point | `fuze==0.1.0`; both server entry points resolve to the same app |
| README local links and anchors | 64 checked; no missing targets |

[Backend output](validation/fuze/backend.log), [browser output](validation/fuze/browser.log), [media output](validation/fuze/media.log).

The upgrade tests check old Render provider settings, explicit new-name precedence including empty values, the previous local Ollama model alias, old checkpoint class identity and deserialization, promotion of old session cookies, logout revocation of both cookie names, and the welcome-project migration. The migration test also verifies that user-created project names and sessions remain intact.

Tests and recording used disposable local PostgreSQL databases. No production Neon data was accessed. No live Groq or Gemini inference was needed, and the research recording reused an existing Exa response with zero new Exa requests.

## Media and documents

All four README videos and both animated previews were re-recorded or regenerated from the FUZE interface. FFmpeg decoded every published frame successfully. The scripts verify onboarding, the design workflow, exports, saved-board reopening, review questions, revision edits, restoration and declared signal connections. Published capture metadata describes the actual input data and results.

The live supplier recording placed three of three parts with zero failures, six unknown checks and no accepted warnings. Unknown checks remain visible. It used basic planning, not AI inference. The reference research board retains its existing conflict and unverified source labels.

Thirteen SWE PDFs were updated. Every page whose text changed was rendered (193 pages), with representative covers, body pages, headers and tables visually inspected. Automated checks compared normalized character inventories before and after the name substitution to catch accidentally removed surrounding content. Existing dates, authorship and engineering claims were retained. Spreadsheets contained no old-name matches and were left unchanged.

The raster use-case diagrams and embedded schedule/release-chart labels were rebranded with the built-in image tool, then inspected before insertion. The standalone diagram is [Use_Case_Diagram.png](../Lab%201%20Deliverables/Use-Case%20Model/Use_Case_Diagram.png); the other outputs are embedded in the corresponding PDFs. The edit instructions were to replace only the old product name with `FUZE`, preserving all nodes, actors, relationships, labels, dates and chart data. This is a branding update to historical illustrations, not new engineering evidence.

Old unused prototype screenshots and the prototype icon were removed. Current workspace screenshots and validation-page examples were captured again. Original test logs and immutable migration text remain historical records; the [upgrade guide](FUZE_UPGRADE.md#historical-records) lists deliberate exceptions to the rename.

## Issues caught during verification

- The initial backend run found a test fixture that cleared only the new environment prefix and a migration-count assertion still expecting four migrations. Both were updated; the full suite now passes.
- The recording scripts needed selectors adjusted for current project capitalization and nested document details. Failed takes were discarded; published recordings come from successful runs.
- PDF checks caught small-caps headings split across font spans and overlapping punctuation. The edit process was corrected to preserve complete lines, and text-preservation checks passed before saving final PDFs.
- Provider quota/availability and the production deployment were not retested because this is a branding migration. Existing keys and legacy configuration remain supported.
