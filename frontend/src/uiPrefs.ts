import { FLOW_DEFAULTS } from './flow';
import { sanitizeTiles, type TileId } from './tileState';
import type { FlowFilters } from './types';

// Workspace persistence (R40): the terminal greets return visits with
// their layout, not a reset — hidden types and tape filters survive
// reloads. Everything is validated on load; garbage reads as defaults.

const HIDDEN_KEY = 'baleen.ui.hiddenTypes';
const FLOW_KEY = 'baleen.ui.flowFilters';
const TILES_KEY = 'baleen.ui.tiles';

export const DEFAULT_FLOW: FlowFilters = { ...FLOW_DEFAULTS };

function read<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return fallback;
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

function write(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* private mode etc. — prefs just don't persist */
  }
}

export function loadHiddenTypes(): string[] {
  const raw = read<unknown>(HIDDEN_KEY, []);
  if (!Array.isArray(raw)) return [];
  return raw.filter((t): t is string => typeof t === 'string');
}

export function saveHiddenTypes(hidden: Set<string> | string[]): void {
  write(HIDDEN_KEY, [...hidden]);
}

export function loadFlowFilters(): FlowFilters {
  const raw = read<Partial<FlowFilters> | null>(FLOW_KEY, null);
  if (!raw || typeof raw !== 'object') return { ...DEFAULT_FLOW };
  const tickers = Array.isArray(raw.tickers)
    ? raw.tickers.filter((t): t is string => typeof t === 'string')
    : [];
  const side = raw.side === 'call' || raw.side === 'put' ? raw.side : 'all';
  const num = (v: unknown, fallback: number) =>
    typeof v === 'number' && Number.isFinite(v) ? v : fallback;
  return {
    tickers,
    side,
    min_ratio: num(raw.min_ratio, DEFAULT_FLOW.min_ratio),
    window_days: num(raw.window_days, DEFAULT_FLOW.window_days),
    limit: num(raw.limit, DEFAULT_FLOW.limit),
  };
}

export function saveFlowFilters(filters: FlowFilters): void {
  write(FLOW_KEY, filters);
}

export function loadTiles(): TileId[] {
  return sanitizeTiles(read<unknown>(TILES_KEY, null));
}

export function saveTiles(tiles: TileId[]): void {
  write(TILES_KEY, tiles);
}
