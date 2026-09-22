import Graph from 'graphology';

import type { GraphData } from '../types';
import { hashStr } from './geometry';
import {
  aggregateSchema,
  eventTitle,
  humanizeRel,
  isSchemaId,
  nodeColor,
  nodeSize,
  SCHEMA_LABELS,
  SCHEMA_RADIUS,
  schemaId,
  shade,
  truncate,
} from './view';

const POS_CACHE_CAP = 2000;

function bankPos(
  posCache: Map<string, { x: number; y: number }>,
  graph: Graph,
  id: string,
): void {
  const x = graph.getNodeAttribute(id, 'x') as number;
  const y = graph.getNodeAttribute(id, 'y') as number;
  if (Number.isFinite(x) && Number.isFinite(y)) {
    posCache.delete(id);
    posCache.set(id, { x, y });
    while (posCache.size > POS_CACHE_CAP) {
      const oldest = posCache.keys().next();
      if (oldest.done) break;
      posCache.delete(oldest.value);
    }
  }
}

// Single owner of graph membership, attrs, edges, and positions. Computes
// the wanted id set from the mode/filter, drops anything else (banking
// positions for a possible return), adds what's missing (attrs + restored
// or seeded positions), and syncs edges. Positions of surviving nodes are
// never touched. When initialLayout is set, present instances are spread on
// a deterministic circle instead (first-paint input for the FA2 pass).
// Single owner of graph membership AND per-item visuals. Computes the wanted
// id set from the mode/filter, drops anything else (banking positions),
// (re-)adds what's missing with full attrs, syncs edges, and seeds positions
// for nodes that have none (banked restore, else neighbor centroid, else
// deterministic ring). Surviving nodes are never moved or relaid out.
// Returns membership stats for diagnostics. Ends with a guarantee pass: any
// node still lacking finite coordinates gets a deterministic ring spot, so
// no caller can ever hand sigma a positionless node.

