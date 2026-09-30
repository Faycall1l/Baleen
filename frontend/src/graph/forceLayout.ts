import Graph from 'graphology';
import forceAtlas2 from 'graphology-layout-forceatlas2';

import type { GraphData } from '../types';
import { hashStr } from './geometry';
import { radialLayout, radialRadius } from './radial';

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
  // Halo after centering so the ring is measured in final frame coordinates.
  // Sanitize first: centering a NaN poisons the whole map. Normalize last
  // so every payload opens at the same framing regardless of engine drift.
  return normalizeRadius(
    liftIsolates(centerOnCentroid(withFiniteFallback(out, seeds)), data, seeds),
    radialRadius(data.nodes.length),
  );
}

/**
 * Connected components over the payload links, largest first, ties broken
 * by smallest node id: isolates become singleton components. Deterministic
 * BFS — no dependency, no randomness — so component packing (F9) is stable.
 */
export function connectedComponents(data: GraphData): string[][] {
  const adj = new Map<string, Set<string>>();
  for (const n of data.nodes) adj.set(n.id, new Set());
  for (const l of data.links) {
    if (!adj.has(l.source) || !adj.has(l.target)) continue;
    adj.get(l.source)!.add(l.target);
    adj.get(l.target)!.add(l.source);
  }
  const seen = new Set<string>();
  const components: string[][] = [];
  for (const n of data.nodes) {
    if (seen.has(n.id)) continue;
    const component: string[] = [];
    const queue = [n.id];
    seen.add(n.id);
    while (queue.length > 0) {
      const id = queue.pop()!;
      component.push(id);
      const neighbors = [...(adj.get(id) ?? [])].sort();
      for (const nb of neighbors) {
        if (!seen.has(nb)) {
          seen.add(nb);
          queue.push(nb);
        }
      }
    }
    components.push(component.sort());
  }
  components.sort((a, b) => b.length - a.length || (a[0] < b[0] ? -1 : 1));
  return components;
}
/**
 * Scale the map so its extent matches a target radius: FA2 output scale
 * drifts with size and settings, which used to reframe the camera per
 * payload. Uniform scaling preserves every angle and ratio — only the
 * framing stabilizes.
 */
export function normalizeRadius(
  positions: Map<string, { x: number; y: number }>,
  targetRadius: number,
): Map<string, { x: number; y: number }> {
  let maxR = 0;
  for (const pos of positions.values()) {
    maxR = Math.max(maxR, Math.hypot(pos.x, pos.y));
  }
  if (maxR === 0) return positions;
  const scale = targetRadius / maxR;
  const out = new Map<string, { x: number; y: number }>();
  for (const [id, pos] of positions) {
    out.set(id, { x: pos.x * scale, y: pos.y * scale });
  }
  return out;
}
export function centerOnCentroid(
  positions: Map<string, { x: number; y: number }>,
): Map<string, { x: number; y: number }> {
  if (positions.size === 0) return positions;
  let sx = 0;
  let sy = 0;
  for (const pos of positions.values()) {
    sx += pos.x;
    sy += pos.y;
  }
  const cx = sx / positions.size;
  const cy = sy / positions.size;
  const out = new Map<string, { x: number; y: number }>();
  for (const [id, pos] of positions) {
    out.set(id, { x: pos.x - cx, y: pos.y - cy });
  }
  return out;
}

/**
 * Zero-degree nodes collapse into the gravity well and read as noise, so
 * lift them onto a deterministic halo ring just outside the connected
 * mass: same angle hash the radial map uses, radius 15% past the
 * connected extent.
 */
export function liftIsolates(
  positions: Map<string, { x: number; y: number }>,
  data: GraphData,
  seeds: Map<string, { x: number; y: number }>,
): Map<string, { x: number; y: number }> {
  const degree = new Map<string, number>();
  for (const n of data.nodes) degree.set(n.id, 0);
  for (const l of data.links) {
    if (l.source === l.target || !degree.has(l.source) || !degree.has(l.target)) continue;
    degree.set(l.source, degree.get(l.source)! + 1);
    degree.set(l.target, degree.get(l.target)! + 1);
  }
  const out = withFiniteFallback(positions, seeds);
  let maxR = 0;
  for (const [id, pos] of out) {
    if ((degree.get(id) ?? 0) > 0) maxR = Math.max(maxR, Math.hypot(pos.x, pos.y));
  }
  if (maxR === 0) maxR = radialRadius(data.nodes.length);
  const halo = maxR * 1.15;
  for (const [id, d] of degree) {
    if (d === 0 && out.has(id)) {
      const a = hashStr(id) * 2 * Math.PI;
      out.set(id, { x: Math.cos(a) * halo, y: Math.sin(a) * halo });
    }
  }
  return out;
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
