import type { GraphData } from '../types';

export const SCHEMA_LABELS = ['Politician', 'Insider', 'Committee', 'Event', 'Ticker', 'Sector'];
export const SCHEMA_RADIUS = 260;

/** Node/edge dim colors for unfocused items (reducers only dim, never hide). */
export const DIM_NODE = '#2b2b31';
export const DIM_EDGE = 'rgba(61,61,69,0.15)';

export function schemaId(label: string): string {
  return `schema:${label}`;
}

export function isSchemaId(id: string): boolean {
  return id.startsWith('schema:');
}

export function humanizeRel(rel: string): string {
  return rel.toLowerCase().replace(/_/g, ' ');
}

export interface SchemaAgg {
  counts: Map<string, number>;
  rels: Map<string, { source: string; rel: string; target: string; count: number }>;
}

export function aggregateSchema(
  nodes: GraphData['nodes'],
  links: GraphData['links'],
): SchemaAgg {
  const counts = new Map<string, number>();
  const labelById = new Map<string, string>();
  for (const n of nodes) {
    labelById.set(n.id, n.label);
    counts.set(n.label, (counts.get(n.label) ?? 0) + 1);
  }
  const rels = new Map<string, { source: string; rel: string; target: string; count: number }>();
  for (const l of links) {
    const s = labelById.get(l.source);
    const t = labelById.get(l.target);
    if (!s || !t) continue;
    const key = `${s}>${l.type}>${t}`;
    const prev = rels.get(key);
    if (prev) prev.count += 1;
    else rels.set(key, { source: s, rel: l.type, target: t, count: 1 });
  }
  return { counts, rels };
}
