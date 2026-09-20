import { useEffect, useRef, useState } from 'react';

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
// Sigma + graphology renderer.
//
// Motion model: the layout is computed ONCE, synchronously, on first paint
// (deterministic circle seed + one ForceAtlas2 pass), then never again. Later
// polls merge into the same graphology instance — positions persist, newcomers
// are placed at their neighborhood centroid, removals drop out — and sigma
// itself runs no simulation at all. The map is static by construction: no
// popping, no flicker, no perpetual animation. Pan/zoom/drag still work;
// drag simply rewrites the node's coordinates.
// ---------------------------------------------------------------------------

const FA2_ITERATIONS_SMALL = 250;
const FA2_ITERATIONS_LARGE = 100;
const LARGE_GRAPH = 500;

const DIM_NODE = '#2b2b31';
const DIM_EDGE = 'rgba(61,61,69,0.15)';

function nodeColor(label: string): string {
  return theme.nodeColors[label as keyof typeof theme.nodeColors] ?? theme.accent;
}

function nodeSize(label: string, degree: number): number {
  const base = label === 'Event' ? 2 : 3.2;
  return base * (1 + Math.min(degree, 16) * 0.06);
}

function hashStr(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return (h >>> 0) / 4294967295;
}

export default function GraphView({ data, error, selectedId, onSelect }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const graphRef = useRef<Graph | null>(null);
  const sigmaRef = useRef<Sigma | null>(null);
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;
  const [hovered, setHovered] = useState<string | null>(null);

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
      if (!graph.hasNode(n.id)) {
        graph.addNode(n.id, {});
        added.push(n.id);
      }
      graph.replaceNodeAttributes(n.id, {
        label: n.label,
        key: n.key,
        title: n.title,
        props: n.props,
        tweet_url: n.tweet_url,
      });
    }
    const seenEdges = new Set<string>();
    for (const l of data.links) {
      if (!graph.hasNode(l.source) || !graph.hasNode(l.target)) continue;
      const key = `${l.source}>${l.target}:${l.type}`;
      seenEdges.add(key);
      if (!graph.hasEdge(key)) graph.addEdgeWithKey(key, l.source, l.target, { type: l.type });
    }
    graph.forEachEdge((e) => {
      if (!seenEdges.has(e)) graph.dropEdge(e);
    });
    graph.forEachNode((n) => {
      if (!seen.has(n)) graph.dropNode(n);
    });
    graph.forEachNode((n) => {
      const label = graph.getNodeAttribute(n, 'label') as string;
      graph.setNodeAttribute(n, 'size', nodeSize(label, graph.degree(n)));
      graph.setNodeAttribute(n, 'color', nodeColor(label));
    });

    let sigma = sigmaRef.current;
    if (!sigma) {
      // First paint: deterministic circle, one synchronous FA2 pass, then
      // render. Nothing animates at any point.
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
        labelSize: 12,
        labelColor: { color: theme.textDim },
        labelFont: 'system-ui, sans-serif',
        labelRenderedSizeThreshold: 7,
        defaultNodeColor: theme.accent,
        defaultEdgeColor: '#3d3d45',
        minCameraRatio: 0.05,
        maxCameraRatio: 20,
      });
      sigmaRef.current = fresh;

      fresh.on('clickNode', ({ node }) => {
        const g = graphRef.current;
        if (!g || !g.hasNode(node)) return;
        const a = g.getNodeAttributes(node);
        onSelectRef.current({
          id: node,
          label: a.label,
          key: a.key,
          title: a.title,
          props: a.props,
          tweet_url: a.tweet_url ?? null,
        });
      });
      fresh.on('clickStage', () => onSelectRef.current(null));
      fresh.on('enterNode', ({ node }) => setHovered(node));
      fresh.on('leaveNode', () => setHovered(null));

      let dragged: string | null = null;
      fresh.on('downNode', (e) => {
        dragged = e.node;
        fresh.getCamera().disable();
      });
      fresh.getMouseCaptor().on('mousemove', (e) => {
        const g = graphRef.current;
        if (!dragged || !g || !g.hasNode(dragged)) return;
        const pos = fresh.viewportToGraph(e);
        g.setNodeAttribute(dragged, 'x', pos.x);
        g.setNodeAttribute(dragged, 'y', pos.y);
        e.preventSigmaDefault();
      });
      const endDrag = () => {
        dragged = null;
        fresh.getCamera().enable();
      };
      fresh.getMouseCaptor().on('mouseup', endDrag);
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
      sigma.refresh();
    }
  }, [data]);

  // Focus dimming: selection (or hover) keeps its 1-hop neighborhood bright;
  // everything else fades via reducers (no custom paint code needed).
  useEffect(() => {
    const sigma = sigmaRef.current;
    const graph = graphRef.current;
    if (!sigma || !graph) return;
    const focus = selectedId ?? hovered;
    let focusSet: Set<string> | null = null;
    if (focus && graph.hasNode(focus)) {
      focusSet = new Set([focus, ...graph.neighbors(focus)]);
    }
    const fs = focusSet;
    sigma.setSetting('nodeReducer', (node, attrs) => {
      if (selectedId === node) return { ...attrs, size: (attrs.size || 3) * 1.5 };
      if (fs && !fs.has(node)) return { ...attrs, color: DIM_NODE, label: '' };
      return attrs;
    });
    sigma.setSetting('edgeReducer', (edge, attrs) => {
      if (!fs) return attrs;
      const [s, t] = graph.extremities(edge);
      return fs.has(s) && fs.has(t) ? attrs : { ...attrs, color: DIM_EDGE };
    });
    sigma.refresh();
  }, [selectedId, hovered]);

  // Glide the camera to the selected node, keeping the zoom level.
  useEffect(() => {
    const sigma = sigmaRef.current;
    const graph = graphRef.current;
    if (!sigma || !graph || !selectedId || !graph.hasNode(selectedId)) return;
    const camera = sigma.getCamera();
    camera.animate(
      {
        x: graph.getNodeAttribute(selectedId, 'x'),
        y: graph.getNodeAttribute(selectedId, 'y'),
        ratio: camera.ratio,
      },
      { duration: 600 },
    );
  }, [selectedId]);

  // Teardown: sigma owns WebGL resources that must be released.
  useEffect(() => {
    return () => {
      sigmaRef.current?.kill();
      sigmaRef.current = null;
      graphRef.current = null;
    };
  }, []);

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
      <div className="graph-legend">
        {(Object.keys(theme.nodeColors) as (keyof typeof theme.nodeColors)[]).map((label) => (
          <span key={label} className="graph-legend-item">
            <span
              className="graph-legend-dot"
              style={{ background: theme.nodeColors[label] }}
            />
            {label}
          </span>
        ))}
      </div>
      <div ref={containerRef} className="graph-canvas" />
    </div>
  );
}
