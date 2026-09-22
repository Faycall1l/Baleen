import { useEffect, useMemo, useRef, useState } from 'react';

import Graph from 'graphology';

import type { GraphData, GraphNode } from '../types';
import type { GraphMode } from '../graph/types';
import { applyVisibility } from '../graph/visibility';
import { isSchemaId, SCHEMA_LABELS } from '../graph/view';
import GraphLegend from './GraphLegend';
import GraphToolbar from './GraphToolbar';
import { useGraphSync } from '../hooks/useGraphSync';
import { useSigmaInstance } from '../hooks/useSigmaInstance';
import { reportUx } from '../api';

// Scan for non-finite coordinates (NaN/Infinity poison GPU batches and can
// blank the whole canvas) and reset offenders to a deterministic ring.
// Returns the repaired count for diagnostics.
interface Props {
  data: GraphData | null;
  error: string | null;
  selectedId: string | null;
  onSelect: (node: GraphNode | null) => void;
}

// ---------------------------------------------------------------------------
// Sigma + graphology explorer.
//
// Motion model: the layout is computed ONCE, synchronously, on first paint
// (deterministic circle seed + one ForceAtlas2 pass), then never again. Later
// polls merge into the same graphology instance — positions persist, newcomers
// are placed at their neighborhood centroid, removals drop out — and sigma
// itself runs no simulation at all. The map is static by construction: no
// popping, no flicker. Pan/zoom/drag still work; drag rewrites coordinates.
//
// Visibility model: what you see is what EXISTS in the graphology graph.
// Modes and legend filters add/drop nodes (banking positions), never paint
// tricks — custom WebGL programs don't reliably honor sigma's `hidden`
// flag, so hiding-by-paint is not used anywhere. Reducers only dim.
//
// Exploration model (Shneiderman, condensed): search to find, click to focus
// (neighborhood stays bright, rest dims), toolbar to reset, legend always
// visible, detail panel on the right for the selected node.
// ---------------------------------------------------------------------------

const DIM_NODE = '#2b2b31';
const DIM_EDGE = 'rgba(61,61,69,0.15)';

// Banked positions survive node removal so re-shown nodes return to their
// old spot instead of being re-seeded. Bounded FIFO.











