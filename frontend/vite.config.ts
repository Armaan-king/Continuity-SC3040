import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { readFileSync } from 'node:fs'

const walkthroughReplay = readFileSync(
  new URL('../backend/continuity/api/walkthrough.jsonl', import.meta.url),
  'utf8',
)

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  define: {
    __WALKTHROUGH_REPLAY__: JSON.stringify(walkthroughReplay),
  },
})
