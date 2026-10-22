/** Encode and decode-check the successful revision/evidence recordings for GitHub. */
import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { readFile, stat, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = fileURLToPath(new URL('../../', import.meta.url))
const work = path.join(root, '.local/review-recording')
const output = path.join(root, 'docs/media')
const ffmpeg = process.env.FFMPEG ?? 'ffmpeg'
const report = JSON.parse(await readFile(path.join(work, 'capture.json'), 'utf8'))
assert.deepEqual(report.clips.map(clip => clip.name), ['design-review', 'research-connections'])
assert(!('credentials' in report), 'Never publish the recording account credentials')

function encode(args) {
  const result = spawnSync(ffmpeg, ['-hide_banner', '-y', ...args], { encoding: 'utf8', maxBuffer: 8_000_000 })
  if (result.error) throw result.error
  assert.equal(result.status, 0, result.stderr)
  return result.stderr
}

for (const clip of report.clips) {
  assert.equal(clip.result, 'PASS')
  const target = path.join(output, `${clip.name}.mp4`)
  encode(['-ss', String(clip.trimStart), '-i', path.join(work, `${clip.name}.webm`),
    '-an', '-vf', 'fps=24', '-c:v', 'libx264', '-preset', 'medium', '-crf', '22',
    '-pix_fmt', 'yuv420p', '-movflags', '+faststart', '-map_metadata', '-1', target])
  const decoded = encode(['-i', target, '-f', 'null', '-'])
  clip.file = `${clip.name}.mp4`
  clip.duration = decoded.match(/Duration: ([\d:.]+)/)?.[1]
  clip.bytes = (await stat(target)).size
  clip.chapters = clip.chapters.map(chapter => ({ ...chapter, seconds: Math.max(0, chapter.seconds - clip.trimStart) }))
  delete clip.trimStart
  console.log(`PASS: ${clip.file}, ${clip.duration}, ${clip.bytes} bytes; all frames decoded`)
  // Sample each chapter for visual review without altering the production app.
  for (let index = 0; index < clip.chapters.length; index++) {
    encode(['-ss', String(clip.chapters[index].seconds + 1), '-i', target, '-frames:v', '1',
      '-update', '1', path.join(work, `${clip.name}-chapter-${index + 1}.png`)])
  }
}

const review = report.clips[0]
const start = Math.max(0, review.chapters.find(item => item.title === 'Edit a requirement and revalidate').seconds - 1)
const end = review.chapters.find(item => item.title === 'Compare revisions without losing the original design').seconds + 3
encode(['-ss', String(start), '-t', String(end - start), '-i', path.join(output, review.file),
  '-vf', 'fps=5,scale=960:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128[p];[b][p]paletteuse=dither=bayer:bayer_scale=3',
  '-loop', '0', path.join(output, 'design-review.gif')])
encode(['-i', path.join(output, 'design-review.gif'), '-f', 'null', '-'])
report.preview = { file: 'design-review.gif', source: review.file, startSeconds: start,
  durationSeconds: end - start, width: 960, fps: 5, bytes: (await stat(path.join(output, 'design-review.gif'))).size }
await writeFile(path.join(output, 'review-capture.json'), JSON.stringify(report, null, 2) + '\n')
console.log('PASS: animated preview and sanitized review-capture.json written')
