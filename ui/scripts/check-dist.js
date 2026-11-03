// Fails when ui/dist is older than ui/src (M6 plan point 5): judges must
// never get a stale committed build. Run from ui/ before every commit
// that touches ui/src — or just run `npm run build` first.
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), '..')

function newestMtime(dir) {
  let newest = 0
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) {
      newest = Math.max(newest, newestMtime(full))
    } else {
      newest = Math.max(newest, fs.statSync(full).mtimeMs)
    }
  }
  return newest
}

const srcFiles = ['src', 'index.html', 'package.json', 'vite.config.js']
const srcNewest = Math.max(
  ...srcFiles.map((f) => {
    const p = path.join(root, f)
    if (!fs.existsSync(p)) return 0
    return fs.statSync(p).isDirectory() ? newestMtime(p) : fs.statSync(p).mtimeMs
  }),
)
const built = path.join(root, 'dist', 'index.html')
const builtMtime = fs.existsSync(built) ? fs.statSync(built).mtimeMs : 0

if (srcNewest > builtMtime) {
  console.error('ui/dist is stale (older than ui/src): run `npm run build` first')
  process.exit(1)
}
console.log('ui/dist is fresh')
