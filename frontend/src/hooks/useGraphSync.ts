import { useEffect, useRef } from 'react';

import Graph from 'graphology';
import type Sigma from 'sigma';

import { hashStr } from '../graph/geometry';
import { applyVisibility } from '../graph/visibility';
import { forceLayout } from '../graph/forceLayout';
import type { GraphData } from '../types';

// Scan for non-finite coordinates (NaN/Infinity poison GPU batches and can
// blank the whole canvas) and reset offenders to a deterministic ring.
// Returns the repaired count for diagnostics.
function sanitizePositions(graph: Graph): number {
  let repaired = 0;
  graph.forEachNode((id) => {
    const x = graph.getNodeAttribute(id, 'x') as number;
    const y = graph.getNodeAttribute(id, 'y') as number;
    if (!Number.isFinite(x) || !Number.isFinite(y)) {
      const a = hashStr(id) * 2 * Math.PI;
      const r = 40 + hashStr(id + ':r') * 80;
      graph.setNodeAttribute(id, 'x', Math.cos(a) * r);
      graph.setNodeAttribute(id, 'y', Math.sin(a) * r);
      repaired += 1;
    }
  });
  return repaired;
}

interface Args {
  containerRef: React.RefObject<HTMLDivElement | null>;
  graphRef: React.MutableRefObject<Graph | null>;
  posCacheRef: React.MutableRefObject<Map<string, { x: number; y: number }>>;
  data: GraphData | null;
  hiddenTypes: Set<string>;
  ensureSigma: () => Sigma | null;
}

/**
 * Syncs loaded data into the graphology graph via applyVisibility (membership,
 * attrs, edges, positions), audits coordinates, and applies the deterministic
 * radial-by-type layout on first paint (P2). Positions of surviving nodes are
 * never touched, and no layout ever re-runs afterwards.
 */
export function useGraphSync({
  containerRef,
  graphRef,
  posCacheRef,
  data,
  hiddenTypes,
  ensureSigma,
}: Args): void {
  const paintedRef = useRef(false);
  const paintedOrderRef = useRef(0);

  useEffect(() => {
    if (!containerRef.current || !data || data.nodes.length === 0) return;

    let graph = graphRef.current;
    if (!graph) {
      graph = new Graph({ type: 'directed', allowSelfLoops: true });
      graphRef.current = graph;
    }

    applyVisibility(graph, data, hiddenTypes, posCacheRef.current);
    sanitizePositions(graph);

    // Layout on first paint AND on staged upgrades (P1 boots a small window,
    // then swaps in the full snapshot): a >30% order jump means a new window,
    // not drift — relayout so the full graph gets real clusters instead of
    // accreting around the small-window map. Steady-state polls hold order
    // and never trip this, so no flicker.
    const first = !paintedRef.current || data.nodes.length > paintedOrderRef.current * 1.3;
    if (first) {
      // First paint: one-shot ForceAtlas2 from deterministic radial seeds
      // (R19). Same data always yields the same map — no random hairball —
      // and the engine never runs live, so nothing animates afterwards.
      // Staged upgrades additionally seed survivors from their live spots
      // (F4) so the map settles instead of jumping.
      let existing: Map<string, { x: number; y: number }> | undefined;
      if (paintedRef.current) {
        existing = new Map();
        graph.forEachNode((id) => {
          const x = graph.getNodeAttribute(id, 'x') as number;
          const y = graph.getNodeAttribute(id, 'y') as number;
          if (Number.isFinite(x) && Number.isFinite(y)) existing!.set(id, { x, y });
        });
      }
      paintedRef.current = true;
      paintedOrderRef.current = data.nodes.length;
      const positions = forceLayout(data, existing);
      graph.forEachNode((n) => {
        const p = positions.get(n);
        if (p) {
          graph.setNodeAttribute(n, 'x', p.x);
          graph.setNodeAttribute(n, 'y', p.y);
        }
      });
      sanitizePositions(graph);
    }

    const sigma = ensureSigma();
    if (!sigma) return;
    if (first) {
      // Open zoomed in on the layout center: sigma rescales graph coordinates
      // into normalized [0,1] space (autoRescale), so the camera ALSO lives
      // in normalized space — (0,0) is the bottom-left corner of the content
      // (half-empty canvas), and graph-unit coords point into the void.
      // (0.5, 0.5) is the content center by construction; 1.6 keeps the
      // zoomed-in open (a full fit reads as dots). Verified against sigma's
      // matrixFromCamera + animatedReset({0.5, 0.5, ratio 1}) sources.
      sigma.getCamera().setState({ x: 0.5, y: 0.5, angle: 0, ratio: 1.6 });
    } else {
      try {
        sigma.refresh();
      } catch {
        /* a failed paint must never take down the view; next poll refreshes */
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data]);
}
