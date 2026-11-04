/* Pure graph derivations (no React/Cytoscape imports, so Node can
 * exercise the same code the UI runs). */

export const GRAPH_MAX_ZOOM = 1.2
export const GRAPH_MIN_FIT_ZOOM = 0.75

/* Non-chain edge visibility: hidden until their node is selected, so
 * dense graphs read chain-first. Edges to filtered-out endpoints stay
 * hidden (no dangling lines). Returns [display, lit]. */
export function describeEdge({ isChain, endsVisible, selected, sourceId, targetId }) {
  if (!endsVisible) return ['none', false]
  if (isChain) return ['element', false]
  const lit = selected != null && (sourceId === selected || targetId === selected)
  return [lit ? 'element' : 'none', lit]
}
