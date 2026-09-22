import { useEffect, useRef } from 'react';

import Graph from 'graphology';
import forceAtlas2 from 'graphology-layout-forceatlas2';
import type Sigma from 'sigma';

import { reportUx } from '../api';
import { hashStr } from '../graph/geometry';
import { applyVisibility } from '../graph/visibility';
import type { GraphData } from '../types';

const FA2_ITERATIONS_SMALL = 250;
const FA2_ITERATIONS_LARGE = 100;
const LARGE_GRAPH = 500;

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
  mode: 'instances' | 'schema';
  hiddenTypes: Set<string>;
  ensureSigma: () => Sigma | null;
}

/**
 * Syncs loaded data into the graphology graph via applyVisibility (membership,
 * attrs, edges, positions), audits coordinates, and runs one synchronous FA2
 * pass on first paint. Positions of surviving nodes are never touched, and
 * no layout ever re-runs afterwards.
 */
export function useGraphSync({
  containerRef,
  graphRef,
  posCacheRef,
  data,
  mode,
  hiddenTypes,
  ensureSigma,
}: Args): void {
  const paintedRef = useRef(false);

  useEffect(() => {
    if (!containerRef.current || !data || data.nodes.length === 0) return;

    let graph = graphRef.current;
    if (!graph) {
      graph = new Graph({ type: 'directed', allowSelfLoops: true });
      graphRef.current = graph;
    }

    applyVisibility(graph, data, mode, hiddenTypes, posCacheRef.current);
    const repaired = sanitizePositions(graph);
    if (repaired > 0) reportUx('nan-repair', `repaired=${repaired} order=${graph.order}`);

    const first = !paintedRef.current;
    if (first) {
      paintedRef.current = true;
      // First paint: deterministic circle over the rendered set (initial mode
      // is always instances), then one synchronous FA2 pass. Nothing animates
      // at any point.
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
      const fa2nan = sanitizePositions(graph);
      reportUx('layout-done', `order=${order} nan=${fa2nan}`);
    }

    const sigma = ensureSigma();
    if (!sigma) return;
    if (first) {
      // Open zoomed in: the interesting structure sits near the center and
      // a full-fit overview reads as dots. Runs before first paint, so the
      // user never sees the wide view.
      sigma.getCamera().setState({ x: 0, y: 0, angle: 0, ratio: 1.6 });
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
