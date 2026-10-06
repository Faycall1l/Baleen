import { useCallback, useEffect, useRef, useState } from 'react';

import { fetchGraph } from '../api';
import type { GraphData } from '../types';

export type GraphStage = 'first' | 'partial' | 'full';

// Newest event_time across the loaded snapshot: the delta cursor for the
// next poll. Events carry ISO strings; unparseable values are ignored.
function graphCursor(g: GraphData | null): string | null {
  if (!g) return null;
  let best: string | null = null;
  for (const n of g.nodes) {
    if (n.label !== 'Event') continue;
    const t = n.props.event_time;
    if (typeof t !== 'string' || t === '') continue;
    if (best === null || t > best) best = t;
  }
  return best;
}

// Board snapshot feed (extracted from App, H1): poll/merge/stage ownership.
// Steady-state polls ask for a delta off the newest event_time (cheap
// index-bounded query); every 10th poll refetches full as the resync.
export function useGraphFeed(): {
  graph: GraphData | null;
  graphError: string | null;
  graphStage: GraphStage;
  updatedAt: number | null;
  loadGraph: () => void;
  showEvents: boolean;
  setShowEvents: React.Dispatch<React.SetStateAction<boolean>>;
} {
  const [graph, setGraph] = useState<GraphData | null>(null);
  const [graphError, setGraphError] = useState<string | null>(null);
  const [graphStage, setGraphStage] = useState<GraphStage>('first');
  // Focused default (R54b, T4): the signal board aggregates events into
  // capped lanes, so it loads with events on — unlike the old canvas, the
  // full window stays readable. The toggle still opts out to entities only.
  const [showEvents, setShowEvents] = useState(true);
  // Last successful fetch time (R38): honesty about freshness in the
  // status bar. Stamped on fetch success, not on merge — an unchanged
  // snapshot is still fresh data.
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);

  const graphRef = useRef(graph);
  graphRef.current = graph;
  const graphAbortRef = useRef<AbortController | null>(null);
  const pollCountRef = useRef(0);
  const graphSigRef = useRef<string | null>(null);

  const applyGraphPayload = useCallback((payload: GraphData, isDelta: boolean) => {
    if (isDelta) {
      if (payload.nodes.length === 0 && payload.links.length === 0) return;
      setGraph((prev) => {
        if (!prev) return payload;
        const nodes = new Map(prev.nodes.map((n) => [n.id, n]));
        for (const n of payload.nodes) nodes.set(n.id, n);
        const seen = new Set(prev.links.map((x) => `${x.source}>${x.target}:${x.type}`));
        const links = [...prev.links];
        for (const x of payload.links) {
          const k = `${x.source}>${x.target}:${x.type}`;
          if (!seen.has(k)) {
            seen.add(k);
            links.push(x);
          }
        }
        const merged: GraphData = {
          ...payload,
          nodes: [...nodes.values()],
          links,
          node_count: nodes.size,
          link_count: links.length,
          // Totals describe the snapshot, not the delta.
          event_count: prev.event_count,
          truncated: prev.truncated,
        };
        const sig = JSON.stringify({
          n: merged.node_count,
          l: merged.link_count,
          e: merged.event_count,
          nodes: merged.nodes.map((n) => n.id).sort(),
          links: merged.links.map((x) => `${x.source}>${x.target}:${x.type}`).sort(),
        });
        if (sig !== graphSigRef.current) {
          graphSigRef.current = sig;
          return merged;
        }
        return prev;
      });
      return;
    }
    const sig = JSON.stringify({
      n: payload.node_count,
      l: payload.link_count,
      e: payload.event_count,
      nodes: payload.nodes.map((n) => n.id).sort(),
      links: payload.links.map((x) => `${x.source}>${x.target}:${x.type}`).sort(),
    });
    if (sig !== graphSigRef.current) {
      graphSigRef.current = sig;
      setGraph(payload);
    }
  }, []);

  const loadGraph = useCallback(() => {
    // Slow renders must never pile up: a new poll cancels the previous
    // in-flight fetch (aborts are swallowed, not surfaced as errors).
    graphAbortRef.current?.abort();
    const ctrl = new AbortController();
    graphAbortRef.current = ctrl;
    const timer = setTimeout(() => ctrl.abort(), 60_000);
    setGraphError(null);
    pollCountRef.current += 1;
    const cursor = graphCursor(graphRef.current);
    const full = pollCountRef.current % 10 === 1 || cursor === null;
    fetchGraph(150, ctrl.signal, full ? undefined : cursor ?? undefined, undefined, !showEvents)
      .then((data) => {
        setUpdatedAt(Date.now());
        applyGraphPayload(data, !full);
      })
      .catch((err) => {
        if (err instanceof DOMException && err.name === 'AbortError') return;
        setGraphError(err.message);
      })
      .finally(() => clearTimeout(timer));
  }, [applyGraphPayload, showEvents]);

  useEffect(() => {
    // Staged boot: paint a quick small window first (seconds even on a slow
    // backend), then upgrade to the full width in the background. The poll
    // loop takes over afterwards; its cursor/delta logic merges onto
    // whatever snapshot is present.
    let cancelled = false;
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 120_000);
    setGraphError(null);
    fetchGraph(25, ctrl.signal, undefined, undefined, !showEvents)
      .then((data) => {
        if (cancelled) return;
        setUpdatedAt(Date.now());
        applyGraphPayload(data, false);
        setGraphStage('partial');
        return fetchGraph(150, ctrl.signal, undefined, undefined, !showEvents);
      })
      .then((data) => {
        if (cancelled || !data) return;
        setUpdatedAt(Date.now());
        applyGraphPayload(data, false);
        setGraphStage('full');
      })
      .catch((err) => {
        if (cancelled) return;
        if (err instanceof DOMException && err.name === 'AbortError') return;
        // Small-window failure: fall back to the standard full fetch path.
        loadGraph();
      })
      .finally(() => clearTimeout(timer));
    return () => {
      cancelled = true;
      ctrl.abort();
    };
  }, [applyGraphPayload, loadGraph, showEvents]);

  return { graph, graphError, graphStage, updatedAt, loadGraph, showEvents, setShowEvents };
}
