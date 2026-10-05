import { describe, expect, it } from 'vitest';

import { buildGraphUrl, parseGraphUrl } from './graphUrl';

describe('graphUrl', () => {
  it('parses selection, hidden types, and tiles', () => {
    expect(parseGraphUrl('?node=Ticker%3ANVDA&hide=Event%2CSector&tiles=flow%2Calerts')).toEqual({
      node: 'Ticker:NVDA',
      hidden: ['Event', 'Sector'],
      tiles: ['board', 'flow', 'alerts'],
    });
  });

  it('reads blanks as absent and unknown tiles as board-only', () => {
    expect(parseGraphUrl('')).toEqual({ node: null, hidden: [], tiles: null });
    expect(parseGraphUrl('?node=&hide=,,')).toEqual({ node: null, hidden: [], tiles: null });
    expect(parseGraphUrl('?tiles=bogus')).toEqual({ node: null, hidden: [], tiles: ['board'] });
  });

  it('honors the legacy single-drawer param', () => {
    expect(parseGraphUrl('?drawer=alerts')).toEqual({
      node: null,
      hidden: [],
      tiles: ['board', 'alerts'],
    });
  });

  it('round-trips state through build and parse', () => {
    const state: { node: string; hidden: string[]; tiles: ['board', 'flow'] } = {
      node: 'Ticker:NVDA',
      hidden: ['Event'],
      tiles: ['board', 'flow'],
    };
    const url = buildGraphUrl(state);
    expect(url.startsWith('?')).toBe(true);
    expect(parseGraphUrl(url)).toEqual(state);
  });

  it('omits the tiles param for a board-only workspace', () => {
    const url = buildGraphUrl({ node: 'Ticker:NVDA', hidden: [], tiles: ['board'] });
    expect(url).toBe('?node=Ticker%3ANVDA');
  });
});