export function applyVisibility(
  graph: Graph,
  data: GraphData,
  mode: 'instances' | 'schema',
  hiddenTypes: Set<string>,
  posCache: Map<string, { x: number; y: number }>,
): { want: number; order: number; dataNodes: number } {
  const byId = new Map(data.nodes.map((n) => [n.id, n]));
  const degree = new Map<string, number>();
  for (const l of data.links) {
    degree.set(l.source, (degree.get(l.source) ?? 0) + 1);
    degree.set(l.target, (degree.get(l.target) ?? 0) + 1);
  }
  const agg = aggregateSchema(data.nodes, data.links);

  // Payload adjacency for centroid placement (graph edges sync below, so
  // graph.neighbors would see nothing yet — and throws on missing nodes).
  const linkAdj = new Map<string, string[]>();
  const linkAdjAdd = (a: string, b: string) => {
    const list = linkAdj.get(a) ?? [];
    list.push(b);
    linkAdj.set(a, list);
  };
  for (const l of data.links) {
    linkAdjAdd(l.source, l.target);
    linkAdjAdd(l.target, l.source);
  }

  const want = new Set<string>();
  if (mode === 'instances') {
    for (const n of data.nodes) if (!hiddenTypes.has(n.label)) want.add(n.id);
  } else {
    for (const [label, count] of agg.counts) if (count > 0) want.add(schemaId(label));
  }

  const drop: string[] = [];
  graph.forEachNode((id) => {
    if (!want.has(id)) drop.push(id);
  });
  for (const id of drop) {
    bankPos(posCache, graph, id);
    graph.dropNode(id);
  }

  for (const id of want) {
    if (graph.hasNode(id)) {
      // Refresh attrs of survivors (titles/counts/colors can change).
      if (isSchemaId(id)) {
        const label = id.slice('schema:'.length);
        const count = agg.counts.get(label) ?? 0;
        const color = nodeColor(label);
        graph.mergeNodeAttributes(id, {
          title: schemaTitle(label, count, byId, degree),
          color,
          borderColor: shade(color, 0.55),
          size: 10 + Math.sqrt(count) * 1.5,
        });
      } else {
        const n = byId.get(id);
        if (!n) continue;
        const color = nodeColor(n.label);
        const display = n.label === 'Event' ? eventTitle(n.props) : n.title;
        graph.mergeNodeAttributes(id, {
          type: 'bordered',
          kind: n.label,
          label: display,
          key: n.key,
          title: display,
          props: n.props,
          tweet_url: n.tweet_url,
          color,
          borderColor: shade(color, 0.55),
          size: nodeSize(n.label, degree.get(id) ?? 0),
        });
      }
      continue;
    }
    // New nodes are created WITH coordinates in the same call: sigma
    // validates synchronously on graphology mutation events, so a node
    // must never exist even transiently without a finite position.
    if (isSchemaId(id)) {
      const label = id.slice('schema:'.length);
      const count = agg.counts.get(label) ?? 0;
      const color = nodeColor(label);
      const idx = Math.max(SCHEMA_LABELS.indexOf(label), 0);
      const a = (idx / SCHEMA_LABELS.length) * 2 * Math.PI;
      const x = Math.cos(a) * SCHEMA_RADIUS;
      const y = Math.sin(a) * SCHEMA_RADIUS;
      graph.addNode(id, {
        type: 'bordered',
        kind: label,
        label: schemaTitle(label, count, byId, degree),
        key: label,
        title: schemaTitle(label, count, byId, degree),
        props: {},
        tweet_url: null,
        color,
        borderColor: shade(color, 0.55),
        size: 10 + Math.sqrt(count) * 1.5,
        x,
        y,
      });
      continue;
    }
    const n = byId.get(id);
    if (!n) continue;
    const color = nodeColor(n.label);
    const display = n.label === 'Event' ? eventTitle(n.props) : n.title;
    const saved = posCache.get(id);
    let px: number;
    let py: number;
    if (saved) {
      px = saved.x;
      py = saved.y;
    } else {
      // Centroid over payload neighbors that already have finite spots.
      // (Graph edges sync below, so neighbor lookups here use the payload
      // adjacency instead of graph.neighbors — which throws on missing
      // nodes and would see no edges yet anyway.)
      const xs: number[] = [];
      const ys: number[] = [];
      for (const nb of linkAdj.get(id) ?? []) {
        if (!graph.hasNode(nb)) continue;
        const x = graph.getNodeAttribute(nb, 'x') as number;
        const y = graph.getNodeAttribute(nb, 'y') as number;
        if (Number.isFinite(x) && Number.isFinite(y)) {
          xs.push(x);
          ys.push(y);
        }
      }
      if (xs.length > 0) {
        px = xs.reduce((s, v) => s + v, 0) / xs.length;
        py = ys.reduce((s, v) => s + v, 0) / ys.length;
      } else {
        const a = hashStr(id) * 2 * Math.PI;
        const r = 40 + hashStr(id + ':r') * 80;
        px = Math.cos(a) * r;
        py = Math.sin(a) * r;
      }
    }
    graph.addNode(id, {
      type: 'bordered',
      kind: n.label,
      label: display,
      key: n.key,
      title: display,
      props: n.props,
      tweet_url: n.tweet_url,
      color,
      borderColor: shade(color, 0.55),
      size: nodeSize(n.label, degree.get(id) ?? 0),
      x: px,
      y: py,
    });
  }

  // Edges: payload links between present nodes, schema rels between present
  // supernodes; drop everything stale.
  const payloadEdgeKeys = new Set<string>();
  for (const l of data.links) {
    if (!graph.hasNode(l.source) || !graph.hasNode(l.target)) continue;
    const key = `${l.source}>${l.target}:${l.type}`;
    payloadEdgeKeys.add(key);
    if (!graph.hasEdge(key)) {
      graph.addEdgeWithKey(key, l.source, l.target, { type: 'curvedArrow', label: l.type });
    }
  }
  for (const [key, r] of agg.rels) {
    const s = schemaId(r.source);
    const t = schemaId(r.target);
    if (!graph.hasNode(s) || !graph.hasNode(t)) continue;
    if (!graph.hasEdge(key)) {
      graph.addEdgeWithKey(key, s, t, { type: 'curvedArrow', label: humanizeRel(r.rel) });
    }
    graph.setEdgeAttribute(key, 'size', 1.5 + Math.log10(r.count + 1));
  }
  graph.forEachEdge((e) => {
    if (!payloadEdgeKeys.has(e) && !agg.rels.has(e)) graph.dropEdge(e);
  });

  // Guarantee pass: any node still lacking finite coordinates gets a
  // deterministic ring spot. No caller can hand sigma a positionless node,
  // regardless of which path above created it.
  for (const id of want) {
    if (!graph.hasNode(id)) continue;
    const x = graph.getNodeAttribute(id, 'x');
    const y = graph.getNodeAttribute(id, 'y');
    if (typeof x === 'number' && typeof y === 'number' && Number.isFinite(x) && Number.isFinite(y)) {
      continue;
    }
    const a = hashStr(id) * 2 * Math.PI;
    const r = 40 + hashStr(id + ':r') * 80;
    graph.setNodeAttribute(id, 'x', Math.cos(a) * r);
    graph.setNodeAttribute(id, 'y', Math.sin(a) * r);
  }

  return { want: want.size, order: graph.order, dataNodes: data.nodes.length };
}

function schemaTitle(
  label: string,
  count: number,
  byId: Map<string, GraphData['nodes'][number]>,
  degree: Map<string, number>,
): string {
  if (count === 0) return `${label} (0)`;
  const tops = [...byId.values()]
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
