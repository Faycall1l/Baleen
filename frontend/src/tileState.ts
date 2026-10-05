// Dockable workspace tiles (P1): the rail opens panels side-by-side in the
// main area instead of one overlay at a time. `board` is permanent — every
// other tile toggles, reorders, enlarges, and closes freely.

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
  const deduped = [...new Set(raw.filter(isTileId))];
  if (!deduped.includes('board')) deduped.unshift('board');
  return deduped;
}

/** Rail toggle: add at the end, or remove (never the board). */
export function toggleTile(tiles: TileId[], id: TileId): TileId[] {
  if (id === 'board') return tiles;
  return tiles.includes(id) ? tiles.filter((t) => t !== id) : [...tiles, id];
}

/** Ensure a tile is open (deep links, tape jumps); no-op when present. */
export function ensureTile(tiles: TileId[], id: TileId): TileId[] {
  return tiles.includes(id) ? tiles : [...tiles, id];
}

/** Close button: the board stays — closing it would strand users with no
 * way back (the rail has no board tab). */
export function closeTile(tiles: TileId[], id: TileId): TileId[] {
  if (id === 'board') return tiles;
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
