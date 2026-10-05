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
  it('keeps valid tiles, drops garbage, dedupes, and guarantees the board', () => {
    expect(sanitizeTiles(['board', 'flow', 'nope', 42, 'flow'])).toEqual(['board', 'flow']);
    expect(sanitizeTiles(['alerts'])).toEqual(['board', 'alerts']);
    expect(sanitizeTiles(null)).toEqual([...DEFAULT_TILES]);
    expect(sanitizeTiles('board')).toEqual([...DEFAULT_TILES]);
  });
});

describe('toggleTile', () => {
  it('adds missing tiles and removes present ones, never the board', () => {
    expect(toggleTile(['board'], 'flow')).toEqual(['board', 'flow']);
    expect(toggleTile(['board', 'flow'], 'flow')).toEqual(['board']);
    expect(toggleTile(['board', 'flow'], 'board')).toEqual(['board', 'flow']);
  });
});

describe('ensureTile / closeTile', () => {
  it('opens missing tiles exactly once and refuses to close the board', () => {
    const tiles: TileId[] = ['board'];
    expect(ensureTile(tiles, 'flow')).toEqual(['board', 'flow']);
    expect(ensureTile(['board', 'flow'], 'flow')).toEqual(['board', 'flow']);
    expect(closeTile(['board', 'flow'], 'flow')).toEqual(['board']);
    expect(closeTile(['board', 'flow'], 'board')).toEqual(['board', 'flow']);
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
