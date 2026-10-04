import type { GraphData, GraphNode } from '../types';

// Pure data shaping for the TimelineBoard (T1): options-flow events grouped
// into one row per ticker, positioned on a shared time axis. Insider and
// congress-trade lanes arrive in T3; the row model already supports them.

export type FlowSide = 'call' | 'put' | 'other';

export interface TimelineDot {
  node: GraphNode;
  time: number;
  side: FlowSide;
  volume: number;
}

export interface TimelineRow {
  key: string;
  title: string;
  dots: TimelineDot[];
}

export function eventTimeMs(node: GraphNode): number | null {
  const t = node.props.event_time;
  if (typeof t !== 'string' || t === '') return null;
  const ms = Date.parse(t);
  return Number.isNaN(ms) ? null : ms;
}

function numProp(node: GraphNode, key: string): number {
  const v = node.props[key];
  const n = typeof v === 'number' ? v : Number(v);
  return Number.isFinite(n) && n > 0 ? n : 0;
}

/** Options-flow events grouped by ticker, rows sorted by activity, capped. */
export function buildTimelineRows(
  data: GraphData | null,
  hiddenTypes: Set<string>,
  maxRows = 14,
): TimelineRow[] {
  if (!data || hiddenTypes.has('Event')) return [];
  const hideTickers = hiddenTypes.has('Ticker');
  const byTicker = new Map<string, TimelineDot[]>();
  for (const n of data.nodes) {
    if (n.label !== 'Event' || n.props.type !== 'options_flow') continue;
    const time = eventTimeMs(n);
    if (time === null) continue;
    const raw = n.props.ticker;
    const ticker = typeof raw === 'string' && raw !== '' ? raw.toUpperCase() : 'UNKNOWN';
    if (ticker !== 'UNKNOWN' && hideTickers) continue;
    const ct = n.props.contract_type;
    const side: FlowSide = ct === 'call' ? 'call' : ct === 'put' ? 'put' : 'other';
    const list = byTicker.get(ticker) ?? [];
    list.push({ node: n, time, side, volume: numProp(n, 'volume') });
    byTicker.set(ticker, list);
  }
  return [...byTicker.entries()]
    .map(([key, dots]) => ({
      key,
      title: key,
      dots: dots.sort((a, b) => a.time - b.time),
    }))
    .sort((a, b) => b.dots.length - a.dots.length || (a.key < b.key ? -1 : 1))
    .slice(0, maxRows);
}

/** Full time extent across rows; null when there is nothing to plot. */
export function timeDomain(rows: TimelineRow[]): [number, number] | null {
  let lo = Infinity;
  let hi = -Infinity;
  for (const r of rows) {
    for (const d of r.dots) {
      if (d.time < lo) lo = d.time;
      if (d.time > hi) hi = d.time;
    }
  }
  return lo === Infinity ? null : [lo, hi];
}

export const DAY_MS = 86_400_000;

/** Zoom window: last `days` of the domain, or the full domain when null. */
export function viewWindow(
  domain: [number, number] | null,
  days: number | null,
): [number, number] | null {
  if (!domain) return null;
  if (days === null) return domain;
  return [Math.min(domain[0], domain[1] - days * DAY_MS), domain[1]];
}

/** Largest volume across rows; dot radii scale against it (0-safe). */
export function maxVolume(rows: TimelineRow[]): number {
  let best = 0;
  for (const r of rows) for (const d of r.dots) if (d.volume > best) best = d.volume;
  return best;
}

/** Dot radius in px: log-scaled against the window max, clamped 2.5–7. */
export function dotRadius(volume: number, vmax: number): number {
  if (!(vmax > 0) || !(volume > 0)) return 2.5;
  const t = Math.log10(1 + volume) / Math.log10(1 + vmax);
  return 2.5 + 4.5 * Math.min(1, Math.max(0, t));
}
