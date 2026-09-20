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

// react-force-graph-2d mutates links' source/target into node objects after
// loading; keep the export data immutable by passing a shallow copy.
function toForceData(data: GraphData) {
  return {
    nodes: data.nodes.map((n) => ({ id: n.id, label: n.label, title: n.title })),
    links: data.links.map((l) => ({
      source: l.source,
      target: l.target,
      type: l.type,
    })),
  };
}

export default function GraphView({ data, error, selectedId, onSelect }: Props) {
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

  // Lazy import keeps the (large) force-graph bundle off the initial path.
  const forceData = toForceData(data);
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
