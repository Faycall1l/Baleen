import Graph from 'graphology';
import forceAtlas2 from 'graphology-layout-forceatlas2';

import type { GraphData } from '../types';
import { radialLayout } from './radial';

// Iterations for the one-shot pass. Fixed at FULL_ITERATIONS for small
// graphs; beyond that the budget scales down so total engine work stays
// roughly constant (~30k node-iterations) instead of growing quadratically
// with order. The engine is O(n^2) per iteration without Barnes-Hut, so
// large graphs flip to Barnes-Hut (and huge ones skip FA2 entirely).
const FULL_ITERATIONS = 100;
const MIN_ITERATIONS = 30;
const BARNES_HUT_ORDER = 400;
const MAX_FA2_ORDER = 1200;

/** Fixed work budget: small graphs settle fully, large ones stay fast. */
export function iterationsForOrder(order: number): number {
  if (order <= 300) return FULL_ITERATIONS;
  return Math.max(MIN_ITERATIONS, Math.floor(30000 / order));
}

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
export function forceLayout(
  data: GraphData,
  existing?: Map<string, { x: number; y: number }>,
): Map<string, { x: number; y: number }> {
  const seeds = radialLayout(data);
  // Survivors keep their live spots as seeds: a staged upgrade (small
  // window -> full snapshot) settles around the map the user is already
  // reading instead of jumping to a fresh arrangement.
  if (existing) {
    for (const [id, pos] of existing) {
      if (seeds.has(id) && Number.isFinite(pos.x) && Number.isFinite(pos.y)) {
        seeds.set(id, { x: pos.x, y: pos.y });
      }
    }
  }
  if (data.nodes.length === 0) return seeds;
  // FA2 is quadratic per iteration: beyond this size the synchronous pass
  // would block the main thread, so huge graphs keep the radial seeds.
  if (data.nodes.length > MAX_FA2_ORDER) return seeds;
  // The layout must never blank the canvas: any engine failure (degenerate
  // input, numerical blowup) falls back to the radial seeds.
  try {
    return runForceAtlas2(data, seeds, iterationsForOrder(data.nodes.length));
  } catch {
    return seeds;
  }
}

function runForceAtlas2(
  data: GraphData,
  seeds: Map<string, { x: number; y: number }>,
  iterations: number,
): Map<string, { x: number; y: number }> {
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
  const positions = forceAtlas2(g, { iterations, settings });
  const out = new Map<string, { x: number; y: number }>();
  for (const [id, pos] of Object.entries(positions)) {
    out.set(id, { x: pos.x, y: pos.y });
  }
  return withFiniteFallback(out, seeds);
}

/**
 * Belt and braces behind the try/catch: any non-finite engine output
 * (NaN/Infinity from a degenerate component) falls back to that node's
 * radial seed instead of poisoning the GPU batches downstream.
 */
export function withFiniteFallback(
  positions: Map<string, { x: number; y: number }>,
  seeds: Map<string, { x: number; y: number }>,
): Map<string, { x: number; y: number }> {
  const out = new Map<string, { x: number; y: number }>();
  for (const [id, pos] of positions) {
    if (Number.isFinite(pos.x) && Number.isFinite(pos.y)) out.set(id, pos);
    else out.set(id, seeds.get(id) ?? { x: 0, y: 0 });
  }
  return out;
}
