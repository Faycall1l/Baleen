import { useEffect, useMemo, useRef, useState } from 'react';

import { EdgeCurvedArrowProgram } from '@sigma/edge-curve';
import { createNodeBorderProgram } from '@sigma/node-border';
import Graph from 'graphology';
import forceAtlas2 from 'graphology-layout-forceatlas2';
import Sigma from 'sigma';

import { theme } from '../theme';
import type { GraphData, GraphNode } from '../types';

interface Props {
  data: GraphData | null;
  error: string | null;
  selectedId: string | null;
  onSelect: (node: GraphNode | null) => void;
}

// ---------------------------------------------------------------------------
// Sigma + graphology explorer.
//
// Motion model: the layout is computed ONCE, synchronously, on first paint
// (deterministic circle seed + one ForceAtlas2 pass), then never again. Later
// polls merge into the same graphology instance — positions persist, newcomers
// are placed at their neighborhood centroid, removals drop out — and sigma
// itself runs no simulation at all. The map is static by construction: no
// popping, no flicker. Pan/zoom/drag still work; drag rewrites coordinates.
//
// Visibility model: what you see is what EXISTS in the graphology graph.
// Modes and legend filters add/drop nodes (banking positions), never paint
// tricks — custom WebGL programs don't reliably honor sigma's `hidden`
// flag, so hiding-by-paint is not used anywhere. Reducers only dim.
//
// Exploration model (Shneiderman, condensed): search to find, click to focus
// (neighborhood stays bright, rest dims), toolbar to reset, legend always
// visible, detail panel on the right for the selected node.
// ---------------------------------------------------------------------------

const FA2_ITERATIONS_SMALL = 250;
const FA2_ITERATIONS_LARGE = 100;
const LARGE_GRAPH = 500;

const DIM_NODE = '#2b2b31';
const DIM_EDGE = 'rgba(61,61,69,0.15)';

// Banked positions survive node removal so re-shown nodes return to their
// old spot instead of being re-seeded. Bounded FIFO.
const POS_CACHE_CAP = 2000;

// Ontology (schema) overview: the graph's labels as supernodes sized by
// instance count (sqrt-scaled, per VOWL), connected by the relationship
// types actually present, with human-readable property labels. Gives the
// "what is this data" overview first; clicking a class drills into its
// instances. Pure frontend aggregation over the loaded payload — no backend
// changes.
const SCHEMA_LABELS = ['Politician', 'Insider', 'Committee', 'Event', 'Ticker', 'Sector'];
const SCHEMA_RADIUS = 260;

const schemaId = (label: string): string => `schema:${label}`;
const isSchemaId = (id: string): boolean => id.startsWith('schema:');
const schemaEdgeKey = (s: string, rel: string, t: string): string =>
  `schema-rel:${s}>${rel}>${t}`;

function humanizeRel(rel: string): string {
  return rel.toLowerCase().replace(/_/g, ' ');
}

// Nodes render as filled discs with a thin darker border; arrows show edge
// direction. Both programs are static paint — no animation involved.
const BorderedNodeProgram = createNodeBorderProgram({
  borders: [
    { size: { value: 0.16, mode: 'relative' }, color: { attribute: 'borderColor' } },
    { size: { fill: true }, color: { attribute: 'color' } },
  ],
});

function nodeColor(label: string): string {
  return theme.nodeColors[label as keyof typeof theme.nodeColors] ?? theme.accent;
}

function shade(hex: string, factor: number): string {
  const n = parseInt(hex.slice(1), 16);
  const mix = (c: number) => Math.round(c * factor);
  const v = (mix((n >> 16) & 255) << 16) | (mix((n >> 8) & 255) << 8) | mix(n & 255);
  return `#${v.toString(16).padStart(6, '0')}`;
}

function nodeSize(label: string, degree: number): number {
  if (label === 'Event') return 1.6 + Math.min(Math.sqrt(degree) * 0.8, 2.4);
  return 2.2 + Math.min(Math.sqrt(degree) * 1.2, 5);
}

function hashStr(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return (h >>> 0) / 4294967295;
}

function truncate(s: string, n: number): string {
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}

