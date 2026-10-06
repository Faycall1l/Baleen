import { describe, expect, it } from 'vitest';

import {
  closeTile,
  DEFAULT_TILES,
  ensureTile,
  moveTile,
  sanitizeTiles,
  toggleTile,
  type TileId,
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

describe('toggleTile', () => {
  it('adds missing tiles and removes present ones, board included', () => {
    expect(toggleTile(['board'], 'flow')).toEqual(['board', 'flow']);
    expect(toggleTile(['board', 'flow'], 'flow')).toEqual(['board']);
    expect(toggleTile(['board', 'flow'], 'board')).toEqual(['flow']);
    expect(toggleTile(['flow'], 'board')).toEqual(['flow', 'board']);
  });
});

describe('ensureTile / closeTile', () => {
  it('opens missing tiles exactly once and closes any tile', () => {
    const tiles: TileId[] = ['board'];
    expect(ensureTile(tiles, 'flow')).toEqual(['board', 'flow']);
    expect(ensureTile(['board', 'flow'], 'flow')).toEqual(['board', 'flow']);
    expect(closeTile(['board', 'flow'], 'flow')).toEqual(['board']);
    expect(closeTile(['board', 'flow'], 'board')).toEqual(['flow']);
    expect(closeTile(['board'], 'board')).toEqual([]);
  });
});

describe('moveTile', () => {
  it('swaps one step and no-ops at the edges or on unknown tiles', () => {
    const tiles: TileId[] = ['board', 'flow', 'alerts'];
    expect(moveTile(tiles, 'flow', 1)).toEqual(['board', 'alerts', 'flow']);
    expect(moveTile(tiles, 'flow', -1)).toEqual(['flow', 'board', 'alerts']);
    expect(moveTile(tiles, 'board', -1)).toEqual(tiles);
    expect(moveTile(tiles, 'alerts', 1)).toEqual(tiles);
    expect(moveTile(tiles, 'path', 1)).toEqual(tiles);
  });
});
