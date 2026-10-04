import { useEffect, useMemo, useRef } from 'react';

import Graph from 'graphology';

import type { GraphData, GraphNode } from '../types';
import { applyVisibility } from '../graph/visibility';
import { nearestInDirection, type PointNode } from '../graph/spatial';
import { SCHEMA_LABELS } from '../graph/schema';
import GraphLegend from './GraphLegend';
import GraphToolbar from './GraphToolbar';
import { useGraphCamera } from '../hooks/useGraphCamera';
import { useGraphSearch } from '../hooks/useGraphSearch';
import { useGraphSelection } from '../hooks/useGraphSelection';
import { useGraphSync } from '../hooks/useGraphSync';
import { useSigmaInstance } from '../hooks/useSigmaInstance';

interface Props {
  data: GraphData | null;
  error: string | null;
  selectedId: string | null;
  onSelect: (node: GraphNode | null) => void;
  onHoverEdgeChange?: (rel: string | null) => void;
  onRetry?: () => void;
  hiddenTypes: Set<string>;
  onToggleType: (label: string) => void;
  onSoloType: (label: string) => void;
  onClearTypes: () => void;
  scrubDaysAgo: number | null;
  scrubMaxDays: number;
  onScrubChange: (daysAgo: number | null) => void;
  showEvents: boolean;
  onToggleEvents: () => void;
  loadingHint?: string;
  warming?: boolean;
}

