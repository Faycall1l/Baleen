import { useMemo, useRef } from 'react';

import ForceGraph2D from 'react-force-graph-2d';

import type { GraphData, GraphNode } from '../types';
import { theme } from '../theme';

interface Props {
  data: GraphData | null;
  error: string | null;
  selectedId: string | null;
  onSelect: (node: GraphNode | null) => void;
}

// Budgets that keep the canvas interactive as the graph grows. Past them,
// decoration (particles, far-zoom labels) switches off instead of melting
// the browser; the data itself is untouched.
const LINK_PARTICLE_BUDGET = 200;
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
}

// Reconcile incoming nodes against the live objects by id. The force engine
// stores positions/velocities ON the node objects and merges updates by
// object identity — handing it fresh instances every poll (as we used to)
// reads as a brand-new dataset and re-heats the whole simulation, which is
// the "popping" on every refresh. Reused instances stay put; only genuinely
// new nodes enter, seeded next to their already-placed neighborhood.
function reconcileNodes(
  incoming: GraphData['nodes'],
  links: GraphData['links'],
  cache: Map<string, PlacedNode>,
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
  }
  return placed;
}

export default function GraphView({ data, error, selectedId, onSelect }: Props) {
  // Live node objects, keyed by id: the engine's positions live on these
  // instances, so they must survive across polls (see reconcileNodes).
  const nodeCache = useRef(new Map<string, PlacedNode>());

  const forceData = useMemo(() => {
    if (!data) return null;
    return {
      nodes: reconcileNodes(data.nodes, data.links, nodeCache.current),
      links: data.links.map((l) => ({
        source: l.source,
        target: l.target,
        type: l.type,
      })),
    };
  }, [data]);

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
      graphData={forceData}
      backgroundColor={theme.bg}
      linkColor={() => '#3d3d45'}
      linkWidth={1.2}
      linkDirectionalParticles={data.links.length > LINK_PARTICLE_BUDGET ? 0 : 1}
      linkDirectionalParticleWidth={2}
      linkDirectionalParticleColor={() => theme.accent}
      linkLabel={(l) => (l as { type: string }).type}
      nodeRelSize={5}
      nodeLabel={(n) => `${(n as { title: string }).title} · ${(n as { label: string }).label}`}
      cooldownTime={6000}
      nodeCanvasObject={(node, ctx, globalScale) => {
        const label = node.label as string;
        const title = node.title as string;
        const color = theme.nodeColors[label as keyof typeof theme.nodeColors] ?? theme.accent;
        const size = label === 'Event' ? 4.5 : 6.5;

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

        // Labels are the per-frame cost that scales worst: on dense graphs
        // they render only once zoomed in close.
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
        ctx.arc(node.x!, node.y!, 8, 0, 2 * Math.PI);
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
