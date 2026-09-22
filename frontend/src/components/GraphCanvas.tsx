import { useEffect, useMemo, useRef, useState } from 'react';

import { fitView } from '../graph/camera';
import { computeInitialLayout, placeMissing } from '../graph/layout';
import type { Camera } from '../graph/types';
import { buildViewModel, schemaSlots, SCHEMA_LABELS } from '../graph/view';
import { paint } from '../graph/renderer';
import { theme } from '../theme';
import type { GraphData, GraphNode } from '../types';

interface Props {
  data: GraphData | null;
  error: string | null;
  selectedId: string | null;
  onSelect: (node: GraphNode | null) => void;
}

export default function GraphCanvas({ data, error, selectedId, onSelect }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const initializedRef = useRef(false);
  const [positions, setPositions] = useState(new Map<string, { x: number; y: number }>());
  const [cam, setCam] = useState<Camera>({ x: 0, y: 0, ratio: 1.6 });
  const [size, setSize] = useState({ w: 0, h: 0, dpr: 1 });
  const [mode, setMode] = useState<'instances' | 'schema'>('instances');
  const [hiddenTypes, setHiddenTypes] = useState<Set<string>>(new Set());
  const [query, setQuery] = useState('');
  const [searchOpen, setSearchOpen] = useState(false);

  // Merge positions: survivors keep theirs, newcomers get placed, removals
  // drop out. First paint runs one FA2 pass and centers the camera.
  useEffect(() => {
    if (!data || data.nodes.length === 0) return;
    setPositions((prev) => {
      if (!initializedRef.current) {
        initializedRef.current = true;
        const ids = data.nodes.map((n) => n.id);
        const edges = data.links.map(
          (l): [string, string] => [l.source, l.target],
        );
        const next = computeInitialLayout(ids, edges);
        let cx = 0;
        let cy = 0;
        for (const p of next.values()) {
          cx += p.x;
          cy += p.y;
        }
        setCam({ x: cx / Math.max(next.size, 1), y: cy / Math.max(next.size, 1), ratio: 1.6 });
        return next;
      }
      const adj = new Map<string, string[]>();
      for (const l of data.links) {
        if (!adj.has(l.source)) adj.set(l.source, []);
        if (!adj.has(l.target)) adj.set(l.target, []);
        adj.get(l.source)!.push(l.target);
        adj.get(l.target)!.push(l.source);
      }
      const ids = data.nodes.map((n) => n.id);
      const next = placeMissing(ids, adj, prev);
      for (const id of [...next.keys()]) {
        if (!ids.includes(id)) next.delete(id);
      }
      return next;
    });
  }, [data]);

  const allPositions = useMemo(() => {
    const merged = new Map(positions);
    for (const [id, p] of schemaSlots()) {
      if (!merged.has(id)) merged.set(id, p);
    }
    return merged;
  }, [positions]);

  const viewModel = useMemo(() => {
    if (!data) return { nodes: [], edges: [] };
    return buildViewModel(data, { mode, hiddenTypes, selectedId, positions: allPositions });
  }, [data, mode, hiddenTypes, selectedId, allPositions]);

  // Container sizing (DPR-aware).
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const update = () => {
      const rect = el.getBoundingClientRect();
      setSize({ w: Math.max(rect.width, 1), h: Math.max(rect.height, 1), dpr: Math.min(window.devicePixelRatio || 1, 2) });
    };
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Paint on anything that changes the picture. No animation loop exists —
  // a static scene costs exactly one frame.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || size.w === 0) return;
    canvas.width = Math.round(size.w * size.dpr);
    canvas.height = Math.round(size.h * size.dpr);
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    paint(ctx, viewModel, cam, size);
  }, [viewModel, cam, size]);

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!data || q.length < 2) return [];
    return data.nodes.filter((n) => n.title.toLowerCase().includes(q)).slice(0, 8);
  }, [query, data]);

  const chooseMatch = (m: GraphNode) => {
    onSelect(m);
    const p = positions.get(m.id);
    if (p) setCam((c) => ({ ...c, x: p.x, y: p.y }));
    setQuery('');
    setSearchOpen(false);
  };

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

  return (
    <div className="graph-wrap">
      <div className="graph-toolbar">
        <div className="graph-mode" role="tablist" aria-label="Graph view">
          <button
            className={mode === 'instances' ? 'active' : ''}
            onClick={() => setMode('instances')}
            role="tab"
            aria-selected={mode === 'instances'}
          >
            Instances
          </button>
          <button
            className={mode === 'schema' ? 'active' : ''}
            onClick={() => setMode('schema')}
            role="tab"
            aria-selected={mode === 'schema'}
          >
            Schema
          </button>
        </div>
        {mode === 'instances' ? (
          <div className="graph-search">
            <input
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setSearchOpen(true);
              }}
              onFocus={() => setSearchOpen(true)}
              onBlur={() => setTimeout(() => setSearchOpen(false), 150)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && matches.length > 0) chooseMatch(matches[0]);
                if (e.key === 'Escape') {
                  setQuery('');
                  setSearchOpen(false);
                }
              }}
              placeholder="Find a node…"
              aria-label="Find a node"
            />
            {searchOpen && matches.length > 0 && (
              <div className="graph-search-results">
                {matches.map((m) => (
                  <button key={m.id} onMouseDown={(e) => e.preventDefault()} onClick={() => chooseMatch(m)}>
                    <span className="graph-search-label">{m.label}</span> {m.title}
                  </button>
                ))}
              </div>
            )}
          </div>
        ) : (
          <div className="graph-hint">Click a type to inspect its instances</div>
        )}
        <button
          className="graph-fit-btn"
          onClick={() => {
            const pts = [...allPositions.values()];
            if (pts.length === 0) return;
            const fit = fitView(pts, { w: size.w, h: size.h });
            setCam({ ...fit, ratio: Math.max(fit.ratio, 0.2) });
          }}
          title="Fit the whole graph in view"
        >
          Fit
        </button>
      </div>
      <div className="graph-legend">
        {SCHEMA_LABELS.map((label) => {
          const hidden = mode === 'instances' && hiddenTypes.has(label);
          const count = typeCounts.get(label) ?? 0;
          return (
            <span
              key={label}
              className={`graph-legend-item${hidden ? ' off' : ''}${mode === 'instances' ? ' clickable' : ''}`}
              onClick={mode === 'instances' ? () => toggleType(label) : undefined}
              title={mode === 'instances' ? `Toggle ${label} nodes` : `${count} in view`}
            >
              <span
                className="graph-legend-dot"
                style={{ background: theme.nodeColors[label as keyof typeof theme.nodeColors] }}
              />
              {label} · {count}
            </span>
          );
        })}
      </div>
      <div ref={containerRef} className="graph-canvas">
        <canvas ref={canvasRef} style={{ width: '100%', height: '100%', display: 'block' }} />
      </div>
      {error && (
        <div className="graph-error" title={error}>
          Refresh failed — showing last good data
        </div>
      )}
    </div>
  );
}
