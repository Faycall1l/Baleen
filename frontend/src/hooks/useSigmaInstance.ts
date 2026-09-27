import { useEffect, useRef } from 'react';

import { EdgeCurvedArrowProgram } from '@sigma/edge-curve';
import { createNodeBorderProgram } from '@sigma/node-border';
import type Graph from 'graphology';
import Sigma from 'sigma';

import { theme } from '../theme';

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
      labelRenderedSizeThreshold: 8,
      labelDensity: 0.08,
      // Label gate, exactly (E5: defaults pinned in sigma/settings:
      // zoomToSizeRatioFunction=Math.sqrt, itemSizesReference="screen"):
      // a label renders iff size / sqrt(ratio) >= threshold, after the
      // LabelGrid pre-selects ceil(density / ratio^2) biggest candidates
      // per cell. Live sizes run 1.6-7.2, so at the 1.6 open ratio the gate
      // needs size >= 10.1: zero labels by design (clean overview). Zooming
      // to 0.5 admits size >= 5.7 (~20 hubs); selection x1.5/x1.25 lifts
      // the focused neighborhood over the gate. Threshold 4 would admit
      // ~90 at fit — noisier. Keep (0.08, 8).
      defaultDrawNodeLabel: drawLabelWithHalo,
      defaultNodeColor: theme.accent,
      defaultEdgeColor: '#2b2b31', // dimmer: edges recede behind nodes
      minCameraRatio: 0.05,
      maxCameraRatio: 20,
      // Generous click-vs-drag tolerance: hand tremor emits well over the
      // default 3 mousemove events, which silently ate real clicks.
      draggedEventsTolerance: 14,
      nodeProgramClasses: { bordered: BorderedNodeProgram },
      edgeProgramClasses: { curvedArrow: EdgeCurvedArrowProgram },
    });
    fresh.getCamera().setState({ x: 0, y: 0, angle: 0, ratio: 1.6 });
    fresh.on('clickNode', ({ node }) => {
      cbsRef.current.onNodeClick(node);
    });
    fresh.on('clickStage', () => cbsRef.current.onStageClick());
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
