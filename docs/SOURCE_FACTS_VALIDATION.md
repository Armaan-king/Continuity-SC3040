# Source-checked research and interface normalization

Implemented and verified locally on 22 October 2026. Production Neon data was not used. Render requires a separate manual deployment.

## Behavior

Generated designs and BOM imports can now turn retrieved manufacturer passages into specifications used by the deterministic rules. Exa retrieves documents; the configured model locates claims; `parts/evidence.py` validates them. Existing supplier data remains useful for pricing, availability and structured specifications.

- The publisher must match a maintained manufacturer/domain mapping. Unknown publishers remain reviewable leads without a model extraction charge.
- Claimed quotations must exist in the retrieved full text. Search highlights alone cannot supply facts. Numeric parameters and values must be adjacent, independently parseable and directly associated with the exact ordering code.
- A supplier-linked manufacturer family datasheet can establish positive interface support. Family evidence does not establish package-dependent GPIO, temperature or voltage numbers.
- Absolute maximum/storage ratings, optional capabilities and conditional claims are excluded. Current and efficiency need operating-mode/load matching and remain review-only in this release.
- Conflicting measurements clear the affected range and produce UNKNOWN findings. Explicit manufacturer denials can invalidate a conflicting positive interface list. Original supplier fields and quoted claims remain available.
- Accepted facts retain their source, supporting passage, identity basis, retrieval timestamp, content hash and validator version. The board, review page, saved revisions and exports use the same facts. Candidate ranking runs again after enrichment.

The existing evidence-card layout now distinguishes source-checked claims used by rules, conflicting claims excluded from rules, and claims requiring review. Source checks validate attribution and parsing; they do not independently measure the component or certify a physical circuit.

## Spelling and formatting

Parser version `verified-4` recognizes IIC, I²C, I₂C, spaced/hyphenated I2C and full-width presentation variants. Unicode minus signs and dashes, microamp units and unit-bearing ranges normalize without replacing their original source strings. USART can supply asynchronous UART capability; UART alone does not establish synchronous USART capability, consistent with [Arm's USART mode-control definitions](https://arm-software.github.io/CMSIS_6/latest/Driver/group__usart__mode__control.html).

CAN-FD remains distinct from CAN. Unknown protocol names and ambiguous decimal-comma values stay unresolved. There is no fuzzy matching of part numbers or guesses that a typo such as `I2G` means I2C.

## Credit controls

Exa retains the existing six-request/$0.12 estimated reservation allowance per run. Extraction defaults to three components per execution, two documents per extraction, 6,000 input characters per document, eight proposed claims and at most 2,048 output tokens. Each extraction gets one model attempt, with no automatic retry. These are application allowances; provider billing remains authoritative.

Retrieval is skipped when the model is unavailable, has exhausted its call/token allowance, or cannot perform another extraction. Completed results are retrieved before checking those limits. Execution caches coalesce concurrent duplicates; durable stages retain completed extraction and part-research results across recovery. A crash before a stage commits can still repeat work; this is not an exactly-once billing guarantee.

For a smaller allowance, set `CONTINUITY_EVIDENCE_MAX_PARTS=1`, `CONTINUITY_RESEARCH_MAX_CALLS=2` and `CONTINUITY_RESEARCH_BUDGET_USD=0.04` on Render. More gaps may remain open. No key replacement or Neon migration is necessary.

## Live check and its limits

One capped check used the independently verified [NXP MC9S08AW60 datasheet](https://www.nxp.com/docs/en/data-sheet/MC9S08AW60.pdf), a minimal controller snapshot and an I²C sensor. It did not perform a complete live supplier search or create a production project.

The actual retrieval and extraction established I²C and SPI support. The sensor's interface result changed from UNKNOWN to PASS. Conditional SCI wording and family-level GPIO evidence stayed review-only.

```text
Exa requests:              1
Exa provider-reported cost: $0.001
Groq HTTP requests:        1
Groq accounted tokens:     3018
Resulting interfaces:      I2C, SPI
Sensor interface check:    PASS
```

The live command returned a nonzero result because its repeat-call assertion exposed a cache bug: after the one-extraction allowance was exhausted, the repeat returned the original unresolved part. No extra provider requests occurred. The cache order was corrected, and an offline regression now runs the same retrieval/extraction/repeat path with an allowance of one, asserting identical facts and one request to each provider. **The corrected cache path was not retested with paid APIs.** [Original live output](validation/source-facts/live.log).

To deliberately repeat this paid smoke later:

```powershell
backend/.venv/Scripts/python.exe backend/tools/check_source_facts.py
```

The script caps itself at two Exa requests and one Groq attempt. It reads existing private environment variables, prints no credentials and writes no database data.

## Offline and browser verification

| Check | Result | Output |
| --- | --- | --- |
| Complete backend suite with disposable PostgreSQL | **1,025 passed, 5 optional live-service skips**, 54.08 seconds | [Backend log](validation/source-facts/backend.log) |
| Browser suite and final affected checks | **34 distinct passing tests**: 33 passed in the full run; all 8 affected/selector checks passed in the final targeted run | [Full run](validation/source-facts/browser-full.log), [final targeted run](validation/source-facts/browser-final-targeted.log) |
| TypeScript and production build | Passed | [Build output](validation/source-facts/build.log) |
| Frontend lint | Passed | `npm run lint` exited 0 |

[Desktop evidence card](validation/source-facts/desktop.png) · [Mobile evidence card](validation/source-facts/mobile.png).

Regression coverage includes source-domain spoofing, absent quotations, wrong variants, family restrictions, adjacent parameter/value binding, absolute maximum headings, conditional measurements, conflicting documents, explicit interface denials, partial provider failures, exhausted budgets, durable stage recovery, duplicate coalescing, source citations and reranking after research.

Database checks verify preservation through revision edits and JSON exports without additional provider calls. Browser checks use controlled evidence fixtures with the real app and disposable PostgreSQL, and verify accepted/conflicting/pending states, exports, reloads, navigation and 390 px mobile layout. They do not represent additional live Exa calls.

Initial verification caught an incorrect test assumption about the export JSON shape, repeated guidance-list keys, an ambiguous existing Memory test selector, and the Unicode CAN-FD hyphen bug. These were corrected before the final checks. The frontend build retains its existing non-failing bundle-size advisory; browser output retains terminal-color warnings.

## Deployment

Manually deploy the new Git commit on Render. Keep the current Groq and Exa keys and Neon URL. New runs use the source-fact pipeline. Historical revisions remain unchanged, and requirement edits revalidate saved evidence without launching paid research. Start a fresh run to research an older design using the new pipeline.
