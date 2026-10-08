// Dockable workspace tiles (P1, board freed in P6): the rail opens panels
// side-by-side in the main area. Every tile — board included — toggles,
// reorders, enlarges, and closes freely; an empty dock offers quick-open.
//
// Splittable workspace (L1): the flat tile list becomes a binary split
// tree. A split is either a row (tiles side by side, divided vertically)
// or a column (tiles stacked, divided horizontally). All ops are pure and
// immutable; closing collapses degenerate splits so the tree never holds
// single-child or empty splits. A tile id appears at most once — opening an
// already-open tile is a no-op instead of a duplicate live instance.

export type SplitDir = 'row' | 'col';

export interface TileLeaf {
  type: 'tile';
  id: TileId;
}

export interface SplitNode {
  type: 'split';
  dir: SplitDir;
  children: LayoutNode[];
}

export type LayoutNode = TileLeaf | SplitNode;

/** Null renders the empty dock. */
export type Layout = LayoutNode | null;

export const DEFAULT_LAYOUT: Layout = {
  type: 'split',
  dir: 'row',
  children: [{ type: 'tile', id: 'leads' }, { type: 'tile', id: 'board' }],
};

export type TileId =
  | 'board'
  | 'leads'
  | 'flow'
  | 'alerts'
  | 'reports'
  | 'leaders'
  | 'signals'
  | 'path';

const ALL_TILES: ReadonlySet<string> = new Set([
  'board',
  'leads',
  'flow',
  'alerts',
  'reports',
  'leaders',
  'signals',
  'path',
]);

export const ALL_TILE_IDS: TileId[] = [
  'board',
  'leads',
  'flow',
  'alerts',
  'reports',
  'leaders',
  'signals',
  'path',
];

export const DEFAULT_TILES: TileId[] = ['leads', 'board'];

export const TILE_TITLES: Record<TileId, string> = {
  board: 'Signal board',
  leads: 'Story leads',
  flow: 'Flow tape',
  alerts: 'Alerts',
  reports: 'Reports',
  leaders: 'Leaderboard',
  signals: 'Activity',
  path: 'Path',
};

function isTileId(v: unknown): v is TileId {
  return typeof v === 'string' && ALL_TILES.has(v);
}

/** Validate persisted tiles: drop garbage, dedupe. Empty stays empty.
 * Non-array input (no prefs yet) yields the default workspace. */
export function sanitizeTiles(raw: unknown): TileId[] {
  if (!Array.isArray(raw)) return [...DEFAULT_TILES];
  return [...new Set(raw.filter(isTileId))];
}

/** Leaf ids in visual order. */
export function flattenLayout(layout: Layout): TileId[] {
  if (!layout) return [];
  if (layout.type === 'tile') return [layout.id];
  return layout.children.flatMap(flattenLayout);
}

function layoutHas(layout: Layout, id: TileId): boolean {
  return flattenLayout(layout).includes(id);
}

/** Rail toggle on trees: close when open, else dock into the root split. */
export function toggleInLayout(layout: Layout, id: TileId): Layout {
  if (layoutHas(layout, id)) return closeInLayout(layout, id);
  return openInLayout(layout, id);
}

/** Dock a tile: no-op when already open (no duplicate live instances). */
export function openInLayout(layout: Layout, id: TileId): Layout {
  if (!layout) return { type: 'tile', id };
  if (layoutHas(layout, id)) return layout;
  if (layout.type === 'tile') {
    return { type: 'split', dir: 'row', children: [layout, { type: 'tile', id }] };
  }
  return { ...layout, children: [...layout.children, { type: 'tile', id }] };
}

/** Split a tile: the new panel docks right (row) or below (column) it.
 * Never returns null — splitting only adds. */
export function splitTile(layout: Layout, targetId: TileId, dir: SplitDir, newId: TileId): LayoutNode {
  if (!layout) return { type: 'tile', id: newId };
  if (layoutHas(layout, newId)) return layout;
  if (layout.type === 'tile') {
    if (layout.id !== targetId) return layout;
    return { type: 'split', dir, children: [layout, { type: 'tile', id: newId }] };
  }
  return { ...layout, children: layout.children.map((c) => splitTile(c, targetId, dir, newId)) };
}

/** Close a tile, collapsing degenerate splits bottom-up. */
export function closeInLayout(layout: Layout, id: TileId): Layout {
  if (!layout) return layout;
  if (layout.type === 'tile') return layout.id === id ? null : layout;
  const children = layout.children
    .map((c) => closeInLayout(c, id))
    .filter((c): c is LayoutNode => c !== null);
  if (children.length === 0) return null;
  if (children.length === 1) return children[0];
  return { ...layout, children };
}

/** Swap with the previous (-1) or next (+1) sibling in the same split. */
export function moveSibling(layout: Layout, id: TileId, dir: -1 | 1): Layout {
  if (!layout || layout.type === 'tile') return layout;
  const i = layout.children.findIndex((c) => c.type === 'tile' && c.id === id);
  if (i >= 0) {
    const j = i + dir;
    if (j < 0 || j >= layout.children.length) return layout;
    const children = [...layout.children];
    children[i] = layout.children[j];
    children[j] = layout.children[i];
    return { ...layout, children };
  }
  const next = layout.children
    .map((c) => moveSibling(c, id, dir))
    .filter((c): c is LayoutNode => c !== null);
  return next.every((c, k) => c === layout.children[k]) ? layout : { ...layout, children: next };
}

/** Validate a persisted layout: drop garbage leaves, collapse degenerate
 * splits, dedupe ids (first occurrence wins). Flat legacy lists migrate
 * into one row. Anything unusable reads as the default workspace. */
export function sanitizeLayout(raw: unknown): Layout {
  if (Array.isArray(raw)) {
    const ids = [...new Set(raw.filter(isTileId))];
    if (ids.length === 0) return null;
    if (ids.length === 1) return { type: 'tile', id: ids[0] };
    return { type: 'split', dir: 'row', children: ids.map((id) => ({ type: 'tile', id })) };
  }
  if (typeof raw !== 'object' || raw === null) return structuredClone(DEFAULT_LAYOUT) as Layout;
  const clean = cleanNode(raw);
  if (!clean) return structuredClone(DEFAULT_LAYOUT) as Layout;
  const deduped = dedupeLayout(clean);
  return deduped ?? (structuredClone(DEFAULT_LAYOUT) as Layout);
}

function cleanNode(raw: unknown): LayoutNode | null {
  if (typeof raw !== 'object' || raw === null) return null;
  const rec = raw as Record<string, unknown>;
  if (rec.type === 'tile') {
    return isTileId(rec.id) ? { type: 'tile', id: rec.id } : null;
  }
  if (rec.type === 'split' && (rec.dir === 'row' || rec.dir === 'col') && Array.isArray(rec.children)) {
    const children = (rec.children as unknown[]).map(cleanNode).filter((c): c is LayoutNode => c !== null);
    if (children.length === 0) return null;
    if (children.length === 1) return children[0];
    return { type: 'split', dir: rec.dir, children };
  }
  return null;
}

function dedupeLayout(node: LayoutNode): LayoutNode | null {
  const seen = new Set<TileId>();
  const walk = (n: LayoutNode): LayoutNode | null => {
    if (n.type === 'tile') {
      if (seen.has(n.id)) return null;
      seen.add(n.id);
      return n;
    }
    const children = n.children.map(walk).filter((c): c is LayoutNode => c !== null);
    if (children.length === 0) return null;
    if (children.length === 1) return children[0];
    return { type: n.type, dir: n.dir, children };
  };
  return walk(node);
}