// ---------------------------------------------------------------------------
// Sigma + graphology explorer.
//
// Motion model: the layout is deterministic radial-by-type, computed ONCE,
// synchronously, on first paint, then never again. Later
// polls merge into the same graphology instance — positions persist, newcomers
// are placed at their neighborhood centroid, removals drop out — and sigma
// itself runs no simulation at all. The map is static by construction: no
// popping, no flicker. Pan/zoom/drag still work; drag rewrites coordinates.
//
// Visibility model: what you see is what EXISTS in the graphology graph.
// Type filters add/drop nodes (banking positions), never paint tricks —
// custom WebGL programs don't reliably honor sigma's `hidden` flag, so
// hiding-by-paint is not used anywhere. Reducers only dim.
//
// Exploration model (Shneiderman, condensed): search to find, click to focus
// (neighborhood stays bright, rest dims), toolbar to reset, legend always
// visible, detail panel on the right for the selected node.
// ---------------------------------------------------------------------------
export default function GraphView({ data, error, selectedId, onSelect, onHoverEdgeChange, onRetry, hiddenTypes, onToggleType, onSoloType, onClearTypes, scrubDaysAgo, scrubMaxDays, onScrubChange, showEvents, onToggleEvents, loadingHint, warming }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const graphRef = useRef<Graph | null>(null);
  const posCacheRef = useRef(new Map<string, { x: number; y: number }>());
  const dataRef = useRef(data);
  dataRef.current = data;

  const selectNodeByKey = useGraphSelection({ graphRef, onSelect });

  const { sigmaRef, ensureSigma } = useSigmaInstance(
    containerRef,
    () => graphRef.current,
    {
      onNodeClick: (id) => selectNodeByKey(id),
      onStageClick: () => onSelect(null),
      onEdgeHover: (rel) => onHoverEdgeChange?.(rel),
      onNodeDragged: (id, x, y) => {
        const g = graphRef.current;
        if (g && g.hasNode(id)) {
          g.setNodeAttribute(id, 'x', x);
          g.setNodeAttribute(id, 'y', y);
        }
      },
    },
  );

  // Data sync + first-paint layout lives in useGraphSync.
  useGraphSync({
    containerRef,
    graphRef,
    posCacheRef,
    data,
    hiddenTypes,
    ensureSigma,
  });

  // Filter changes re-run membership without touching data (positions
  // of survivors are preserved by the bank/restore inside applyVisibility).
  useEffect(() => {
    const graph = graphRef.current;
    const sigma = sigmaRef.current;
    const latest = dataRef.current;
    if (!graph || !sigma || !latest) return;
    try {
      applyVisibility(graph, latest, hiddenTypes, posCacheRef.current);
    } catch {
      /* a failed membership sync must never break the view */
    }
    try {
      sigma.refresh();
    } catch {
      /* visibility flip must never blank the canvas */
    }
  }, [hiddenTypes]);

  const typeCounts = useMemo(() => {
    const counts = new Map<string, number>();
    if (!data) return counts;
    for (const n of data.nodes) counts.set(n.label, (counts.get(n.label) ?? 0) + 1);
    return counts;
  }, [data]);

  useGraphCamera({ sigmaRef, graphRef, selectedId, hiddenTypes });

  const search = useGraphSearch({
    data,
    hiddenTypes,
    showType: onToggleType,
    onSelect,
  });

  const handleFit = () => {
    const camera = sigmaRef.current?.getCamera();
    if (!camera) return;
    // Reduced motion (R30): jump instead of animating.
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      camera.setState({ x: 0.5, y: 0.5, angle: 0, ratio: 1 });
    } else {
      camera.animatedReset({ duration: 600 });
    }
  };

  // One-click escape hatch (R33): refit, drop the selection, show all types.
  const handleResetView = () => {
    onClearTypes();
    onSelect(null);
    handleFit();
  };

  // Zoom shortcuts (R29): instant ratio steps around the view center.
  // Instant, not animated: deterministic and safe under reduced motion.
  const zoomBy = (factor: number) => {
    const sigma = sigmaRef.current;
    if (!sigma) return;
    const camera = sigma.getCamera();
    const state = camera.getState();
    camera.setState({ ...state, ratio: state.ratio * factor });
  };

  // Spatial keyboard navigation (R28): arrows step selection to the
  // nearest node in that direction. Graph units map y-down onto the
  // screen, matching DOM convention.
  const moveSelection = (dx: number, dy: number) => {
    const g = graphRef.current;
    if (!g || !selectedId || !g.hasNode(selectedId)) return;
    const nodes: PointNode[] = [];
    g.forEachNode((id) => {
      const x = g.getNodeAttribute(id, 'x') as number;
      const y = g.getNodeAttribute(id, 'y') as number;
      if (Number.isFinite(x) && Number.isFinite(y)) nodes.push({ id, x, y });
    });
    const next = nearestInDirection(nodes, selectedId, dx, dy);
    if (next) selectNodeByKey(next);
  };

  if (!data) {
    return <div className="empty-hint">{loadingHint ?? 'Loading graph…'}</div>;
  }

  if (data.nodes.length === 0) {
    return (
      <div className="empty-hint">
        The graph is empty. Seed it with{' '}
        <code>python -m scripts.ingest_once --seed-ref</code> and reload.
      </div>
    );
  }

  // NOTE: fetch errors overlay the last-good canvas (see below) instead of
  // replacing it: unmounting on error then remounting on recovery replayed
  // the full layout each time, which read as an endless big bang.
  const allHidden =
    data !== null &&
    data.nodes.length > 0 &&
    data.nodes.every((n) => hiddenTypes.has(n.label));
  return (
    <div className="graph-wrap">
      <GraphToolbar
        hiddenTypes={hiddenTypes}
        typeCounts={typeCounts}
        onToggleType={onToggleType}
        onClearTypes={onClearTypes}
        query={search.query}
        onQueryChange={search.setQuery}
        searchOpen={search.open}
        onSearchOpenChange={search.setOpen}
        matches={search.matches}
        matchTotal={search.matchTotal}
        onChooseMatch={search.choose}
        onFit={handleFit}
        onResetView={handleResetView}
        scrubDaysAgo={scrubDaysAgo}
        scrubMaxDays={scrubMaxDays}
        onScrubChange={onScrubChange}
        showEvents={showEvents}
        onToggleEvents={onToggleEvents}
      />
      <GraphLegend
        // Biggest types first (R44): the eye lands on what dominates the
        // view. Stable sort keeps SCHEMA_LABELS order on ties.
        labels={[...SCHEMA_LABELS].sort(
          (a, b) => (typeCounts.get(b) ?? 0) - (typeCounts.get(a) ?? 0),
        )}
        counts={typeCounts}
        hiddenTypes={hiddenTypes}
        interactive
        onToggleType={onToggleType}
        onSoloType={onSoloType}
      />
      <div ref={containerRef} className="graph-canvas"
        tabIndex={0}
        role="application"
        aria-label="Knowledge graph canvas"
        aria-describedby="graph-canvas-help"
        onKeyDown={(e) => {
          const dir =
            e.key === 'ArrowRight'
              ? [1, 0]
              : e.key === 'ArrowLeft'
                ? [-1, 0]
                : e.key === 'ArrowUp'
                  ? [0, -1]
                  : e.key === 'ArrowDown'
                    ? [0, 1]
                    : null;
          if (dir) {
            e.preventDefault();
            moveSelection(dir[0], dir[1]);
          } else if (e.key === '+' || e.key === '=') {
            e.preventDefault();
            zoomBy(0.8);
          } else if (e.key === '-' || e.key === '_') {
            e.preventDefault();
            zoomBy(1.25);
          }
        }}
      />
      {allHidden && (
        <div className="graph-empty-filter" role="status">
          <div>Every type is hidden — the canvas is empty by filter, not by data.</div>
          <button type="button" onClick={onClearTypes}>
            Show all types
          </button>
        </div>
      )}
      {warming && (
        <div className="graph-error" title="Showing the quick first window; the full graph is still loading">
          Warming up — full graph loading…
        </div>
      )}
      {error && (
        onRetry ? (
          <button type="button" className="graph-error graph-error-action" title={error} onClick={onRetry}>
            Refresh failed — showing last good data · Retry
          </button>
        ) : (
          <div className="graph-error" title={error}>
            Refresh failed — showing last good data
          </div>
        )
      )}
      <p id="graph-canvas-help" className="visually-hidden">
        Focus the canvas, then use arrow keys to move selection between
        nodes, plus and minus to zoom. Use the Find a node search to jump
        to a node by name. Type filters in the legend hide node types.
      </p>
    </div>
  );
}
