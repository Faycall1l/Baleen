import Graph from 'graphology';
import forceAtlas2 from 'graphology-layout-forceatlas2';

import type { GraphData } from '../types';
import { radialLayout } from './radial';

// Iterations for the one-shot pass. Enough for ~300 nodes to settle from a
// radial start; the engine is O(n^2) per iteration without Barnes-Hut, so
// large graphs flip to Barnes-Hut (and huge ones skip FA2 entirely).
const ITERATIONS = 100;
const BARNES_HUT_ORDER = 400;
const MAX_FA2_ORDER = 1200;

/**
 * One-shot ForceAtlas2 layout with deterministic seeds (R19).
 *
 * The old pure-radial map parked every node on a ring sector, so all edges
 * became long chords across the circle — the mesh. FA2 pulls connected
 * nodes together and pushes the rest apart, which is what dissolves it.
 *
 * Static by construction, like the radial map it replaces: the engine runs
 * a fixed iteration count synchronously and is never left running, so the
 * canvas never simulates. Deterministic because FA2 itself has no
 * randomness — every node starts from its radial seed, so the same payload
 * always yields the same map. Runs only on first paint and staged upgrades
 * (see useGraphSync); steady-state polls keep the centroid fallback.
 */
export function forceLayout(data: GraphData): Map<string, { x: number; y: number }> {
  const seeds = radialLayout(data);
  if (data.nodes.length === 0) return seeds;
  // FA2 is quadratic per iteration: beyond this size the synchronous pass
  // would block the main thread, so huge graphs keep the radial seeds.
  if (data.nodes.length > MAX_FA2_ORDER) return seeds;

  const g = new Graph({ type: 'directed', allowSelfLoops: false });
  for (const n of data.nodes) {
    const s = seeds.get(n.id) ?? { x: 0, y: 0 };
    g.addNode(n.id, { x: s.x, y: s.y });
  }
  for (const l of data.links) {
    // Self-loops are zero-distance edges and destabilize the engine.
    if (l.source === l.target) continue;
    if (!g.hasNode(l.source) || !g.hasNode(l.target)) continue;
    const key = `${l.source}>${l.target}:${l.type}`;
    if (!g.hasEdge(key)) g.addEdgeWithKey(key, l.source, l.target);
  }

  const settings = forceAtlas2.inferSettings(g);
  if (g.order > BARNES_HUT_ORDER) settings.barnesHutOptimize = true;
  const positions = forceAtlas2(g, { iterations: ITERATIONS, settings });
  const out = new Map<string, { x: number; y: number }>();
  for (const [id, pos] of Object.entries(positions)) {
    out.set(id, { x: pos.x, y: pos.y });
  }
  return out;
}
