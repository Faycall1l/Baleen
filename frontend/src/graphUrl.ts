import type { Drawer } from './components/PanelDrawers';

// Share-link deep URLs (R46): selection, hidden types, and drawer encoded
// in the query string so any view is linkable. Pure parse/build pair so the
// round-trip unit-tests without a browser. Unknown values read as absent.

export interface GraphViewState {
  node: string | null;
  hidden: string[];
  drawer: Drawer;
}

const DRAWERS: Drawer[] = ['flow', 'alerts', 'reports', 'leaders', 'signals'];

export function parseGraphUrl(search: string): GraphViewState {
  const params = new URLSearchParams(search.startsWith('?') ? search.slice(1) : search);
  const node = params.get('node');
  const hidden = (params.get('hide') ?? '')
    .split(',')
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
  const drawerParam = params.get('drawer');
  const drawer: Drawer = DRAWERS.includes(drawerParam as Drawer)
    ? (drawerParam as Drawer)
    : null;
  return { node: node && node.length > 0 ? node : null, hidden, drawer };
}

export function buildGraphUrl(state: GraphViewState): string {
  const params = new URLSearchParams();
  if (state.node) params.set('node', state.node);
  if (state.hidden.length > 0) params.set('hide', state.hidden.join(','));
  if (state.drawer) params.set('drawer', state.drawer);
  const query = params.toString();
  return query ? `?${query}` : window.location.pathname;
}
