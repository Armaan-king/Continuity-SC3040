# Missing controller interface evidence

Verified locally on 22 October 2026. This update fixes a false interface conflict between an MC9S08AW60CFGE controller and an I²C peripheral. Production Neon data was not used.

## Diagnosis

A public supplier lookup for MC9S08AW60CFGE (C5193889) returned clock, temperature and generic feature fields, but no interface parameter. The legacy capability rule treated the empty controller interface list as proof that no compatible bus existed.

The [NXP MC9S08AW60 datasheet](https://www.nxp.com/docs/en/data-sheet/MC9S08AW60.pdf) documents IIC support: Table 1-2 lists it across the package variants, and Chapter 13 describes the module. This was an independent manufacturer lookup; it does not establish which documents the user's saved Exa run retrieved. A second parser issue dropped conventional IIC/I²C/TWI spellings when those were present in explicit supplier parameters.

## Changes and checks

- A controller with missing interface data receives an UNKNOWN finding. A peripheral without a demonstrated compatible controller also remains UNKNOWN when controller capabilities are missing. This does not trigger the hard-conflict repair/waiver path.
- Explicit supplier interface parameters share the engine's canonical bus vocabulary, retain original source text, and reject negated or optional capabilities. A documented incompatible interface list still fails.
- Missing controller capabilities remain visible before peripherals are placed, so candidate ranking does not reward an empty record.
- Missing controller/peripheral interfaces are prioritized in Exa research questions. Retrieved text remains unverified; it does not silently populate rule inputs.
- Parser version `verified-3` invalidates old normalized part/stage results. New revisions record ruleset `2026-10-interfaces-4`; historical revisions are preserved.

The regression suite includes the exact supplier specification fields, positive aliases and provenance, negative/unknown values, known mismatches, multiple-controller ambiguity, explicit signal nets, graph routing, review tasks, targeted research and stale normalization-stage reuse.

## Verification

Full backend suite, including integration tests against disposable local PostgreSQL:

```text
950 passed, 5 skipped in 31.64s
```

The five skips are optional external-service tests. [Complete test output](validation/interface-backend.log).

```powershell
# Both URLs must point to a disposable test database, never production Neon.
$env:CONTINUITY_TEST_DB = 'postgresql://USER:PASSWORD@127.0.0.1:55439/engine_tests'
$env:DATABASE_URL = $env:CONTINUITY_TEST_DB
$env:CONTINUITY_LIVE = '0'
backend/.venv/Scripts/python.exe -m pytest backend/tests -p no:cacheprovider --basetemp .local/pytest-interface-check -ra
```

One public supplier lookup and the manufacturer datasheet were inspected for diagnosis. The regression suite used controlled responses; no new paid Exa or Groq requests were made. No frontend layout changes were made, so browser videos and screenshots were not re-recorded.

## Deployment and limits

Manually deploy the updated commit on Render. No environment-variable or Neon schema changes are required. A run already paused at the old conflict retains its saved state: stop it and start a fresh run after deployment. Historical reports are not rewritten.

This update prevents unsupported claims of incompatibility; it does not establish physical connectivity or automatically verify a component's datasheet. Exa research still matches exact MPN text and can omit applicable family-level documents. A future extraction stage needs part/variant applicability, supporting passages, operating conditions and conflict handling before promoting research into specifications.
