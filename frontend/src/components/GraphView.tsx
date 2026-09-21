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
// Visual model (network-readability rules): curved directed edges declutter
// crossings; labels carry a halo and render for important nodes first;
// sizes follow sqrt(degree) in a ~3x band; edges sit behind nodes at low
// opacity via dimming.
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

// Ontology (schema) overview: the graph's labels as supernodes sized by
// instance count (sqrt-scaled, per VOWL), connected by the relationship
// types actually present, with human-readable property labels. Gives the
// "what is this data" overview first; clicking a class drills into its
// instances. Pure frontend aggregation over the loaded payload — no backend
// changes. Schema ids are prefixed so they can share one graphology graph
// with the instances (hidden set decides which half renders).
const SCHEMA_LABELS = ['Politician', 'Insider', 'Committee', 'Event', 'Ticker', 'Sector'];
const SCHEMA_RADIUS = 260;

const schemaId = (label: string): string => `schema:${label}`;
const isSchemaId = (id: string): boolean => id.startsWith('schema:');
const schemaEdgeKey = (s: string, rel: string, t: string): string =>
  `schema-rel:${s}>${rel}>${t}`;

function humanizeRel(rel: string): string {
  return rel.toLowerCase().replace(/_/g, ' ');
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

// Sync the schema overlay into the shared graph: one supernode per label
// WITH instances, at fixed circle positions, plus one edge per relationship
// type present. Zero-count labels render nothing at all — an empty type
// occupying the map at full saturation reads as false importance (and can
// only ever be connectionless); the legend still lists it with (0).
// Supenodes never join any layout pass; only counts/sizes/labels update.
function syncSchemaOverlay(graph: Graph, agg: SchemaAgg): void {
  SCHEMA_LABELS.forEach((label, i) => {
    const id = schemaId(label);
    const count = agg.counts.get(label) ?? 0;
    if (count === 0) {
      if (graph.hasNode(id)) graph.dropNode(id);
      return;
    }
    const color = nodeColor(label);
    const a = (i / SCHEMA_LABELS.length) * 2 * Math.PI;
    if (!graph.hasNode(id)) {
      graph.addNode(id, {});
      graph.setNodeAttribute(id, 'x', Math.cos(a) * SCHEMA_RADIUS);
      graph.setNodeAttribute(id, 'y', Math.sin(a) * SCHEMA_RADIUS);
    }
    // Top instances by degree so the type reads as content
    // ("Politician (20): Warren, Waters"), not just a tag.
    const tops: { title: string; deg: number }[] = [];
    graph.forEachNode((n, attrs) => {
      if (isSchemaId(n) || attrs.kind !== label) return;
      tops.push({ title: attrs.title as string, deg: graph.degree(n) });
    });
    tops.sort((x, y) => y.deg - x.deg);
    const names = tops
      .slice(0, 2)
      .map((t) => truncate(t.title, 15))
      .join(', ');
    const title = count > 0 && names ? `${label} (${count}): ${names}` : `${label} (${count})`;
    graph.replaceNodeAttributes(id, {
      type: 'bordered',
      kind: label,
      label: title,
      key: label,
      title,
      props: {},
      tweet_url: null,
      color,
      borderColor: shade(color, 0.55),
      size: 10 + Math.sqrt(count) * 1.5,
      x: graph.getNodeAttribute(id, 'x'),
      y: graph.getNodeAttribute(id, 'y'),
    });
  });
  const seen = new Set<string>();
  for (const [key, r] of agg.rels) {
    seen.add(key);
    if (!graph.hasEdge(key)) {
      graph.addEdgeWithKey(key, schemaId(r.source), schemaId(r.target), {
        type: 'curvedArrow',
        label: humanizeRel(r.rel),
      });
    }
    graph.setEdgeAttribute(key, 'size', 1.5 + Math.log10(r.count + 1));
  }
  graph.forEachEdge((e) => {
    if (e.startsWith('schema-rel:') && !seen.has(e)) graph.dropEdge(e);
  });
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
  // Literature range ~3x from leaf to hub, sqrt-scaled so mega-hubs don't
  // eat the canvas; events stay small regardless of degree.
  if (label === 'Event') return 1.6 + Math.min(Math.sqrt(degree) * 0.8, 2.4);
  return 2.2 + Math.min(Math.sqrt(degree) * 1.2, 5);
}

// Default label painter plus a 1-2px background halo so text lifts off
// edges and nodes on the dark canvas. Same geometry as sigma's default.
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

export default function GraphView({ data, error, selectedId, onSelect }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const graphRef = useRef<Graph | null>(null);
  const sigmaRef = useRef<Sigma | null>(null);
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;
  // Drag gesture state, shared between the sigma handlers below and the
  // window mouseup backstop (a release outside the canvas must still free
  // the camera).
  const dragRef = useRef<{ node: string | null; dist: number; last: { x: number; y: number } | null }>({
    node: null,
    dist: 0,
    last: null,
  });
  const [query, setQuery] = useState('');
  const [searchOpen, setSearchOpen] = useState(false);
  const [mode, setMode] = useState<'instances' | 'schema'>('instances');
  // Labels hidden from the instance view (legend toggles). Schema view is
  // always complete; hidden types only apply to instances.
  const [hiddenTypes, setHiddenTypes] = useState<Set<string>>(new Set());

  // Below this many px of pointer travel the gesture is a click (real clicks
  // always micro-move); beyond it, a drag. Sigma's own clickNode is unreliable
  // here because any move poisons it, so selection goes through mouseup.
  const CLICK_DRAG_PX = 5;

  // Selection entry point: schema nodes drill into their type, instance
  // nodes select for the inspector. Never throws, never moves the camera.
  const selectNodeByKey = (node: string) => {
    const g = graphRef.current;
    if (!g || !g.hasNode(node)) return;
    if (isSchemaId(node)) {
      setHiddenTypes(new Set(SCHEMA_LABELS.filter((l) => l !== node.slice('schema:'.length))));
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
      dragRef.current = { node: null, dist: 0, last: null };
      sigmaRef.current?.getCamera().enable();
    };
    window.addEventListener('mouseup', backstop);
    return () => window.removeEventListener('mouseup', backstop);
  }, []);

  // Merge incoming data into the persistent graph. Positions of surviving
  // nodes are never touched; only genuinely new nodes need placement.
  useEffect(() => {
    if (!containerRef.current || !data || data.nodes.length === 0) return;

    let graph = graphRef.current;
    if (!graph) {
      graph = new Graph({ type: 'directed', allowSelfLoops: true });
      graphRef.current = graph;
    }

    const seen = new Set<string>();
    const added: string[] = [];
    for (const n of data.nodes) {
      seen.add(n.id);
      const color = nodeColor(n.label);
      if (!graph.hasNode(n.id)) {
        graph.addNode(n.id, {});
        added.push(n.id);
      }
      const display = n.label === 'Event' ? eventTitle(n.props) : n.title;
      graph.replaceNodeAttributes(n.id, {
        type: 'bordered',
        kind: n.label,
        label: display,
        key: n.key,
        title: display,
        props: n.props,
        tweet_url: n.tweet_url,
        color,
        borderColor: shade(color, 0.55),
      });
    }
    const seenEdges = new Set<string>();
    for (const l of data.links) {
      if (!graph.hasNode(l.source) || !graph.hasNode(l.target)) continue;
      const key = `${l.source}>${l.target}:${l.type}`;
      seenEdges.add(key);
      if (!graph.hasEdge(key)) {
        graph.addEdgeWithKey(key, l.source, l.target, { type: 'curvedArrow', label: l.type });
      }
    }
    // Prune removals, but never the schema overlay (it is synced below and
    // positioned once, outside the instance layout).
    graph.forEachEdge((e) => {
      if (!seenEdges.has(e) && !e.startsWith('schema-rel:')) graph.dropEdge(e);
    });
    graph.forEachNode((n) => {
      if (!seen.has(n) && !isSchemaId(n)) graph.dropNode(n);
    });
    graph.forEachNode((n) => {
      if (isSchemaId(n)) return;
      const kind = graph.getNodeAttribute(n, 'kind') as string;
      graph.setNodeAttribute(n, 'size', nodeSize(kind, graph.degree(n)));
    });

    let sigma = sigmaRef.current;
    if (!sigma) {
      // First paint: deterministic circle, one synchronous FA2 pass over
      // the instances only, then the schema overlay is added at fixed
      // positions (never part of any layout), then render.
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
      syncSchemaOverlay(graph, aggregateSchema(data.nodes, data.links));

      const fresh = new Sigma(graph, containerRef.current, {
        allowInvalidContainer: true,
        renderLabels: true,
        labelSize: 13,
        labelWeight: '500',
        labelColor: { color: theme.textDim },
        labelFont: 'system-ui, sans-serif',
        labelRenderedSizeThreshold: 5,
        labelDensity: 0.12,
        defaultDrawNodeLabel: drawLabelWithHalo,
        defaultNodeColor: theme.accent,
        defaultEdgeColor: '#3d3d45',
        minCameraRatio: 0.05,
        maxCameraRatio: 20,
        nodeProgramClasses: { bordered: BorderedNodeProgram },
        edgeProgramClasses: { curvedArrow: EdgeCurvedArrowProgram },
      });
      sigmaRef.current = fresh;
      // Open zoomed in: the interesting structure sits near the center and
      // a full-fit overview reads as dots. Runs before first paint, so the
      // user never sees the wide view.
      fresh.getCamera().setState({ x: 0, y: 0, angle: 0, ratio: 1.6 });

      // NOTE: no clickNode handler on purpose (see the click-vs-drag note
      // above): selection goes through the captor mouseup path, which also
      // covers schema drill-down via selectNodeByKey.
      fresh.on('clickStage', () => onSelectRef.current(null));

      fresh.on('downNode', (e) => {
        dragRef.current = { node: e.node, dist: 0, last: null };
        fresh.getCamera().disable();
      });
      fresh.getMouseCaptor().on('mousemove', (e) => {
        const d = dragRef.current;
        const g = graphRef.current;
        if (!d.node || !g || !g.hasNode(d.node)) return;
        if (d.last) d.dist += Math.hypot(e.x - d.last.x, e.y - d.last.y);
        d.last = { x: e.x, y: e.y };
        if (d.dist < CLICK_DRAG_PX) return;
        const pos = fresh.viewportToGraph(e);
        g.setNodeAttribute(d.node, 'x', pos.x);
        g.setNodeAttribute(d.node, 'y', pos.y);
        e.preventSigmaDefault();
      });
      fresh.getMouseCaptor().on('mouseup', () => {
        const d = dragRef.current;
        dragRef.current = { node: null, dist: 0, last: null };
        fresh.getCamera().enable();
        if (d.node && d.dist < CLICK_DRAG_PX) selectNodeByKey(d.node);
      });
    } else {
      // Later polls: place newcomers at their placed neighborhood centroid
      // (deterministic ring fallback), leave everything else exactly alone.
      const placed = new Map<string, { x: number; y: number }>();
      graph.forEachNode((n) => {
        if (!added.includes(n)) {
          placed.set(n, {
            x: graph.getNodeAttribute(n, 'x'),
            y: graph.getNodeAttribute(n, 'y'),
          });
        }
      });
      const neighborPos = new Map<string, { x: number; y: number }[]>();
      graph.forEachEdge((_e, _attrs, s, t) => {
        const pa = placed.get(s);
        const pb = placed.get(t);
        if ((pa && pb) || (!pa && !pb)) return;
        const unknown = pa ? t : s;
        const known = (pa ?? pb)!;
        if (placed.has(unknown)) return;
        const list = neighborPos.get(unknown) ?? [];
        list.push(known);
        neighborPos.set(unknown, list);
      });
      for (const id of added) {
        const neighbors = neighborPos.get(id);
        if (neighbors?.length) {
          graph.setNodeAttribute(id, 'x', neighbors.reduce((sum, p) => sum + p.x, 0) / neighbors.length);
          graph.setNodeAttribute(id, 'y', neighbors.reduce((sum, p) => sum + p.y, 0) / neighbors.length);
        } else {
          const a = hashStr(id) * 2 * Math.PI;
          const r = 40 + hashStr(id + ':r') * 80;
          graph.setNodeAttribute(id, 'x', Math.cos(a) * r);
          graph.setNodeAttribute(id, 'y', Math.sin(a) * r);
        }
      }
      syncSchemaOverlay(graph, aggregateSchema(data.nodes, data.links));
      try {
        sigma.refresh();
      } catch {
        /* a failed paint must never take down the view; next poll refreshes */
      }
    }
  }, [data]);

  // Focus dimming + mode/type visibility, all through reducers (no custom
  // paint code needed). Schema mode shows only the overlay; instances mode
  // shows instances minus legend-hidden types. Dimming follows selection
  // ONLY — hover-driven dimming used to strobe on every mouse move across
  // dense nodes, so hover is limited to the native label affordance.
  useEffect(() => {
    const sigma = sigmaRef.current;
    const graph = graphRef.current;
    if (!sigma || !graph) return;
    const visible = (id: string, label: string): boolean => {
      if (mode === 'schema') return isSchemaId(id);
      if (isSchemaId(id)) return false;
      return !hiddenTypes.has(label);
    };
    let focusSet: Set<string> | null = null;
    if (selectedId && graph.hasNode(selectedId)) {
      focusSet = new Set([selectedId, ...graph.neighbors(selectedId)]);
    }
    const fs = focusSet;
    sigma.setSetting('nodeReducer', (node, attrs) => {
      const kind = (attrs.kind as string) ?? '';
      if (!visible(node, kind)) return { ...attrs, hidden: true };
      if (selectedId === node) return { ...attrs, size: (attrs.size || 3) * 1.5 };
      if (fs && !fs.has(node)) return { ...attrs, color: DIM_NODE, label: '' };
      // Focused neighborhood reads bigger, which also lifts it over the
      // label-size threshold: selecting a node guarantees its neighborhood
      // is labeled, not just brightened.
      if (fs && fs.has(node)) return { ...attrs, size: (attrs.size || 3) * 1.25 };
      return attrs;
    });
    sigma.setSetting('edgeReducer', (edge, attrs) => {
      const [s, t] = graph.extremities(edge);
      const sk = (graph.getNodeAttribute(s, 'kind') as string) ?? '';
      const tk = (graph.getNodeAttribute(t, 'kind') as string) ?? '';
      if (!visible(s, sk) || !visible(t, tk)) return { ...attrs, hidden: true };
      if (fs && !(fs.has(s) && fs.has(t))) return { ...attrs, color: DIM_EDGE };
      return attrs;
    });
    try {
      sigma.refresh();
    } catch {
      /* selection dimming must never blank the canvas */
    }
  }, [selectedId, mode, hiddenTypes]);

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

  // Glide the camera to the selected node, keeping the zoom level. Skips
  // hidden nodes (e.g. type toggled off) instead of flying to a stale spot.
  useEffect(() => {
    const sigma = sigmaRef.current;
    const graph = graphRef.current;
    if (!sigma || !graph || !selectedId || !graph.hasNode(selectedId)) return;
    if (
      mode !== 'schema' &&
      hiddenTypes.has(graph.getNodeAttribute(selectedId, 'kind') as string)
    ) {
      return;
    }
    const x = graph.getNodeAttribute(selectedId, 'x') as number;
    const y = graph.getNodeAttribute(selectedId, 'y') as number;
    if (!Number.isFinite(x) || !Number.isFinite(y)) return;
    try {
      const camera = sigma.getCamera();
      camera.animate({ x, y, ratio: camera.ratio }, { duration: 600 });
    } catch {
      /* a failed glide must never break selection */
    }
  }, [selectedId, mode, hiddenTypes]);

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
    onSelect(m);
    setQuery('');
    setSearchOpen(false);
  };

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

  if (error) {
    return (
      <div className="empty-hint">
        <div className="error">{error}</div>
      </div>
    );
  }

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
    </div>
  );
}