// Event nodes arrive titled with the bare ticker ("SE"), so dozens of them
// read identically. Compose what happened from the props instead — short
// enough for canvas labels, distinct from the ticker nodes themselves.
function eventTitle(props: Record<string, unknown>): string {
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

function drawLabelWithHalo(
  context: CanvasRenderingContext2D,
  data: { x: number; y: number; size: number; label?: string | null; color: string },
  settings: { labelSize: number; labelFont: string; labelWeight: string },
): void {
  if (!data.label) return;
  const { labelSize: size, labelFont: font, labelWeight: weight } = settings;
  context.font = `${weight} ${size}px ${font}`;
  context.lineWidth = 3;
  context.strokeStyle = theme.bg;
  context.strokeText(data.label, data.x + data.size + 3, data.y + size / 3);
  context.fillStyle = theme.text;
  context.fillText(data.label, data.x + data.size + 3, data.y + size / 3);
}

type SchemaAgg = {
  counts: Map<string, number>;
  rels: Map<string, { source: string; rel: string; target: string; count: number }>;
};

function aggregateSchema(
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
    const key = schemaEdgeKey(s, l.type, t);
    const prev = rels.get(key);
    if (prev) prev.count += 1;
    else rels.set(key, { source: s, rel: l.type, target: t, count: 1 });
  }
  return { counts, rels };
}

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
function applyVisibility(
  graph: Graph,
  data: GraphData,
  mode: 'instances' | 'schema',
  hiddenTypes: Set<string>,
  posCache: Map<string, { x: number; y: number }>,
): void {
  const byId = new Map(data.nodes.map((n) => [n.id, n]));
  const degree = new Map<string, number>();
  for (const l of data.links) {
    degree.set(l.source, (degree.get(l.source) ?? 0) + 1);
    degree.set(l.target, (degree.get(l.target) ?? 0) + 1);
  }
  const agg = aggregateSchema(data.nodes, data.links);

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
    if (isSchemaId(id)) {
      const label = id.slice('schema:'.length);
      const count = agg.counts.get(label) ?? 0;
      const color = nodeColor(label);
      const idx = Math.max(SCHEMA_LABELS.indexOf(label), 0);
      const a = (idx / SCHEMA_LABELS.length) * 2 * Math.PI;
      graph.addNode(id, {});
      graph.mergeNodeAttributes(id, {
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
        x: Math.cos(a) * SCHEMA_RADIUS,
        y: Math.sin(a) * SCHEMA_RADIUS,
      });
      continue;
    }
    const n = byId.get(id);
    if (!n) continue;
    const color = nodeColor(n.label);
    const display = n.label === 'Event' ? eventTitle(n.props) : n.title;
    graph.addNode(id, {});
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
    // Position: banked restore, else centroid of placed neighbors, else ring.
    const saved = posCache.get(id);
    if (saved) {
      graph.setNodeAttribute(id, 'x', saved.x);
      graph.setNodeAttribute(id, 'y', saved.y);
    } else {
      const xs: number[] = [];
      const ys: number[] = [];
      for (const nb of graph.neighbors(id)) {
        const x = graph.getNodeAttribute(nb, 'x') as number;
        const y = graph.getNodeAttribute(nb, 'y') as number;
        if (Number.isFinite(x) && Number.isFinite(y)) {
          xs.push(x);
          ys.push(y);
        }
      }
      if (xs.length > 0) {
        graph.setNodeAttribute(id, 'x', xs.reduce((s, v) => s + v, 0) / xs.length);
        graph.setNodeAttribute(id, 'y', ys.reduce((s, v) => s + v, 0) / ys.length);
      } else {
        const a = hashStr(id) * 2 * Math.PI;
        const r = 40 + hashStr(id + ':r') * 80;
        graph.setNodeAttribute(id, 'x', Math.cos(a) * r);
        graph.setNodeAttribute(id, 'y', Math.sin(a) * r);
      }
    }
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

export default function GraphView({ data, error, selectedId, onSelect }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const graphRef = useRef<Graph | null>(null);
  const sigmaRef = useRef<Sigma | null>(null);
  const posCacheRef = useRef(new Map<string, { x: number; y: number }>());
  const dataRef = useRef(data);
  dataRef.current = data;
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;
  const [query, setQuery] = useState('');
  const [searchOpen, setSearchOpen] = useState(false);
  const [mode, setMode] = useState<'instances' | 'schema'>('instances');
  // Labels hidden from the instance view (legend toggles). Schema view is
  // always complete; hidden types only apply to instances.
  const [hiddenTypes, setHiddenTypes] = useState<Set<string>>(new Set());

  // Selection entry point: schema nodes drill into their type, instance
  // nodes select for the inspector. Dropped (invisible) nodes can never be
  // selected because hit-testing only sees rendered nodes — and the guard
  // below makes that structural, not assumed.
  const selectNodeByKey = (node: string) => {
    const g = graphRef.current;
    if (!g || !g.hasNode(node)) return;
    if (isSchemaId(node)) {
      // Drill-down keeps the type PLUS its directly related types (read off
      // the schema edges): isolating a single type would show dots with no
      // relationships, which is exactly as useless as it sounds.
      const label = node.slice('schema:'.length);
      const keep = new Set<string>([label]);
      g.forEachEdge((e) => {
        if (!e.startsWith('schema-rel:')) return;
        const [s, t] = g.extremities(e);
        const sl = g.getNodeAttribute(s, 'kind') as string;
        const tl = g.getNodeAttribute(t, 'kind') as string;
        if (sl === label && tl) keep.add(tl);
        if (tl === label && sl) keep.add(sl);
      });
      setHiddenTypes(new Set(SCHEMA_LABELS.filter((l) => !keep.has(l))));
      setMode('instances');
      onSelectRef.current(null);
      return;
    }
    const a = g.getNodeAttributes(node);
    onSelectRef.current({
      id: node,
      label: a.kind,
      key: a.key,
      title: a.label,
      props: a.props,
      tweet_url: a.tweet_url ?? null,
    });
  };

  // Backstop: releasing outside the canvas still ends any drag and frees
  // the camera, otherwise pan/zoom stay dead until the next gesture.
  useEffect(() => {
    const backstop = () => {
      dragRef.current = null;
      sigmaRef.current?.getCamera().enable();
    };
    window.addEventListener('mouseup', backstop);
    return () => window.removeEventListener('mouseup', backstop);
  }, []);

  // Node being dragged, if any. Dragging is manual (sigma core pans the
  // camera but never moves nodes itself): press moves with the pointer,
  // camera stays put. Click detection stays entirely native — this layer
  // only moves coordinates, never decides or suppresses clicks.
  const dragRef = useRef<string | null>(null);

  // Data flow per poll: applyVisibility syncs membership, attrs, edges and
  // positions; first paint adds one FA2 pass over the rendered set, then
  // sigma is created exactly once. Positions of surviving nodes are never
  // touched at any step, and no layout ever re-runs afterwards.
  useEffect(() => {
    if (!containerRef.current || !data || data.nodes.length === 0) return;

    let graph = graphRef.current;
    if (!graph) {
      graph = new Graph({ type: 'directed', allowSelfLoops: true });
      graphRef.current = graph;
    }

    applyVisibility(graph, data, mode, hiddenTypes, posCacheRef.current);

    let sigma = sigmaRef.current;
    if (!sigma) {
      // First paint: one synchronous FA2 pass over the rendered set (initial
      // mode is always instances), then render. Nothing animates at any point.
      const order = graph.order;
      const radius = 30 * Math.sqrt(Math.max(order, 1));
      let i = 0;
      graph.forEachNode((n) => {
        const a = (i++ / order) * 2 * Math.PI;
        graph.setNodeAttribute(n, 'x', Math.cos(a) * radius);
        graph.setNodeAttribute(n, 'y', Math.sin(a) * radius);
      });
      forceAtlas2.assign(graph, {
        iterations: order > LARGE_GRAPH ? FA2_ITERATIONS_LARGE : FA2_ITERATIONS_SMALL,
        settings: forceAtlas2.inferSettings(graph),
      });

      const fresh = new Sigma(graph, containerRef.current, {
        allowInvalidContainer: true,
        renderLabels: true,
        labelSize: 13,
        labelWeight: '500',
        labelColor: { color: theme.textDim },
        labelFont: 'system-ui, sans-serif',
        labelRenderedSizeThreshold: 8,
        labelDensity: 0.08,
        defaultDrawNodeLabel: drawLabelWithHalo,
        defaultNodeColor: theme.accent,
        defaultEdgeColor: '#3d3d45',
        minCameraRatio: 0.05,
        maxCameraRatio: 20,
        // Generous click-vs-drag tolerance: hand tremor emits well over the
        // default 3 mousemove events, which silently ate real clicks.
        draggedEventsTolerance: 14,
        nodeProgramClasses: { bordered: BorderedNodeProgram },
        edgeProgramClasses: { curvedArrow: EdgeCurvedArrowProgram },
      });
      sigmaRef.current = fresh;
      // Open zoomed in: the interesting structure sits near the center and
      // a full-fit overview reads as dots. Runs before first paint, so the
      // user never sees the wide view.
      fresh.getCamera().setState({ x: 0, y: 0, angle: 0, ratio: 1.6 });

      // Native click selection (sigma suppresses clicks that turn into drags
      // past draggedEventsTolerance, raised above for hand tremor).
      fresh.on('clickNode', ({ node }) => selectNodeByKey(node));
      fresh.on('clickStage', () => onSelectRef.current(null));
      // Manual node dragging (sigma core pans the camera but never moves
      // nodes itself): the pressed node follows the pointer, camera stays
      // put. Click detection stays entirely native — this layer only moves
      // coordinates, never decides or suppresses clicks.
      fresh.on('downNode', (e) => {
        dragRef.current = e.node;
        fresh.getCamera().disable();
      });
      fresh.getMouseCaptor().on('mousemove', (e) => {
        const dragged = dragRef.current;
        const g = graphRef.current;
        if (!dragged || !g || !g.hasNode(dragged)) return;
        const pos = fresh.viewportToGraph(e);
        g.setNodeAttribute(dragged, 'x', pos.x);
        g.setNodeAttribute(dragged, 'y', pos.y);
        e.preventSigmaDefault();
      });
      fresh.getMouseCaptor().on('mouseup', () => {
        dragRef.current = null;
        fresh.getCamera().enable();
      });
    } else {
      try {
        sigma.refresh();
      } catch {
        /* a failed paint must never take down the view; next poll refreshes */
      }
    }
  }, [data]);

  // Mode/filter changes re-run membership without touching data (positions
  // of survivors are preserved by the bank/restore inside applyVisibility).
  useEffect(() => {
    const graph = graphRef.current;
    const sigma = sigmaRef.current;
    const latest = dataRef.current;
    if (!graph || !sigma || !latest) return;
    applyVisibility(graph, latest, mode, hiddenTypes, posCacheRef.current);
    try {
      sigma.refresh();
    } catch {
      /* visibility flip must never blank the canvas */
    }
  }, [mode, hiddenTypes]);

  const toggleType = (label: string) => {
    setHiddenTypes((prev) => {
      const next = new Set(prev);
      if (next.has(label)) next.delete(label);
      else next.add(label);
      return next;
    });
  };

  const typeCounts = useMemo(() => {
    const counts = new Map<string, number>();
    if (!data) return counts;
    for (const n of data.nodes) counts.set(n.label, (counts.get(n.label) ?? 0) + 1);
    return counts;
  }, [data]);

  // Focus dimming through reducers (color/label only — the only per-item
  // channels the custom programs are proven to honor). Dimming follows
  // selection ONLY — hover-driven dimming used to strobe on every mouse
  // move across dense nodes, so hover is limited to the native label.
  useEffect(() => {
    const sigma = sigmaRef.current;
    const graph = graphRef.current;
    if (!sigma || !graph) return;
    let focusSet: Set<string> | null = null;
    if (selectedId && graph.hasNode(selectedId)) {
      focusSet = new Set([selectedId, ...graph.neighbors(selectedId)]);
    }
    const fs = focusSet;
    sigma.setSetting('nodeReducer', (node, attrs) => {
      if (selectedId === node) return { ...attrs, size: (attrs.size || 3) * 1.5 };
      if (fs && !fs.has(node)) return { ...attrs, color: DIM_NODE, label: '' };
      // Focused neighborhood reads bigger, which also lifts it over the
      // label-size threshold: selecting a node guarantees its neighborhood
      // is labeled, not just brightened.
      if (fs && fs.has(node)) return { ...attrs, size: (attrs.size || 3) * 1.25 };
      return attrs;
    });
    sigma.setSetting('edgeReducer', (edge, attrs) => {
      if (!fs) return attrs;
      const [s, t] = graph.extremities(edge);
      return fs.has(s) && fs.has(t) ? attrs : { ...attrs, color: DIM_EDGE };
    });
    try {
      sigma.refresh();
    } catch {
      /* selection dimming must never blank the canvas */
    }
  }, [selectedId]);

  // Edge property labels only make sense on the schema overview; instance
  // edges stay clean.
  useEffect(() => {
    const sigma = sigmaRef.current;
    if (!sigma) return;
    try {
      sigma.setSetting('renderEdgeLabels', mode === 'schema');
      sigma.refresh();
    } catch {
      /* label toggle must never blank the canvas */
    }
  }, [mode]);

  // Refit on mode switch so each view opens framed.
  useEffect(() => {
    const sigma = sigmaRef.current;
    if (!sigma) return;
    sigma.getCamera().animatedReset({ duration: 600 });
  }, [mode]);

  // Place the camera on the selected node, keeping the zoom level. Instant
  // setState, not animate: no async animation machinery in the click path.
  useEffect(() => {
    const sigma = sigmaRef.current;
    const graph = graphRef.current;
    if (!sigma || !graph || !selectedId || !graph.hasNode(selectedId)) return;
    const x = graph.getNodeAttribute(selectedId, 'x') as number;
    const y = graph.getNodeAttribute(selectedId, 'y') as number;
    if (!Number.isFinite(x) || !Number.isFinite(y)) return;
    try {
      const camera = sigma.getCamera();
      camera.setState({ x, y, angle: camera.angle, ratio: camera.ratio });
    } catch {
      /* a failed placement must never break selection */
    }
  }, [selectedId]);

  // Teardown: sigma owns WebGL resources that must be released.
  useEffect(() => {
    return () => {
      sigmaRef.current?.kill();
      sigmaRef.current = null;
      graphRef.current = null;
    };
  }, []);

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!data || q.length < 2) return [];
    return data.nodes.filter((n) => n.title.toLowerCase().includes(q)).slice(0, 8);
  }, [query, data]);

  const chooseMatch = (m: GraphNode) => {
    if (mode === 'schema') setMode('instances');
    const kind = data?.nodes.find((n) => n.id === m.id)?.label;
    if (kind && mode === 'instances' && hiddenTypes.has(kind)) {
      setHiddenTypes((prev) => {
        const next = new Set(prev);
        next.delete(kind);
        return next;
      });
    }
    onSelect(m);
    setQuery('');
    setSearchOpen(false);
  };

  if (!data) {
    return <div className="empty-hint">Loading graph…</div>;
  }

  if (data.nodes.length === 0) {
    return (
      <div className="empty-hint">
        The graph is empty. Seed it with{' '}
        <code>python -m scripts.ingest_once --seed-ref</code> and reload.
      </div>
    );
  }

  // NOTE: fetch errors overlay the last-good canvas (see below) instead of
  // replacing it: unmounting on error then remounting on recovery replayed
  // the full layout each time, which read as an endless big bang.
  return (
    <div className="graph-wrap">
      <div className="graph-toolbar">
        <div className="graph-mode" role="tablist" aria-label="Graph view">
          <button
            className={mode === 'instances' ? 'active' : ''}
            onClick={() => setMode('instances')}
            role="tab"
            aria-selected={mode === 'instances'}
          >
            Instances
          </button>
          <button
            className={mode === 'schema' ? 'active' : ''}
            onClick={() => {
              onSelectRef.current(null);
              setMode('schema');
            }}
            role="tab"
            aria-selected={mode === 'schema'}
          >
            Schema
          </button>
        </div>
        {mode === 'instances' ? (
          <div className="graph-search">
            <input
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setSearchOpen(true);
              }}
              onFocus={() => setSearchOpen(true)}
              onBlur={() => setTimeout(() => setSearchOpen(false), 150)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && matches.length > 0) chooseMatch(matches[0]);
                if (e.key === 'Escape') {
                  setQuery('');
                  setSearchOpen(false);
                }
              }}
              placeholder="Find a node…"
              aria-label="Find a node"
            />
            {searchOpen && matches.length > 0 && (
              <div className="graph-search-results">
                {matches.map((m) => (
                  <button key={m.id} onMouseDown={(e) => e.preventDefault()} onClick={() => chooseMatch(m)}>
                    <span className="graph-search-label">{m.label}</span> {m.title}
                  </button>
                ))}
              </div>
            )}
          </div>
        ) : (
          <div className="graph-hint">Click a type to inspect its instances</div>
        )}
        <button
          className="graph-fit-btn"
          onClick={() => sigmaRef.current?.getCamera().animatedReset({ duration: 600 })}
          title="Fit the whole graph in view"
        >
          Fit
        </button>
      </div>
      <div className="graph-legend">
        {SCHEMA_LABELS.map((label) => {
          const hidden = mode === 'instances' && hiddenTypes.has(label);
          const count = typeCounts.get(label) ?? 0;
          return (
            <span
              key={label}
              className={`graph-legend-item${hidden ? ' off' : ''}${mode === 'instances' ? ' clickable' : ''}`}
              onClick={mode === 'instances' ? () => toggleType(label) : undefined}
              title={mode === 'instances' ? `Toggle ${label} nodes` : `${count} in view`}
            >
              <span
                className="graph-legend-dot"
                style={{ background: theme.nodeColors[label as keyof typeof theme.nodeColors] }}
              />
              {label} · {count}
            </span>
          );
        })}
      </div>
      <div ref={containerRef} className="graph-canvas" />
      {error && (
        <div className="graph-error" title={error}>
          Refresh failed — showing last good data
        </div>
      )}
    </div>
  );
}
