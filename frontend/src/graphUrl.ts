import { sanitizeTiles, type TileId } from './tileState';

// Share-link deep URLs: selection, hidden types, and open
// panels encoded in the query string so any view is linkable. Pure
// parse/build pair so the round-trip unit-tests without a browser. Unknown
// values read as absent. The legacy `drawer` param (single panel, pre-tiles)
// still opens — it maps to a tile.
//
// Embed mode: `embed=<tile>` renders that single panel chromeless for
// publishers. Only parsed, never written — embeds are made by hand.

const ALL_TILES: readonly TileId[] = [
  'board',
  'leads',
  'flow',
  'alerts',
  'reports',
  'leaders',
  'signals',
  'path',
];

export interface GraphViewState {
  node: string | null;
  hidden: string[];
  /** Open tiles, or null when the URL says nothing about panels (prefs win). */
  tiles: TileId[] | null;
  /** Chromeless single-panel render, or null for the full terminal. */
  embed?: TileId | null;
}

function parseTileId(v: string | null): TileId | null {
  const t = (v ?? '').trim();
  return (ALL_TILES as readonly string[]).includes(t) ? (t as TileId) : null;
}

export function parseGraphUrl(search: string): GraphViewState {
  const params = new URLSearchParams(search.startsWith('?') ? search.slice(1) : search);
  const node = params.get('node');
  const hidden = (params.get('hide') ?? '')
    .split(',')
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
  const embed = parseTileId(params.get('embed'));
  const hasPanels = params.has('tiles') || params.has('drawer');
  if (!hasPanels)
    return { node: node && node.length > 0 ? node : null, hidden, tiles: null, embed };
  const listed = (params.get('tiles') ?? '')
    .split(',')
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
  const legacy = params.get('drawer');
  if (legacy && legacy.trim() !== '') listed.push(legacy.trim());
  const tiles = sanitizeTiles(listed);
  return { node: node && node.length > 0 ? node : null, hidden, tiles, embed };
}

export function buildGraphUrl(state: GraphViewState): string {
  const params = new URLSearchParams();
  if (state.node) params.set('node', state.node);
  if (state.hidden.length > 0) params.set('hide', state.hidden.join(','));
  const tiles = state.tiles ?? [];
  if (tiles.length > 0) params.set('tiles', tiles.join(','));
  const query = params.toString();
  return query ? `?${query}` : window.location.pathname;
}
