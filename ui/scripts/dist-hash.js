// Guards the committed ui/dist against stale builds (M6 plan point 5).
//
// Compares CONTENT, not mtimes (git doesn't preserve those, so mtime
// checks cry stale on every fresh clone). `npm run build` writes the
// hash; `npm run check-dist` verifies it:
//
//   node scripts/dist-hash.js --write   # after vite build
//   node scripts/dist-hash.js           # fail if dist is stale
import crypto from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), '..')
const HASH_FILE = path.join(root, 'dist', '.src-hash')
const SOURCES = ['src', 'index.html', 'package.json', 'vite.config.js']

function collect(dir, base = '') {
  const out = []
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const rel = path.join(base, entry.name)
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) out.push(...collect(full, rel))
    else out.push(rel)
  }
  return out
}

export function sourceHash() {
  const files = []
  for (const name of SOURCES) {
    const full = path.join(root, name)
    if (!fs.existsSync(full)) continue
    if (fs.statSync(full).isDirectory()) {
      for (const rel of collect(full)) files.push(path.join(name, rel))
    } else {
      files.push(name)
    }
  }
  files.sort()
  const hash = crypto.createHash('sha256')
  for (const rel of files) {
    hash.update(rel)
    hash.update('\0')
    hash.update(fs.readFileSync(path.join(root, rel)))
    hash.update('\0')
  }
  return hash.digest('hex')
}

const write = process.argv.includes('--write')
if (write) {
  fs.mkdirSync(path.dirname(HASH_FILE), { recursive: true })
  fs.writeFileSync(HASH_FILE, `${sourceHash()}\n`)
  console.log('wrote ui/dist/.src-hash')
} else {
  const current = sourceHash()
  const stored = fs.existsSync(HASH_FILE) ? fs.readFileSync(HASH_FILE, 'utf-8').trim() : null
  if (stored !== current) {
    console.error('ui/dist is stale (content hash mismatch): run `npm run build` first')
    process.exit(1)
  }
  console.log('ui/dist is fresh')
}
