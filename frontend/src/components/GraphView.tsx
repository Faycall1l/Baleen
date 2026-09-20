import { useEffect, useMemo, useRef } from 'react';

import ForceGraph2D from 'react-force-graph-2d';

import type { GraphData, GraphNode } from '../types';
import { theme } from '../theme';

interface Props {
  data: GraphData | null;
  error: string | null;
  selectedId: string | null;
  onSelect: (node: GraphNode | null) => void;
}

// Budgets that keep the canvas interactive as the graph grows. Past the
// label budget, far-zoom labels switch off instead of melting the browser;
// the data itself is untouched.
const LABEL_NODE_BUDGET = 800;
const LABEL_ZOOM = 1.4;

// Small deterministic hash for seeding initial positions (stable across
// polls so first paint doesn't depend on Math.random ordering).
function hashStr(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return (h >>> 0) / 4294967295;
}

interface PlacedNode {
  id: string;
  label: string;
  title: string;
  x?: number;
  y?: number;
  // Pinned coordinates. Once set, the node ignores all forces: this is what
  // keeps the map frozen between polls (see below).
  fx?: number;
  fy?: number;
}

// Reconcile incoming nodes against the live objects by id. The force engine
// stores positions/velocities ON the node objects and merges updates by
// object identity — handing it fresh instances every poll reads as a
// brand-new dataset and re-heats the whole simulation (the "popping" on
// every refresh). Reused instances keep their layout.
//
// Motion model: the map settles ONCE on first paint, then freezes. After the
// engine stops, every node is pinned (fx/fy) and newcomers arrive pre-pinned
// at calm seed spots — later polls never move anything, so the map is a
// stable surface to explore instead of a living jelly. Dragging still works:
// the dropped node re-pins where you leave it.
function reconcileNodes(
  incoming: GraphData['nodes'],
  links: GraphData['links'],
  cache: Map<string, PlacedNode>,
  frozen: boolean,
): PlacedNode[] {
  const seen = new Set<string>();
  const placed: PlacedNode[] = [];
  for (const n of incoming) {
    seen.add(n.id);
    let node = cache.get(n.id);
    if (!node) {
      node = { id: n.id, label: n.label, title: n.title };
      cache.set(n.id, node);
      placed.push(node);
    } else {
      node.label = n.label;
      node.title = n.title;
      placed.push(node);
    }
  }
  for (const id of [...cache.keys()]) {
    if (!seen.has(id)) cache.delete(id);
  }
  // Seed positions only for nodes the engine hasn't placed yet.
  const fresh = placed.filter((n) => n.x === undefined || n.y === undefined);
  if (fresh.length === 0) return placed;
  const anchored = placed.filter((n) => n.x !== undefined && n.y !== undefined);
  if (anchored.length === 0) {
    // First paint: deterministic circle so the graph opens spread out
    // instead of exploding from a point.
    const radius = 30 * Math.sqrt(placed.length);
    placed.forEach((n, i) => {
      const a = (i / placed.length) * 2 * Math.PI + hashStr(n.id);
      n.x = Math.cos(a) * radius;
      n.y = Math.sin(a) * radius;
    });
    return placed;
  }
  const byId = new Map(anchored.map((n) => [n.id, n]));
  const neighborPos = new Map<string, { x: number; y: number }[]>();
  for (const l of links) {
    const a = byId.get(l.source);
    const b = byId.get(l.target);
    if (a && b) continue; // both placed; nothing to seed
    const known = a ?? b;
    const unknown = a ? l.target : l.source;
    if (!known || byId.has(unknown)) continue;
    const list = neighborPos.get(unknown) ?? [];
    list.push({ x: known.x!, y: known.y! });
    neighborPos.set(unknown, list);
  }
  for (const n of fresh) {
    const neighbors = neighborPos.get(n.id);
    if (neighbors?.length) {
      n.x = neighbors.reduce((s, p) => s + p.x, 0) / neighbors.length;
      n.y = neighbors.reduce((s, p) => s + p.y, 0) / neighbors.length;
    } else {
      // No placed neighborhood: deterministic ring near the center.
      const a = hashStr(n.id) * 2 * Math.PI;
      const r = 40 + hashStr(n.id + ':r') * 80;
      n.x = Math.cos(a) * r;
      n.y = Math.sin(a) * r;
    }
    if (frozen) {
      // The map already settled: arrive pinned so this node never moves.
      n.fx = n.x;
      n.fy = n.y;
    }
  }
  return placed;
}

// Pin every node where it stands. Called when the engine stops (first
// settle, and any later reheat) so the layout freezes in place.
function freezeAll(cache: Map<string, PlacedNode>): void {
  for (const n of cache.values()) {
    if (n.x !== undefined && n.y !== undefined) {
      n.fx = n.x;
      n.fy = n.y;
    }
  }
}

