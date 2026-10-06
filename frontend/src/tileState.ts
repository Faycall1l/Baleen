// Dockable workspace tiles (P1, board freed in P6): the rail opens panels
// side-by-side in the main area. Every tile — board included — toggles,
// reorders, enlarges, and closes freely; an empty dock offers quick-open.

export type TileId =
  | 'board'
  | 'flow'
  | 'alerts'
  | 'reports'
  | 'leaders'
  | 'signals'
  | 'path';

const ALL_TILES: ReadonlySet<string> = new Set([
  'board',
  'flow',
  'alerts',
  'reports',
  'leaders',
  'signals',
  'path',
]);

export const DEFAULT_TILES: TileId[] = ['board', 'flow'];

export const TILE_TITLES: Record<TileId, string> = {
  board: 'Signal board',
  flow: 'Flow tape',
  alerts: 'Alerts',
  reports: 'Reports',
  leaders: 'Leaderboard',
  signals: 'Signals',
  path: 'Path',
};

function isTileId(v: unknown): v is TileId {
  return typeof v === 'string' && ALL_TILES.has(v);
}

/** Validate persisted tiles: drop garbage, dedupe, always keep the board.
 * Non-array input (no prefs yet) yields the default workspace. */
export function sanitizeTiles(raw: unknown): TileId[] {
  if (!Array.isArray(raw)) return [...DEFAULT_TILES];
  return [...new Set(raw.filter(isTileId))];
}

/** Rail toggle: add at the end, or remove. */
export function toggleTile(tiles: TileId[], id: TileId): TileId[] {
  return tiles.includes(id) ? tiles.filter((t) => t !== id) : [...tiles, id];
}

/** Ensure a tile is open (deep links, tape jumps); no-op when present. */
export function ensureTile(tiles: TileId[], id: TileId): TileId[] {
  return tiles.includes(id) ? tiles : [...tiles, id];
}

/** Close button: any tile can go; the dock shows quick-open when empty. */
export function closeTile(tiles: TileId[], id: TileId): TileId[] {
  return tiles.filter((t) => t !== id);
}

/** Reorder: swap one step left (-1) or right (+1); edges are no-ops. */
export function moveTile(tiles: TileId[], id: TileId, dir: -1 | 1): TileId[] {
  const i = tiles.indexOf(id);
  const j = i + dir;
  if (i < 0 || j < 0 || j >= tiles.length) return tiles;
  const next = [...tiles];
  next[i] = tiles[j];
  next[j] = tiles[i];
  return next;
}
