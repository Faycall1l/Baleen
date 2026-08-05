import ForceGraph2D from 'react-force-graph-2d';

import type { GraphData, GraphNode } from '../types';
import { theme } from '../theme';

interface Props {
  data: GraphData | null;
  error: string | null;
  onSelect: (node: GraphNode | null) => void;
}

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

export default function GraphView({ data, error, onSelect }: Props) {
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

  return (
    <ForceGraph2D
      graphData={forceData}
      backgroundColor={theme.bg}
      linkColor={() => '#3d3d45'}
      linkWidth={1.2}
      linkDirectionalParticles={1}
      linkDirectionalParticleWidth={2}
      linkDirectionalParticleColor={() => theme.accent}
      linkLabel={(l) => (l as { type: string }).type}
      nodeRelSize={5}
      nodeLabel={(n) => `${(n as { title: string }).title} · ${(n as { label: string }).label}`}
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
  );
}