export default function GraphView({ data, error, selectedId, onSelect }: Props) {
  // Live node objects, keyed by id: the engine's positions live on these
  // instances, so they must survive across polls (see reconcileNodes).
  const nodeCache = useRef(new Map<string, PlacedNode>());
  // False until the first settle completes; afterwards newcomers arrive
  // pre-pinned and nothing ever moves again.
  const frozenRef = useRef(false);
  const fgRef = useRef<any>(null);

  // Glide the camera to the selected node (zoom untouched): on a large
  // frozen map this is what makes selection feel connected to the view.
  useEffect(() => {
    if (!selectedId) return;
    const n = nodeCache.current.get(selectedId);
    if (n?.x !== undefined && n?.y !== undefined) {
      fgRef.current?.centerAt?.(n.x, n.y, 600);
    }
  }, [selectedId]);

  const forceData = useMemo(() => {
    if (!data) return null;
    return {
      nodes: reconcileNodes(data.nodes, data.links, nodeCache.current, frozenRef.current),
      links: data.links.map((l) => ({
        source: l.source,
        target: l.target,
        type: l.type,
      })),
    };
  }, [data]);

  // Focus set: the selected node plus its 1-hop neighborhood. Everything
  // outside it renders dimmed so a dense graph stays readable while
  // inspecting one corner of it. Null = no selection, full brightness.
  const highlight = useMemo(() => {
    if (!selectedId || !data) return null;
    const set = new Set<string>([selectedId]);
    for (const l of data.links) {
      if (l.source === selectedId) set.add(l.target);
      else if (l.target === selectedId) set.add(l.source);
    }
    return set;
  }, [selectedId, data]);

  // Degree per node: hubs render larger so the important nodes read at a
  // glance on a frozen map. Capped so mega-hubs don't eat the canvas.
  const degree = useMemo(() => {
    const m = new Map<string, number>();
    if (!data) return m;
    for (const l of data.links) {
      m.set(l.source, (m.get(l.source) ?? 0) + 1);
      m.set(l.target, (m.get(l.target) ?? 0) + 1);
    }
    return m;
  }, [data]);

  const nodeSize = (id: string, label: string): number => {
    const base = label === 'Event' ? 4.5 : 6.5;
    return base * (1 + Math.min(degree.get(id) ?? 0, 16) * 0.06);
  };

  if (error) {
    return (
      <div className="empty-hint">
        <div className="error">{error}</div>
      </div>
    );
  }

  if (!data || !forceData) {
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

  const dense = data.nodes.length > LABEL_NODE_BUDGET;

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
      <ForceGraph2D
      ref={fgRef}
      graphData={forceData}
      backgroundColor={theme.bg}
      linkDirectionalParticles={0}
      linkCanvasObject={(link, ctx) => {
        // Custom paint so links outside the focus set fade with their nodes.
        // Endpoints arrive as id strings on the first frame and as resolved
        // node objects afterwards; skip until coordinates exist.
        const l = link as unknown as {
          source: { id?: string; x?: number; y?: number } | string;
          target: { id?: string; x?: number; y?: number } | string;
        };
        const s = typeof l.source === 'object' ? l.source : undefined;
        const t = typeof l.target === 'object' ? l.target : undefined;
        if (s?.x === undefined || s?.y === undefined || t?.x === undefined || t?.y === undefined) return;
        const on = !highlight || (highlight.has(s.id ?? '') && highlight.has(t.id ?? ''));
        ctx.strokeStyle = on ? '#3d3d45' : 'rgba(61,61,69,0.10)';
        ctx.lineWidth = 1.2;
        ctx.beginPath();
        ctx.moveTo(s.x, s.y);
        ctx.lineTo(t.x, t.y);
        ctx.stroke();
      }}
      linkLabel={(l) => (l as { type: string }).type}
      nodeRelSize={5}
      cooldownTime={2500}
      onEngineStop={() => {
        frozenRef.current = true;
        freezeAll(nodeCache.current);
      }}
      onNodeDragEnd={(node) => {
        // Keep the user's arrangement: re-pin where dropped instead of
        // releasing back into the simulation.
        const n = node as unknown as PlacedNode;
        n.fx = n.x;
        n.fy = n.y;
      }}
      nodeCanvasObject={(node, ctx, globalScale) => {
        const label = node.label as string;
        const title = node.title as string;
        const color = theme.nodeColors[label as keyof typeof theme.nodeColors] ?? theme.accent;
        const size = nodeSize(node.id as string, label);
        const dimmed = !!highlight && !highlight.has(node.id as string);

        ctx.globalAlpha = dimmed ? 0.12 : 1;
        ctx.beginPath();
        ctx.arc(node.x!, node.y!, size, 0, 2 * Math.PI);
        ctx.fillStyle = color;
        ctx.fill();
        ctx.lineWidth = 1.5 / globalScale;
        ctx.strokeStyle = '#1b1b1f';
        ctx.stroke();

        if (node.id === selectedId) {
          ctx.beginPath();
          ctx.arc(node.x!, node.y!, size + 3 / globalScale, 0, 2 * Math.PI);
          ctx.lineWidth = 2 / globalScale;
          ctx.strokeStyle = theme.accent;
          ctx.stroke();
        }
        ctx.globalAlpha = 1;

        // Labels are the per-frame cost that scales worst: on dense graphs
        // they render only once zoomed in close. Dimmed nodes skip labels
        // so the focused neighborhood reads cleanly.
        if (dimmed) return;
        if (dense && globalScale < LABEL_ZOOM) return;
        const fontSize = 12 / globalScale;
        ctx.font = `${fontSize}px sans-serif`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'top';
        ctx.fillStyle = theme.text;
        ctx.fillText(title, node.x!, node.y! + size + 2 / globalScale);
      }}
      nodePointerAreaPaint={(node, color, ctx) => {
        ctx.fillStyle = color;
        ctx.beginPath();
        ctx.arc(node.x!, node.y!, Math.max(8, nodeSize(node.id as string, node.label as string) + 2), 0, 2 * Math.PI);
        ctx.fill();
      }}
      onNodeClick={(node) => {
        const full = data.nodes.find((n) => n.id === node.id);
        onSelect(full ?? null);
      }}
      onBackgroundClick={() => onSelect(null)}
      />
    </div>
  );
}
