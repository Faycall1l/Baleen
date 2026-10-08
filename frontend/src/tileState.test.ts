import { describe, expect, it } from 'vitest';

import {
  closeInLayout,
  DEFAULT_TILES,
  flattenLayout,
  moveSibling,
  openInLayout,
  sanitizeLayout,
  DEFAULT_LAYOUT,
  sanitizeTiles,
  splitTile,
  toggleInLayout,
  type Layout,
  type TileId,
  type TileLeaf,
} from './tileState';

describe('sanitizeTiles', () => {
  it('keeps valid tiles, drops garbage, and dedupes', () => {
    expect(sanitizeTiles(['board', 'flow', 'nope', 42, 'flow'])).toEqual(['board', 'flow']);
    expect(sanitizeTiles(['alerts'])).toEqual(['alerts']);
    expect(sanitizeTiles([])).toEqual([]);
    expect(sanitizeTiles(null)).toEqual([...DEFAULT_TILES]);
    expect(sanitizeTiles('board')).toEqual([...DEFAULT_TILES]);
  });
});

describe('layout tree', () => {
  const row = (...ids: TileId[]): Exclude<Layout, null> => ({
    type: 'split',
    dir: 'row',
    children: ids.map((id): TileLeaf => ({ type: 'tile', id })),
  });

  it('flattens leaves in visual order', () => {
    expect(flattenLayout(null)).toEqual([]);
    expect(flattenLayout({ type: 'tile', id: 'board' })).toEqual(['board']);
    expect(
      flattenLayout({
        type: 'split',
        dir: 'col',
        children: [row('board', 'flow'), { type: 'tile', id: 'alerts' }],
      }),
    ).toEqual(['board', 'flow', 'alerts']);
  });

  it('opens into the root and refuses duplicates', () => {
    expect(openInLayout(null, 'flow')).toEqual({ type: 'tile', id: 'flow' });
    expect(openInLayout({ type: 'tile', id: 'board' }, 'flow')).toEqual(
      row('board', 'flow'),
    );
    const full = row('board', 'flow');
    expect(openInLayout(full, 'flow')).toBe(full);
  });

  it('splits tiles right and below', () => {
    expect(splitTile(row('board', 'flow'), 'flow', 'col', 'alerts')).toEqual({
      type: 'split',
      dir: 'row',
      children: [
        { type: 'tile', id: 'board' },
        {
          type: 'split',
          dir: 'col',
          children: [{ type: 'tile', id: 'flow' }, { type: 'tile', id: 'alerts' }],
        },
      ],
    });
    // Unknown target or duplicate id: unchanged.
    expect(splitTile(row('board'), 'nope' as TileId, 'row', 'flow')).toEqual(row('board'));
    expect(splitTile(row('board'), 'board', 'row', 'board')).toEqual(row('board'));
  });

  it('closes tiles and collapses degenerate splits', () => {
    expect(closeInLayout(row('board', 'flow'), 'flow')).toEqual({ type: 'tile', id: 'board' });
    expect(closeInLayout({ type: 'tile', id: 'board' }, 'board')).toBeNull();
    expect(closeInLayout(null, 'board')).toBeNull();
    const nested: Layout = {
      type: 'split',
      dir: 'row',
      children: [row('board', 'flow'), { type: 'tile', id: 'alerts' }],
    };
    expect(closeInLayout(nested, 'alerts')).toEqual(row('board', 'flow'));
    expect(closeInLayout(nested, 'flow')).toEqual({
      type: 'split',
      dir: 'row',
      children: [{ type: 'tile', id: 'board' }, { type: 'tile', id: 'alerts' }],
    });
  });

  it('toggles and moves within siblings', () => {
    expect(toggleInLayout(row('board', 'flow'), 'flow')).toEqual({ type: 'tile', id: 'board' });
    expect(toggleInLayout({ type: 'tile', id: 'board' }, 'flow')).toEqual(row('board', 'flow'));
    expect(moveSibling(row('board', 'flow', 'alerts'), 'flow', 1)).toEqual(
      row('board', 'alerts', 'flow'),
    );
    expect(moveSibling(row('board', 'flow'), 'board', -1)).toEqual(row('board', 'flow'));
    expect(moveSibling({ type: 'tile', id: 'board' }, 'board', 1)).toEqual({
      type: 'tile',
      id: 'board',
    });
  });

  it('sanitizes garbage, migrates flat lists, and dedupes', () => {
    expect(sanitizeLayout(null)).toEqual(DEFAULT_LAYOUT);
    expect(sanitizeLayout(['flow', 'nope', 'flow'])).toEqual({ type: 'tile', id: 'flow' });
    expect(sanitizeLayout(['board', 'flow'])).toEqual(row('board', 'flow'));
    expect(sanitizeLayout([])).toBeNull();
    expect(
      sanitizeLayout({
        type: 'split',
        dir: 'row',
        children: [{ type: 'tile', id: 'board' }, { type: 'tile', id: 'board' }, { type: 'tile', id: 'nope' }],
      }),
    ).toEqual({ type: 'tile', id: 'board' });
  });
});
