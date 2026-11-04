/* Pure helpers for the artboard-D polish pass (no React imports, so
 * Node exercises the same code the UI runs). */

import { STAGES } from './stages.js'

/* Landing domino strip: one tile per chain module, root first. Mirrors
 * the graph tiles (decision tile carries the first-break site, every
 * other tile its outgoing hop toward the break). */
export function stripTiles(chain, evidence, firstBreak) {
  if (!Array.isArray(chain) || chain.length === 0) return []
  const fromHop = {}
  for (const hop of evidence || []) {
    if (Array.isArray(hop) && hop.length >= 4 && !(hop[0] in fromHop)) {
      fromHop[hop[0]] = hop
    }
  }
  return chain.map((module, i) => {
    if (i === chain.length - 1) {
      return {
        module,
        role: 'decision',
        file: firstBreak?.path ?? null,
        line: firstBreak?.line ?? null,
      }
    }
    const hop = fromHop[module]
    return {
      module,
      role: i === 0 ? 'root' : 'link',
      file: hop?.[2] ?? null,
      line: hop?.[3] ?? null,
    }
  })
}

/* Final tip angles for the landing strip, root first. */
const TIP_ANGLES = [0, 4, 8, 13, 19]

export function tipAngle(i) {
  return TIP_ANGLES[i % TIP_ANGLES.length]
}

/* Grounding-band status chip. The API proves every line except Muse
 * Code, so provenance decides: recorded use is IN USE, the
 * investigator agent's tools (M4's surface) read M4, build-time
 * tooling reads BUILD. */
export function capabilityStatus(cap) {
  if (cap.id === 'muse-code') return 'BUILD'
  if (cap.id === 'tool-calling') return 'M4'
  return 'IN USE'
}

export function countReplayable(presets) {
  return (presets || []).filter((p) => p.has_recording).length
}

/* Mini rail for the landing run preview. A done recording ran every
 * stage end to end; only the gated M4/M5 outputs can still be
 * missing, which reads skipped exactly like the run rail. */
const GATED = {
  investigate: { arrivesIn: 'M4', presentKey: 'investigation' },
  fix: { arrivesIn: 'M5', presentKey: 'fix' },
}

export function miniRailStates(status, present) {
  const states = {}
  for (const stage of STAGES) {
    const gate = GATED[stage]
    if (status !== 'done') {
      states[stage] = { state: 'pending' }
    } else if (gate && !present?.[gate.presentKey]) {
      states[stage] = { state: 'skipped', reason: `not available yet (${gate.arrivesIn})` }
    } else {
      states[stage] = { state: 'done' }
    }
  }
  return states
}
