import type { GraphData } from '../types';
import { hashStr } from './geometry';
import { SCHEMA_LABELS } from './view';

/** Radius scale shared with the old circle seed so camera framing is unchanged. */
export function radialRadius(order: number): number {
  return 30 * Math.sqrt(Math.max(order, 1));
}

/**
 * Deterministic radial-by-type layout (P2): each node label gets an angular
 * sector proportional to its count (SCHEMA_LABELS order, unknown labels last),
 * nodes spread within their sector sorted by id, radius jittered by hash.
 * Same input -> same positions, every load: no random-looking hairball.
 */
export function radialLayout(data: GraphData): Map<string, { x: number; y: number }> {
  const out = new Map<string, { x: number; y: number }>();
  const total = data.nodes.length;
  if (total === 0) return out;
  const R = radialRadius(total);
  const byLabel = new Map<string, typeof data.nodes>();
  for (const n of data.nodes) {
    const list = byLabel.get(n.label) ?? [];
    list.push(n);
    byLabel.set(n.label, list);
  }
  const orderedLabels = [
    ...SCHEMA_LABELS.filter((l) => byLabel.has(l)),
    ...[...byLabel.keys()].filter((l) => !SCHEMA_LABELS.includes(l)).sort(),
  ];
  let start = 0;
  for (const label of orderedLabels) {
    const members = byLabel.get(label)!.slice().sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
    const span = (members.length / total) * 2 * Math.PI;
    members.forEach((n, i) => {
      const a = start + (span * (i + 0.5)) / members.length;
      const r = R * (0.45 + 0.5 * hashStr(n.id));
      out.set(n.id, { x: Math.cos(a) * r, y: Math.sin(a) * r });
    });
    start += span;
  }
  return out;
}