export default function GraphView({ data, error, selectedId, onSelect }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const graphRef = useRef<Graph | null>(null);
  const posCacheRef = useRef(new Map<string, { x: number; y: number }>());
  const dataRef = useRef(data);
  dataRef.current = data;
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;
  const [query, setQuery] = useState('');
  const [searchOpen, setSearchOpen] = useState(false);
  const [mode, setMode] = useState<'instances' | 'schema'>('instances');
  // Labels hidden from the instance view (legend toggles). Schema view is
  // always complete; hidden types only apply to instances.
  const [hiddenTypes, setHiddenTypes] = useState<Set<string>>(new Set());

  // Selection entry point: schema nodes drill into their type, instance
  // nodes select for the inspector. Dropped (invisible) nodes can never be
  // selected because hit-testing only sees rendered nodes — and the guard
  // below makes that structural, not assumed.
  const selectNodeByKey = (node: string) => {
    const g = graphRef.current;
    if (!g || !g.hasNode(node)) return;
    if (isSchemaId(node)) {
      // Drill-down keeps the type PLUS its directly related types (read off
      // the schema edges): isolating a single type would show dots with no
      // relationships, which is exactly as useless as it sounds.
      const label = node.slice('schema:'.length);
      const keep = new Set<string>([label]);
      g.forEachEdge((e) => {
        if (!e.startsWith('schema-rel:')) return;
        const [s, t] = g.extremities(e);
        const sl = g.getNodeAttribute(s, 'kind') as string;
        const tl = g.getNodeAttribute(t, 'kind') as string;
        if (sl === label && tl) keep.add(tl);
        if (tl === label && sl) keep.add(sl);
      });
      setHiddenTypes(new Set(SCHEMA_LABELS.filter((l) => !keep.has(l))));
      setMode('instances');
      onSelectRef.current(null);
      return;
    }
    const a = g.getNodeAttributes(node);
    onSelectRef.current({
      id: node,
      label: a.kind,
      key: a.key,
      title: a.label,
      props: a.props,
      tweet_url: a.tweet_url ?? null,
    });
  };

  const { sigmaRef, ensureSigma } = useSigmaInstance(
    containerRef,
    () => graphRef.current,
    {
      onNodeClick: (id) => selectNodeByKey(id),
      onStageClick: () => onSelectRef.current(null),
      onNodeDragged: (id, x, y) => {
        const g = graphRef.current;
        if (g && g.hasNode(id)) {
          g.setNodeAttribute(id, 'x', x);
          g.setNodeAttribute(id, 'y', y);
        }
      },
    },
  );

  // Forward uncaught page errors to the backend log (deduped there) — the
  // only way to see browser failures without opening devtools.
  useEffect(() => {
    const onError = (e: ErrorEvent) => {
      reportUx('window-error', `${e.message} @ ${(e.filename || '').split('/').pop()}:${e.lineno ?? '?'}`);
    };
    window.addEventListener('error', onError);
    return () => window.removeEventListener('error', onError);
  }, []);

  // Data flow per poll: applyVisibility syncs membership, attrs, edges and
  // positions; first paint adds one FA2 pass over the rendered set, then
  // sigma is created exactly once. Positions of surviving nodes are never
  // touched at any step, and no layout ever re-runs afterwards.
  useGraphSync({
    containerRef,
    graphRef,
    posCacheRef,
    data,
    mode,
    hiddenTypes,
    ensureSigma,
  });

  // Mode/filter changes re-run membership without touching data (positions
  // of survivors are preserved by the bank/restore inside applyVisibility).
  useEffect(() => {
    const graph = graphRef.current;
    const sigma = sigmaRef.current;
    const latest = dataRef.current;
    reportUx('fx-vis-enter', `mode=${mode} hasGraph=${!!graph} hasSigma=${!!sigma} hasData=${!!latest}`);
    if (!graph || !sigma || !latest) return;
    try {
      const stats = applyVisibility(graph, latest, mode, hiddenTypes, posCacheRef.current);
      reportUx('view-ok', `mode=${mode} want=${stats.want} order=${stats.order} dataNodes=${stats.dataNodes}`);
    } catch (e) {
      reportUx('view-throw', String(e).slice(0, 300));
    }
    const bad: string[] = [];
    graph.forEachNode((id) => {
      const x = graph.getNodeAttribute(id, 'x');
      const y = graph.getNodeAttribute(id, 'y');
      if (typeof x !== 'number' || typeof y !== 'number' || !Number.isFinite(x) || !Number.isFinite(y)) {
        bad.push(id);
      }
    });
    reportUx('view', `mode=${mode} hidden=[${[...hiddenTypes].join(',')}] order=${graph.order} bad=${bad.length} ${bad.slice(0, 5).join(',')}`);
    try {
      sigma.refresh();
    } catch {
      /* visibility flip must never blank the canvas */
    }
  }, [mode, hiddenTypes]);

  const toggleType = (label: string) => {
    setHiddenTypes((prev) => {
      const next = new Set(prev);
      if (next.has(label)) next.delete(label);
      else next.add(label);
      return next;
    });
  };

  const typeCounts = useMemo(() => {
    const counts = new Map<string, number>();
    if (!data) return counts;
    for (const n of data.nodes) counts.set(n.label, (counts.get(n.label) ?? 0) + 1);
    return counts;
  }, [data]);

  // Focus dimming through reducers (color/label only — the only per-item
  // channels the custom programs are proven to honor). Dimming follows
  // selection ONLY — hover-driven dimming used to strobe on every mouse
  // move across dense nodes, so hover is limited to the native label.
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
  }, [selectedId]);

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
  }, [mode]);

  // Refit on mode switch so each view opens framed.
  useEffect(() => {
    const sigma = sigmaRef.current;
    if (!sigma) return;
    reportUx('fx-fit', `mode=${mode}`);
    sigma.getCamera().animatedReset({ duration: 600 });
  }, [mode]);

  // Place the camera on the selected node, keeping the zoom level. Instant
  // setState, not animate: no async animation machinery in the click path.
  useEffect(() => {
    const sigma = sigmaRef.current;
    const graph = graphRef.current;
    if (!sigma || !graph || !selectedId || !graph.hasNode(selectedId)) return;
    const x = graph.getNodeAttribute(selectedId, 'x') as number;
    const y = graph.getNodeAttribute(selectedId, 'y') as number;
    if (!Number.isFinite(x) || !Number.isFinite(y)) return;
    try {
      const camera = sigma.getCamera();
      camera.setState({ x, y, angle: camera.angle, ratio: camera.ratio });
    } catch {
      /* a failed placement must never break selection */
    }
  }, [selectedId]);

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!data || q.length < 2) return [];
    return data.nodes.filter((n) => n.title.toLowerCase().includes(q)).slice(0, 8);
  }, [query, data]);

  const chooseMatch = (m: GraphNode) => {
    if (mode === 'schema') setMode('instances');
    const kind = data?.nodes.find((n) => n.id === m.id)?.label;
    if (kind && mode === 'instances' && hiddenTypes.has(kind)) {
      setHiddenTypes((prev) => {
        const next = new Set(prev);
        next.delete(kind);
        return next;
      });
    }
    onSelect(m);
    setQuery('');
    setSearchOpen(false);
  };

  const handleModeChange = (m: GraphMode) => {
    if (m === 'schema') onSelectRef.current(null);
    setMode(m);
  };

  const handleFit = () => {
    sigmaRef.current?.getCamera().animatedReset({ duration: 600 });
  };

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

  // NOTE: fetch errors overlay the last-good canvas (see below) instead of
  // replacing it: unmounting on error then remounting on recovery replayed
  // the full layout each time, which read as an endless big bang.
  return (
    <div className="graph-wrap">
      <GraphToolbar
        mode={mode}
        onModeChange={handleModeChange}
        query={query}
        onQueryChange={setQuery}
        searchOpen={searchOpen}
        onSearchOpenChange={setSearchOpen}
        matches={matches}
        onChooseMatch={chooseMatch}
        onFit={handleFit}
      />
      <GraphLegend
        labels={SCHEMA_LABELS}
        counts={typeCounts}
        hiddenTypes={hiddenTypes}
        interactive={mode === 'instances'}
        onToggleType={toggleType}
      />
      <div ref={containerRef} className="graph-canvas" />
      {error && (
        <div className="graph-error" title={error}>
          Refresh failed — showing last good data
        </div>
      )}
    </div>
  );
}
