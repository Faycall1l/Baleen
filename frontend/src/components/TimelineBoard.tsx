import { useEffect, useMemo, useRef, useState } from 'react';

import type { GraphNode } from '../types';
import { SCHEMA_LABELS } from '../graph/schema';
import {
  buildTimelineRows,
  dotRadius,
  maxVolume,
  viewWindow,
  type TimelineDot,
} from '../graph/timeline';
import { useGraphSearch } from '../hooks/useGraphSearch';
import GraphLegend from './GraphLegend';
import GraphToolbar from './GraphToolbar';

interface Props {
  data: import('../types').GraphData | null;
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

const ROW_H = 34;
const PLOT_PAD = 8;
const ZOOMS: Array<{ label: string; days: number | null }> = [
  { label: 'All', days: null },
  { label: '30d', days: 30 },
  { label: '7d', days: 7 },
];

const SIDE_FILL: Record<TimelineDot['side'], string> = {
  call: '#6fbf73',
  put: '#e06666',
  other: '#8a8f98',
};

function fmtDay(ms: number): string {
  const d = new Date(ms);
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

/** Signal board (T1): options-flow events as dots on one row per ticker
 * over a shared time axis. Replaces the node-link canvas: vertical scanning
 * correlates tickers at a glance, zoom presets focus the window, and every
 * dot opens the same inspector the graph fed.
 */
export default function TimelineBoard({
  data,
  error,
  selectedId,
  onSelect,
  onRetry,
  hiddenTypes,
  onToggleType,
  onSoloType,
  onClearTypes,
  scrubDaysAgo,
  scrubMaxDays,
  onScrubChange,
  showEvents,
  onToggleEvents,
  loadingHint,
  warming,
}: Props) {
  const [zoomDays, setZoomDays] = useState<number | null>(null);
  const plotRef = useRef<HTMLDivElement | null>(null);
  const [plotW, setPlotW] = useState(720);

  useEffect(() => {
    const el = plotRef.current;
    if (!el) return;
    const measure = () => setPlotW(Math.max(240, el.clientWidth - PLOT_PAD * 2));
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const search = useGraphSearch({
    data,
    hiddenTypes,
    showType: (label) => {
      if (hiddenTypes.has(label)) onToggleType(label);
    },
    onSelect,
  });

  const rows = useMemo(() => buildTimelineRows(data, hiddenTypes), [data, hiddenTypes]);
  const domain = useMemo(() => {
    let lo = Infinity;
    let hi = -Infinity;
    for (const r of rows) for (const d of r.dots) {
      if (d.time < lo) lo = d.time;
      if (d.time > hi) hi = d.time;
    }
    return lo === Infinity ? null : ([lo, hi] as [number, number]);
  }, [rows]);
  const view = useMemo(() => viewWindow(domain, zoomDays), [domain, zoomDays]);
  const vmax = useMemo(() => maxVolume(rows), [rows]);

  const typeCounts = useMemo(() => {
    const m = new Map<string, number>();
    for (const n of data?.nodes ?? []) m.set(n.label, (m.get(n.label) ?? 0) + 1);
    return m;
  }, [data]);

  const x = (t: number): number => {
    if (!view || view[1] === view[0]) return PLOT_PAD + plotW / 2;
    return PLOT_PAD + ((t - view[0]) / (view[1] - view[0])) * plotW;
  };

  const ticks = useMemo(() => {
    if (!view) return [];
    const out: number[] = [];
    const n = 5;
    for (let i = 0; i <= n; i++) out.push(view[0] + ((view[1] - view[0]) * i) / n);
    return out;
  }, [view]);

  const allHidden =
    data !== null && data.nodes.length > 0 && data.nodes.every((n) => hiddenTypes.has(n.label));

  const dotTitle = (d: TimelineDot): string => {
    const p = d.node.props;
    const parts = [
      `${d.node.title}`,
      d.side === 'other' ? null : d.side.toUpperCase(),
      typeof p.strike === 'number' || typeof p.strike === 'string' ? `$${p.strike}` : null,
      d.volume > 0 ? `vol ${d.volume.toLocaleString()}` : null,
      new Date(d.time).toLocaleString(),
    ].filter(Boolean);
    return parts.join(' · ');
  };

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
        onFit={() => setZoomDays(null)}
        onResetView={() => {
          setZoomDays(null);
          onSelect(null);
          onClearTypes();
        }}
        scrubDaysAgo={scrubDaysAgo}
        scrubMaxDays={scrubMaxDays}
        onScrubChange={onScrubChange}
        showEvents={showEvents}
        onToggleEvents={onToggleEvents}
      />
      <GraphLegend
        labels={[...SCHEMA_LABELS].sort(
          (a, b) => (typeCounts.get(b) ?? 0) - (typeCounts.get(a) ?? 0),
        )}
        counts={typeCounts}
        hiddenTypes={hiddenTypes}
        interactive
        onToggleType={onToggleType}
        onSoloType={onSoloType}
      />
      {error && (
        <div className="graph-error" role="alert">
          <span>{error}</span>
          {onRetry && (
            <button type="button" onClick={onRetry}>
              Retry
            </button>
          )}
        </div>
      )}
      {allHidden && (
        <div className="graph-empty-filter" role="status">
          <div>Every type is hidden — the board is empty by filter, not by data.</div>
          <button type="button" onClick={onClearTypes}>
            Show all types
          </button>
        </div>
      )}
      {!error && !allHidden && rows.length === 0 && (
        <div className="graph-empty-filter" role="status">
          <div>
            {data === null
              ? (loadingHint ?? 'Loading…')
              : 'No options-flow events in this window.'}
          </div>
          {data !== null && !showEvents && (
            <button type="button" onClick={onToggleEvents}>
              Show events
            </button>
          )}
        </div>
      )}
      {warming && (
        <div className="graph-error" title="Showing the quick first window; the full board is still loading">
          <span>Quick view — full window loading…</span>
        </div>
      )}
      {rows.length > 0 && (
        <div className="tl-zoom" role="group" aria-label="Time window">
          {ZOOMS.map((z) => (
            <button
              key={z.label}
              type="button"
              className={`graph-fit-btn${zoomDays === z.days ? ' active' : ''}`}
              aria-pressed={zoomDays === z.days}
              onClick={() => setZoomDays(z.days)}
            >
              {z.label}
            </button>
          ))}
        </div>
      )}
      {rows.length > 0 && (
        <div className="tl-body">
          <div className="tl-labels" aria-hidden="true">
            <div className="tl-axis-spacer" />
            {rows.map((r) => (
              <div key={r.key} className="tl-label" title={`${r.dots.length} events`}>
                <span className="tl-ticker">{r.title}</span>
                <span className="tl-count">{r.dots.length}</span>
              </div>
            ))}
          </div>
          <div className="tl-plot" ref={plotRef}>
            <svg
              width={plotW + PLOT_PAD * 2}
              height={rows.length * ROW_H + 26}
              role="img"
              aria-label="Options flow by ticker over time"
            >
              {ticks.map((t) => (
                <g key={t}>
                  <line
                    x1={x(t)}
                    x2={x(t)}
                    y1={18}
                    y2={rows.length * ROW_H + 22}
                    className="tl-grid"
                  />
                  <text x={x(t)} y={12} textAnchor="middle" className="tl-tick">
                    {fmtDay(t)}
                  </text>
                </g>
              ))}
              {rows.map((r, ri) => (
                <g key={r.key}>
                  <line
                    x1={PLOT_PAD}
                    x2={PLOT_PAD + plotW}
                    y1={26 + ri * ROW_H + ROW_H / 2}
                    y2={26 + ri * ROW_H + ROW_H / 2}
                    className="tl-rowline"
                  />
                  {r.dots.map((d) => {
                    const selected = d.node.id === selectedId;
                    return (
                      <circle
                        key={d.node.id}
                        cx={x(d.time)}
                        cy={26 + ri * ROW_H + ROW_H / 2}
                        r={dotRadius(d.volume, vmax)}
                        className={`tl-dot${selected ? ' selected' : ''}`}
                        fill={SIDE_FILL[d.side]}
                        tabIndex={0}
                        role="button"
                        aria-label={dotTitle(d)}
                        onClick={() => onSelect(d.node)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter' || e.key === ' ') {
                            e.preventDefault();
                            onSelect(d.node);
                          }
                        }}
                      >
                        <title>{dotTitle(d)}</title>
                      </circle>
                    );
                  })}
                </g>
              ))}
            </svg>
          </div>
        </div>
      )}
    </div>
  );
}
