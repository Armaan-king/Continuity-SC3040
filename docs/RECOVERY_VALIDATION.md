# Component recovery and web alternatives

Verified locally on 22 October 2026. No paid Groq or Exa requests were made for this change. PostgreSQL tests used a disposable local database; production Neon was not accessed.

## Behavior

An automatic retry limit now pauses for further decisions instead of claiming that the requirements must be relaxed. **Choose a different component** reaches every slot, including controllers and power supplies outside the immediate conflict. Existing catalogue candidates, retrieved web alternatives and up to six previous selections per slot can be tried explicitly. Every swap reruns the whole board's checks and invalidates previous waivers. An unsuccessful manual choice remains selected, with the failure and recovery options visible; the automatic reviewer does not immediately overwrite it. Manual attempts are not blocked by the automatic repair count.

Empty searches also pause. Continuing without a component leaves an unresolved function. A missing controller leads directly to controller addition/replacement or redesign; an unverified controller role remains UNKNOWN. Controller additions are connected to a regulated rail and included in subsequent sourcing and validation.

Written revision instructions use a bounded structural planning request. Confirmed numeric requirements are preserved, required pinned functions cannot disappear, and invalid, unavailable or unchanged proposals leave the original board intact with an explanation. Applying a valid plan replaces the graph instead of merging obsolete nodes into it. Existing selections are reused unless the plan requests replacements, changes their category or needs a controller with the correct role; all checks run again. Compatibility is decided by the rules, not the planner's explanation.

## Exa and cost controls

The integration follows the installed `build-with-exa` skill's native Search and Contents contracts. Explicit web research uses `type: auto`, highlights and a small structured output schema, followed by one Contents request for at most two source documents. It shares the existing persisted Exa allowance, including the six-request default and estimated dollar budget. Repeated selection of already retrieved alternatives does not require another search.

An offered product must have an exact identifier and a quotation found in retrieved full text at a URL returned by search. A search result is still a candidate, not a compatibility verdict. External products can be selected without a JLCPCB listing; missing stock and price remain unknown. The existing evidence-admission pipeline checks electrical claims after selection. Broad controller classification requires a direct positive description of the exact product on a recognized manufacturer domain; merely mentioning a controller is insufficient.

The search comparison presents identity passages and links. It is not a complete electrical comparison of every datasheet before selection. Unrecognized manufacturer domains, ambiguous identities, failed retrievals, unsupported measurements and exhausted budgets remain unresolved. Native Exa responses were exercised through an HTTP mock; live provider quality and availability were not tested in this run.

## Verification

The final backend suite passed **1,072 tests**, with **5 optional live-service skips**, in **59.87 seconds**. It includes real PostgreSQL integration tests and deterministic provider fixtures. The recovery regressions drive the actual checkpointed graph through an incompatible selection, another user decision, rollback to the prior component, and a compatible replacement. They verify that failures return, requirements stay fixed, other selections survive, and explicit swaps work beyond the automatic retry limit. Additional tests cover failed searches, unavailable/malformed redesign, source identity and quotation rejection, budget exhaustion, previous research reuse and question restoration.

Commands from the repository root:

```powershell
$env:FUZE_TEST_DB = '<disposable local PostgreSQL connection>'
backend/.venv/Scripts/python.exe -m pytest backend/tests -o addopts='' -p no:cacheprovider -q
```

From `frontend`:

```powershell
npm.cmd run build
npm.cmd run lint
$env:E2E_DATABASE_URL = '<disposable local PostgreSQL connection>'
npm.cmd run test:e2e -- e2e/recovery.spec.ts e2e/cancellation.spec.ts e2e/runtime.spec.ts e2e/consistency.spec.ts
```

See [backend output](validation/recovery/backend.log), [browser output](validation/recovery/browser.log), [build](validation/recovery/build.log) and [lint](validation/recovery/lint.log). The final browser run passed **15 Chromium tests** in **51.2 seconds**. These tests cover source cards, explicit selection, repeated swaps, replacement graph nodes, written instructions, cancellation, saved projects and provider warnings. The comparison screen was also visually inspected. The two new recovery browser tests initially used incorrect graph fixture expectations; those were corrected before the final run.

The production build and lint pass. The pre-existing nonfatal JavaScript bundle-size advisory remains. Five optional live-service tests are skipped. Early backend failures included expectations for the old echo-only guidance behavior, a controller-category assumption affecting older plans, tuple/list equality in checkpoint fixtures, and an unnecessary empty field in restored legacy questions; these were addressed before the final suite.

## Deployment and scope

Manually deploy the new Git commit on Render. No additional environment variables or Neon migration are required. Existing Exa and LLM limits remain in effect; increasing them is optional and may increase charges.

These controls operate in paused design runs. Completing a run means its modeled checks have finished; it does not certify a working physical device. UNKNOWN findings, missing parts and accepted failures stay visible. Unsupported module-level constraints, timing, pin-by-pin wiring, PCB layout and real operating measurements still require engineering review. The app does not guarantee that an arbitrary specification has a feasible solution.
