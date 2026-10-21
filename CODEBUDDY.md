# Continuity

Agent that designs a PCB bill of materials from a plain-language description and **validates the
whole board as a graph** — voltage, interfaces, pin budget, current, thermal, and stock — then
repairs conflicts. Hackathon entry, submission 14 Aug 2026.

## YOUR SCOPE: `frontend/` ONLY

Do **not** create `backend/`, `engine/`, `mcp/`, or any Python. The backend is written separately
against the same contract. You build against a **mock event emitter**.

## Read before building

| File | Why |
|---|---|
| `docs/specs/2026-08-02-frontend-spec.md` | Your build spec. Start here. |
| `docs/specs/2026-08-02-contract.md` | Event schema + types. Authoritative — if in doubt, it wins. |
| `design/DESIGN.md` | Design tokens: colours, typography, spacing, elevation. |
| `design/stitch/*/code.html` | Stitch HTML exports — the visual source of truth. |
| `design/stitch/*/screen.png` | Rendered reference for each screen. |

Do **not** read `docs/specs/2026-08-02-continuity-design.md` — that is pitch and backend material,
and loading it wastes context.

## Stack

React 19 · TypeScript · Vite · React Router v7 · Tailwind · shadcn/ui · `motion` ·
**Material Symbols Outlined** (the Stitch exports use it — do not substitute lucide)

State: React state plus one `useDesignSession` hook. No Redux, no Zustand, no react-query.

**Tailwind config:** the Stitch exports contain a complete inline `tailwind.config` object in a
`<script id="tailwind-config">` tag. **Copy it verbatim** into `tailwind.config.js` — do not
retype tokens from `DESIGN.md`. `DESIGN.md` is the human-readable rationale; the inline config is
the machine-readable truth. Convert the `cdn.tailwindcss.com` script tag to a proper Vite
Tailwind install; keep the Google Fonts links.

## Commands

```bash
cd frontend
npm run dev        # vite dev server
npm run build      # must pass clean
npx tsc --noEmit   # must pass clean
```

## Conventions

- Design tokens live in `tailwind.config.js` as named colours (`surface`, `surface-container`,
  `primary-container`, `error`, `tertiary-container`, …). **Never hardcode a hex value in a
  component.**
  **One exception:** `src/index.css`. The Stitch `<style>` blocks define utility classes using
  six colours that are not in the token set — `#0B0C0E`, `#16181D`, `#1A1C20`, `#2A2D35`,
  `#3F444E`, `#4ade80`. Port those **verbatim** into `index.css`; do not substitute the nearest
  token, which would change the look.
- **JetBrains Mono** for every part number, voltage, current, price, and table figure.
  **Inter / Hanken Grotesk** for prose and labels. This split is not optional — it is the
  design system's core rule.
- Dark theme only. No light mode, no toggle.
- Immutable state updates — spread, never mutate.
- Files under ~300 lines. Extract when a component grows past that.
- No `console.log` in committed code.
- Named exports except for route components.

## Renaming

The exports carry **three** different placeholder brands. All become **Continuity**:

| In the export | Where | Replace with |
|---|---|---|
| `SIGNALCORE WORKBENCH` | workspace, conflict | `CONTINUITY` |
| `OSCILLO_CORE` | landing header + footer | `CONTINUITY` |
| `PRECISION_INSTRUMENTS_LOGIC` | landing footer copyright | `CONTINUITY` |
| `CYGNUS-H1` | workspace project chip | `CONTINUITY-01` |
| `signalcore_node_grasph.exe` | landing hero window title | `continuity_graph` |
| `SIGNALCORE AUTOMATED` | landing comparison card | `CONTINUITY AUTOMATED` |
| `©2024` | landing footer | `©2026` |

Keep the name `Cygnus-H1` **only** in `design/DESIGN.md`, where it names the design system
itself rather than the product.

## Out of scope

Auth · persistence · multi-user · settings · light mode · mobile · i18n · analytics · tests
beyond `tsc --noEmit` and `npm run build`.

Drop these from the Stitch exports — they imply features we are not building:
**"Deploy to Hardware"** and the **global search bar**.

**Keep** the left icon rail — render it with the current section highlighted and no routing
behind the other icons. It carries the instrument aesthetic.
