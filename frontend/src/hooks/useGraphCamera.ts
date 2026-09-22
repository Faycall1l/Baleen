import { useEffect } from 'react';

import type Graph from 'graphology';
import type Sigma from 'sigma';

import { reportUx } from '../api';
import { DIM_EDGE, DIM_NODE } from '../graph/view';

interface Args {
  sigmaRef: React.MutableRefObject<Sigma | null>;
  graphRef: React.MutableRefObject<Graph | null>;
  selectedId: string | null;
  mode: 'instances' | 'schema';
  hiddenTypes: Set<string>;
}

/**
 * Camera and selection presentation: focus dimming via reducers, instant
 * placement on selection, refit + edge labels on mode switch. Reducers only
 * touch color/label/size — plain attributes every node program honors.
 */
export function useGraphCamera({ sigmaRef, graphRef, selectedId, mode, hiddenTypes }: Args): void {
  // Focus dimming through reducers. Dimming follows selection ONLY —
  // hover-driven dimming used to strobe on every mouse move across dense
  // nodes, so hover is limited to the native label.
  useEffect(() => {
    const sigma = sigmaRef.current;
    const graph = graphRef.current;
    if (!sigma || !graph) return;
    let focusSet: Set<string> | null = null;
    if (selectedId && graph.hasNode(selectedId)) {
      focusSet = new Set([selectedId, ...graph.neighbors(selectedId)]);
    }
    const fs = focusSet;
    sigma.setSetting('nodeReducer', (node, attrs) => {
      if (selectedId === node) return { ...attrs, size: (attrs.size || 3) * 1.5 };
      if (fs && !fs.has(node)) return { ...attrs, color: DIM_NODE, label: '' };
      // Focused neighborhood reads bigger, which also lifts it over the
      // label-size threshold: selecting a node guarantees its neighborhood
      // is labeled, not just brightened.
      if (fs && fs.has(node)) return { ...attrs, size: (attrs.size || 3) * 1.25 };
      return attrs;
    });
    sigma.setSetting('edgeReducer', (edge, attrs) => {
      if (!fs) return attrs;
      const [s, t] = graph.extremities(edge);
      return fs.has(s) && fs.has(t) ? attrs : { ...attrs, color: DIM_EDGE };
    });
    try {
      sigma.refresh();
    } catch {
      /* selection dimming must never blank the canvas */
    }
  }, [selectedId, sigmaRef, graphRef]);

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
  }, [mode, sigmaRef]);

  // Refit on mode switch so each view opens framed.
  useEffect(() => {
    const sigma = sigmaRef.current;
    if (!sigma) return;
    reportUx('fx-fit', `mode=${mode}`);
    sigma.getCamera().animatedReset({ duration: 600 });
  }, [mode, sigmaRef]);

  // Place the camera on the selected node, keeping the zoom level. Instant
  // setState, not animate: no async animation machinery in the click path.
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
      camera.setState({ x, y, angle: camera.angle, ratio: camera.ratio });
    } catch {
      /* a failed placement must never break selection */
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId]);
}
