// Deterministic initial layout: circle seed (stable input order) plus one
// synchronous ForceAtlas2 pass. Never re-run afterwards — later polls only
// place newcomers (neighbor centroid, deterministic ring fallback) while
// survivors keep their coordinates.

import Graph from 'graphology';
import forceAtlas2 from 'graphology-layout-forceatlas2';

import { centroid, hashStr } from './geometry';

export interface PlacedPoint {
  x: number;
  y: number;
}

/**
 * Compute positions for every id exactly once. Same input always yields the
 * same output (ids are sorted before seeding; FA2 itself is deterministic).
 */
export function computeInitialLayout(
  ids: string[],
  edges: [string, string][],
  iterations = 250,
): Map<string, PlacedPoint> {
  const sorted = [...new Set(ids)].sort();
  const graph = new Graph({ type: 'directed', allowSelfLoops: true });
  const radius = 30 * Math.sqrt(Math.max(sorted.length, 1));
  sorted.forEach((id, i) => {
    const a = (i / Math.max(sorted.length, 1)) * 2 * Math.PI;
    graph.addNode(id, { x: Math.cos(a) * radius, y: Math.sin(a) * radius });
  });
  for (const [s, t] of edges) {
    if (graph.hasNode(s) && graph.hasNode(t)) graph.addEdge(s, t);
  }
  if (graph.order > 0) {
    forceAtlas2.assign(graph, {
      iterations,
      settings: forceAtlas2.inferSettings(graph),
    });
  }
  const out = new Map<string, PlacedPoint>();
  graph.forEachNode((id, attrs) => {
    out.set(id, { x: attrs.x as number, y: attrs.y as number });
  });
  return out;
}

/**
 * Position ids missing from `positions`, using placed neighbors (centroid)
 * or a deterministic ring. Never touches ids that already have a spot.
 */
export function placeMissing(
  ids: string[],
  adjacency: Map<string, string[]>,
  positions: Map<string, PlacedPoint>,
): Map<string, PlacedPoint> {
  const next = new Map(positions);
  for (const id of ids) {
    if (next.has(id)) continue;
    const neighbors = (adjacency.get(id) ?? [])
      .map((nb) => next.get(nb))
      .filter((p): p is PlacedPoint => !!p);
    const center = centroid(neighbors);
    if (center) {
      next.set(id, center);
    } else {
      const a = hashStr(id) * 2 * Math.PI;
      const r = 40 + hashStr(id + ':r') * 80;
      next.set(id, { x: Math.cos(a) * r, y: Math.sin(a) * r });
    }
  }
  return next;
}
