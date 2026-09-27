// View-model builder: API payload + view state -> everything the canvas
// paints. Pure function of its inputs (no layout, no camera, no DOM), so
// filtering, dimming, titles, and sizes are all unit-testable.

import { theme } from '../theme';
import type { GraphData } from '../types';
import { isFinitePoint } from './geometry';
import type { GraphMode, ViewEdge, ViewModel, ViewNode } from './types';

export const SCHEMA_LABELS = ['Politician', 'Insider', 'Committee', 'Event', 'Ticker', 'Sector'];
export const SCHEMA_RADIUS = 260;

/** Fixed circle slots for schema supernodes (stable across polls). */
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

function schemaTitle(
  label: string,
  count: number,
  nodes: GraphData['nodes'],
  degree: Map<string, number>,
): string {
  if (count === 0) return `${label} (0)`;
  const tops = nodes
    .filter((n) => n.label === label)
    .map((n) => ({
      t: n.label === 'Event' ? eventTitle(n.props) : n.title,
      d: degree.get(n.id) ?? 0,
    }))
    .sort((x, y) => y.d - x.d)
    .slice(0, 2)
    .map((t) => truncate(t.t, 15));
  return tops.length ? `${label} (${count}): ${tops.join(', ')}` : `${label} (${count})`;
}

export interface ViewOptions {
  mode: GraphMode;
  hiddenTypes: Set<string>;
  selectedId: string | null;
  hoveredId?: string | null;
  positions: Map<string, { x: number; y: number }>;
}

/**
 * Build the render lists. Only items with finite positions are included —
 * anything unplaceable is dropped rather than rendered as garbage. Hidden
 * types and the inactive mode are excluded structurally (never dimmed into
 * place), so the painter cannot disagree about visibility.
 */
export function buildViewModel(data: GraphData, opts: ViewOptions): ViewModel {
  const { mode, hiddenTypes, selectedId, hoveredId, positions } = opts;
  const byId = new Map(data.nodes.map((n) => [n.id, n]));
  const degree = new Map<string, number>();
  for (const l of data.links) {
    degree.set(l.source, (degree.get(l.source) ?? 0) + 1);
    degree.set(l.target, (degree.get(l.target) ?? 0) + 1);
  }
  const agg = aggregateSchema(data.nodes, data.links);

  const wanted = new Map<string, { kind: string }>();
  if (mode === 'instances') {
    for (const n of data.nodes) {
      if (hiddenTypes.has(n.label)) continue;
      const pos = positions.get(n.id);
      if (!isFinitePoint(pos)) continue;
      wanted.set(n.id, { kind: n.label });
    }
  } else {
    for (const [label, count] of agg.counts) {
      if (count === 0) continue;
      const id = schemaId(label);
      const pos = positions.get(id);
      if (!isFinitePoint(pos)) continue;
      wanted.set(id, { kind: label });
    }
  }

  const adjacency = new Map<string, Set<string>>();
  const link = (a: string, b: string) => {
    if (!wanted.has(a) || !wanted.has(b)) return;
    if (!adjacency.has(a)) adjacency.set(a, new Set());
    adjacency.get(a)!.add(b);
    if (!adjacency.has(b)) adjacency.set(b, new Set());
    adjacency.get(b)!.add(a);
  };

  const nodes: ViewNode[] = [];
  for (const id of wanted.keys()) {
    const pos = positions.get(id)!;
    if (isSchemaId(id)) {
      const label = id.slice('schema:'.length);
      const count = agg.counts.get(label) ?? 0;
      const color = nodeColor(label);
      nodes.push({
        id,
        kind: label,
        label: schemaTitle(label, count, data.nodes, degree),
        title: schemaTitle(label, count, data.nodes, degree),
        x: pos.x,
        y: pos.y,
        size: 10 + Math.sqrt(count) * 1.5,
        color,
        borderColor: shade(color, 0.55),
        dimmed: false,
        selected: false,
        props: {},
        tweet_url: null,
      });
      continue;
    }
    const n = byId.get(id)!;
    const color = nodeColor(n.label);
    const display = n.label === 'Event' ? eventTitle(n.props) : n.title;
    nodes.push({
      id,
      kind: n.label,
      label: display,
      title: display,
      x: pos.x,
      y: pos.y,
      size: nodeSize(n.label, degree.get(id) ?? 0),
      color,
      borderColor: shade(color, 0.55),
      dimmed: false,
      selected: selectedId === id,
      props: n.props,
      tweet_url: n.tweet_url,
    });
  }

  const edges: ViewEdge[] = [];
  const pushEdge = (
    key: string,
    source: string,
    target: string,
    type: string,
    label: string,
    size: number,
  ) => {
    if (!wanted.has(source) || !wanted.has(target)) return;
    link(source, target);
    edges.push({ key, source, target, type, label, size, color: '#3d3d45', dimmed: false });
  };
  for (const l of data.links) {
    pushEdge(`${l.source}>${l.target}:${l.type}`, l.source, l.target, l.type, l.type, 1.2);
  }
  for (const [key, r] of agg.rels) {
    const s = schemaId(r.source);
    const t = schemaId(r.target);
    if (!wanted.has(s) || !wanted.has(t)) continue;
    link(s, t);
    edges.push({
      key,
      source: s,
      target: t,
      type: r.rel,
      label: humanizeRel(r.rel),
      size: 1.5 + Math.log10(r.count + 1),
      color: '#3d3d45',
      dimmed: false,
    });
  }

  // Focus: selection (or hover) keeps its 1-hop neighborhood bright. Only
  // the selected node gets the size boost; hovered focus just dims the rest.
  const focusId = selectedId ?? hoveredId ?? null;
  let focus: Set<string> | null = null;
  if (focusId && wanted.has(focusId)) {
    focus = new Set([focusId, ...(adjacency.get(focusId) ?? [])]);
  }
  if (focus) {
    for (const n of nodes) {
      if (n.id === selectedId) continue;
      if (!focus.has(n.id)) {
        n.dimmed = true;
        n.color = DIM_NODE;
        n.label = '';
      } else if (focusId && n.id !== focusId) {
        n.size *= 1.25;
      }
    }
    for (const e of edges) {
      if (!(focus.has(e.source) && focus.has(e.target))) {
        e.dimmed = true;
        e.color = DIM_EDGE;
      }
    }
    const sel = nodes.find((n) => n.id === selectedId);
    if (sel) sel.size *= 1.5;
  }

  return { nodes, edges };
}
