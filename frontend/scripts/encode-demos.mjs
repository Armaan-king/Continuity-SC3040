/** Encode successful local captures for GitHub. Requires FFmpeg with libx264. */
import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { readFile, stat, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = fileURLToPath(new URL('../../', import.meta.url))
const work = path.join(root, '.local/readme-recording')
const output = path.join(root, 'docs/media')
const ffmpeg = process.env.FFMPEG ?? 'ffmpeg'
const report = JSON.parse(await readFile(path.join(work, 'capture.json'), 'utf8'))
assert.equal(report.clips.length, 2, 'Finish both UI recordings before publishing media')

function encode(args) {
  const result = spawnSync(ffmpeg, ['-hide_banner', '-y', ...args], { encoding: 'utf8', maxBuffer: 8_000_000 })
  if (result.error) throw result.error
  assert.equal(result.status, 0, result.stderr)
  return result.stderr
}

for (const clip of report.clips) {
  assert.equal(clip.result, 'PASS')
  const filename = `${clip.name}.mp4`
  const target = path.join(output, filename)
  encode(['-ss', String(clip.trimStart), '-i', path.join(work, `${clip.name}.webm`),
    '-an', '-vf', 'fps=24', '-c:v', 'libx264', '-preset', 'medium', '-crf', '22',
    '-pix_fmt', 'yuv420p', '-movflags', '+faststart', '-map_metadata', '-1', target])
  const validation = encode(['-i', target, '-f', 'null', '-'])
  clip.file = filename
  clip.duration = validation.match(/Duration: ([\d:.]+)/)?.[1]
  clip.bytes = (await stat(target)).size
  clip.chapters = clip.chapters.map((chapter) => ({ ...chapter, seconds: Math.max(0, chapter.seconds - clip.trimStart) }))
  delete clip.trimStart
  console.log(`PASS: ${filename}, ${clip.duration}, ${clip.bytes} bytes; full video decoded`)
}

const walkthrough = report.clips.find((clip) => clip.name === 'guided-walkthrough')
const start = Math.max(0, walkthrough.chapters.find((chapter) => chapter.title === 'It shows its working').seconds - 1)
const end = walkthrough.chapters.find((chapter) => chapter.title === 'Memory keeps the finding').seconds + 2
encode(['-ss', String(start), '-t', String(end - start), '-i', path.join(output, walkthrough.file),
  '-vf', 'fps=6,scale=960:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128[p];[b][p]paletteuse=dither=bayer:bayer_scale=3',
  '-loop', '0', path.join(output, 'walkthrough.gif')])
report.preview = { file: 'walkthrough.gif', source: walkthrough.file, startSeconds: start,
  durationSeconds: end - start, width: 960, fps: 6, bytes: (await stat(path.join(output, 'walkthrough.gif'))).size }
await writeFile(path.join(output, 'capture.json'), JSON.stringify(report, null, 2) + '\n')
console.log('PASS: walkthrough.gif and capture.json written')
