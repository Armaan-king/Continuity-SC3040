# Upgrading to FUZE

FUZE is the product name, Python package (`fuze`), logger name and configuration prefix (`FUZE_*`). The GitHub repository is [Armaan-king/FUZE](https://github.com/Armaan-king/FUZE).

## Existing Render and Neon installation

1. Manually deploy this Git revision to the **existing Render service** using your current deployment method. A Git push does not deploy this service.
2. Keep `DATABASE_URL`, `GROQ_API_KEY`, `GEMINI_API_KEY` and `EXA_API_KEY` unchanged. No new provider account, key or Neon database is needed.
3. Existing `CONTINUITY_*` settings continue to work. When convenient, rename each to `FUZE_*`, retaining its value: for example, `CONTINUITY_LLM_MODEL` becomes `FUZE_LLM_MODEL`. If both are set, the `FUZE_*` value wins, even when empty. Remove the old duplicate after saving the new one.
4. The Dockerfile already starts `fuze.server:app`. An existing `continuity.server:app` override remains supported. When updating that override, use `fuze.server:app` or `python -m fuze --host 0.0.0.0 --port $PORT`.
5. Startup applies migration `005_fuze_brand` automatically. It renames only the generated “Welcome to Continuity” walkthrough projects. Accounts, sessions, user project names, component facts, saved runs and revisions remain intact. Do not edit older migrations or clear Neon tables.
6. Check `/api/ready`, `/api/runtime`, sign in and reopen a saved design. Runtime shows configuration; a successful provider receipt is the separate proof of a live AI call.

The public address remains [continuity-sc3040.onrender.com](https://continuity-sc3040.onrender.com). That hostname belongs to the existing Render service; changing the name in source code does not rename it. Update links only after a replacement hostname is assigned and verified. The free service may sleep between visits.

The new `render.yaml` names newly provisioned services `fuze-sc3040`. Do not apply it as a new Blueprint merely to rename an existing deployment.

## Local development

Reinstall the renamed editable package once:

```sh
python -m pip install --no-deps -e backend
python -m fuze --port 8000
```

Use your backend virtual-environment Python. The transitional `python -m continuity` entry point remains available for existing scripts. Old checkpoint class names resolve to the same canonical FUZE classes; newly saved checkpoints use `fuze.*`. Old session cookies are accepted and promoted to `fuze_session` on authenticated responses; logout revokes both cookie names.

New Docker Compose installations use database/user `fuze` and volume `fuze-data`. **If you already have the original local Compose database**, preserve its volume and credentials with:

```sh
docker compose -f compose.yaml -f compose.legacy.yaml up --build
```

The compatibility override defaults to the original `b-main_continuity-data` volume. If the original Compose project had another name, set `COMPOSE_PROJECT_NAME` to that original name before running the command. The volume is external, so a missing or misspelled name fails instead of silently creating an empty replacement. Never run `down -v` to perform a branding upgrade. Existing custom database URLs need no rename.

## Historical records

Current application text, configuration examples, documentation branding and walkthrough media use FUZE. Historical test output, immutable saved revision evidence, original migration SQL, factual old branch names/URLs in SWE records and the compatibility aliases retain their original identifiers. These are not a second product name; rewriting them would invalidate audit history, checksums or existing installations.

The SWE PDFs retain their original dates, authorship and engineering content, with project branding updated. Generic English uses of “continuity” retain their meaning.
