import { useEffect, useRef } from 'react';

import { createNodeBorderProgram } from '@sigma/node-border';
import type Graph from 'graphology';
import Sigma from 'sigma';

import { truncate } from '../graph/nodes';
import { theme } from '../theme';

function drawLabelWithHalo(
  context: CanvasRenderingContext2D,
  data: {
    x: number;
    y: number;
    size: number;
    label?: string | null;
    sublabel?: string | null;
    color: string;
  },
  settings: { labelSize: number; labelFont: string; labelWeight: string },
): void {
  if (!data.label) return;
  const { labelSize: size, labelFont: font, labelWeight: weight } = settings;
  // ER-entity box (R50): header row plus field rows inside a bordered box,
  // mermaid-style — not floating text. Single-line labels keep the old
  // halo treatment.
  const title = truncate(data.label, 30);
  const fields = data.sublabel ? [data.sublabel] : [];
  if (fields.length === 0) {
    const lx = data.x + data.size + 3;
    const ly = data.y + size / 3;
    context.font = `${weight} ${size}px ${font}`;
    context.lineWidth = 3;
    context.strokeStyle = theme.bg;
    context.strokeText(title, lx, ly);
    context.fillStyle = theme.text;
    context.fillText(title, lx, ly);
    return;
  }
  const titleFont = `${weight} ${size}px ${font}`;
  const fieldFont = `${size - 2}px ${font}`;
  context.font = titleFont;
  const titleW = context.measureText(title).width;
  context.font = fieldFont;
  const fieldW = Math.max(...fields.map((f) => context.measureText(f).width));
  const padX = 7;
  const padTop = 6;
  const rowGap = 3;
  const boxW = Math.max(titleW, fieldW) + padX * 2;
  const boxH = padTop + size + rowGap + (size - 2) * fields.length + padTop;
  const bx = data.x + data.size + 4;
  const by = data.y - size - 2;
  context.fillStyle = 'rgba(22, 22, 27, 0.94)';
  context.strokeStyle = data.color;
  context.lineWidth = 1.2;
  context.beginPath();
  context.roundRect(bx, by, boxW, boxH);
  context.fill();
  context.stroke();
  // Header divider, ER-table style.
  const dividerY = by + padTop + size + rowGap / 2;
  context.strokeStyle = 'rgba(255, 255, 255, 0.12)';
  context.lineWidth = 1;
  context.beginPath();
  context.moveTo(bx + padX, dividerY);
  context.lineTo(bx + boxW - padX, dividerY);
  context.stroke();
  context.font = titleFont;
  context.fillStyle = theme.text;
  context.fillText(title, bx + padX, by + padTop + size - 2);
  context.font = fieldFont;
  context.fillStyle = theme.textDim;
  fields.forEach((field, i) => {
    context.fillText(field, bx + padX, by + padTop + size + rowGap + (size - 2) * (i + 1) - 3);
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

interface Callbacks {
  onNodeClick: (id: string) => void;
  onStageClick: () => void;
  onNodeDragged: (id: string, x: number, y: number) => void;
  onEdgeHover: (rel: string | null) => void;
}

/**
 * Owns the sigma renderer instance: creation (once per mount, against the
 * caller-provided graph), event wiring (native click/stage/drag), and
 * teardown (WebGL release + camera backstop). Returns the store plus an
 * ensure() that creates on first use.
 *
 * Dragging is manual (sigma core pans the camera but never moves nodes):
 * press moves the node, camera stays. Click detection stays native.
 */
export function useSigmaInstance(
  containerRef: React.RefObject<HTMLDivElement | null>,
  getGraph: () => Graph | null,
  cbs: Callbacks,
): { sigmaRef: React.MutableRefObject<Sigma | null>; ensureSigma: () => Sigma | null } {
  const sigmaRef = useRef<Sigma | null>(null);
  const cbsRef = useRef(cbs);
  cbsRef.current = cbs;
  const dragRef = useRef<string | null>(null);

  // Backstop: releasing outside the canvas still ends any drag and frees
  // the camera, otherwise pan/zoom stay dead until the next gesture.
  useEffect(() => {
    const backstop = () => {
      dragRef.current = null;
      sigmaRef.current?.getCamera().enable();
    };
    window.addEventListener('mouseup', backstop);
    return () => {
      window.removeEventListener('mouseup', backstop);
      sigmaRef.current?.kill();
      sigmaRef.current = null;
    };
  }, []);

  const ensureSigma = (): Sigma | null => {
    if (sigmaRef.current) return sigmaRef.current;
    const container = containerRef.current;
    const graph = getGraph();
    if (!container || !graph) return null;
    const fresh = new Sigma(graph, container, {
      allowInvalidContainer: true,
      renderLabels: true,
      labelSize: 13,
      labelWeight: '500',
      labelColor: { color: theme.textDim },
      labelFont: 'system-ui, sans-serif',
      // Labels are the top overlap offender in dense pockets: only hubs
      // clear the bar at overview zoom, the rest appear on zoom/select.
      labelRenderedSizeThreshold: 7,
      labelDensity: 0.12,
      // Label gate: size / sqrt(ratio) >= threshold after the LabelGrid
      // pre-selects ceil(density / ratio^2) biggest candidates per cell.
      // Hub labels visible from the open view; zoom/select reveal the rest.
      defaultDrawNodeLabel: drawLabelWithHalo,
      defaultNodeColor: theme.accent,
      defaultEdgeColor: '#2b2b31', // dimmer: edges recede behind nodes
      minCameraRatio: 0.05,
      maxCameraRatio: 20,
      // Generous click-vs-drag tolerance: hand tremor emits well over the
      // default 3 mousemove events, which silently ate real clicks.
      draggedEventsTolerance: 14,
      nodeProgramClasses: { bordered: BorderedNodeProgram },
    });
    fresh.getCamera().setState({ x: 0, y: 0, angle: 0, ratio: 1.6 });
    fresh.on('clickNode', ({ node }) => {
      cbsRef.current.onNodeClick(node);
    });
    fresh.on('clickStage', () => cbsRef.current.onStageClick());
    // Edge readout (R35): hovering a relationship names it in the status
    // bar. Labels stay off the canvas — the mesh stays clean.
    fresh.on('enterEdge', ({ edge }) => {
      const g = getGraph();
      const rel = g && g.hasEdge(edge) ? g.getEdgeAttribute(edge, 'label') : null;
      cbsRef.current.onEdgeHover(typeof rel === 'string' && rel ? rel : null);
    });
    fresh.on('leaveEdge', () => cbsRef.current.onEdgeHover(null));
    fresh.on('downNode', (e) => {
      dragRef.current = e.node;
      fresh.getCamera().disable();
    });
    fresh.getMouseCaptor().on('mousemove', (e) => {
      const dragged = dragRef.current;
      const g = getGraph();
      if (!dragged || !g || !g.hasNode(dragged)) return;
      const pos = fresh.viewportToGraph(e);
      cbsRef.current.onNodeDragged(dragged, pos.x, pos.y);
      e.preventSigmaDefault();
    });
    fresh.getMouseCaptor().on('mouseup', () => {
      dragRef.current = null;
      fresh.getCamera().enable();
    });
    sigmaRef.current = fresh;
    return fresh;
  };

  return { sigmaRef, ensureSigma };
}
