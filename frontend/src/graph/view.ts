// Shared graph helpers: schema constants, titles, colors, sizes.
// Pure functions of their inputs (no layout, no camera, no DOM).

import { theme } from '../theme';
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

export function nodeColor(label: string): string {
  return theme.nodeColors[label as keyof typeof theme.nodeColors] ?? theme.accent;
}

export function shade(hex: string, factor: number): string {
  const n = parseInt(hex.slice(1), 16);
  const mix = (c: number) => Math.round(c * factor);
  const v = (mix((n >> 16) & 255) << 16) | (mix((n >> 8) & 255) << 8) | mix(n & 255);
  return `#${v.toString(16).padStart(6, '0')}`;
}

export function truncate(s: string, n: number): string {
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}

export function humanizeRel(rel: string): string {
  return rel.toLowerCase().replace(/_/g, ' ');
}

// Event nodes arrive titled with the bare ticker ("SE"), so dozens read
// identically. Compose what happened from the props instead.
export function eventTitle(props: Record<string, unknown>): string {
  const ticker = typeof props.ticker === 'string' ? props.ticker : '';
  const type = props.type;
  if (type === 'options_flow') {
    const contract = props.contract_type === 'put' ? 'put' : 'call';
    const strike = typeof props.strike === 'number' ? ` ${props.strike}` : '';
    return ticker ? `${ticker} ${contract}${strike}` : 'Options flow';
  }
  if (type === 'congress_trade' || type === 'insider_trade') {
    const side = props.transaction_type === 'sell' ? 'sold' : 'bought';
    return ticker ? `${ticker} ${side}` : 'Trade';
  }
  return 'Unparsed';
}

export function nodeSize(label: string, degree: number): number {
  if (label === 'Event') return 2.4 + Math.min(Math.sqrt(degree) * 1.0, 3);
  return 3.2 + Math.min(Math.sqrt(degree) * 1.6, 7);
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
