import { describe, expect, it } from 'vitest';

import { buildGraphUrl, parseGraphUrl } from './graphUrl';

describe('graphUrl', () => {
  it('parses selection, hidden types, and drawer', () => {
    expect(parseGraphUrl('?node=Ticker%3ANVDA&hide=Event%2CSector&drawer=alerts')).toEqual({
      node: 'Ticker:NVDA',
      hidden: ['Event', 'Sector'],
      drawer: 'alerts',
    });
  });

  it('reads blanks and unknown drawers as absent', () => {
    expect(parseGraphUrl('')).toEqual({ node: null, hidden: [], drawer: null });
    expect(parseGraphUrl('?node=&hide=,,&drawer=bogus')).toEqual({
      node: null,
      hidden: [],
      drawer: null,
    });
  });

  it('round-trips state through build and parse', () => {
    const state = { node: 'Ticker:NVDA', hidden: ['Event'], drawer: 'flow' as const };
    const url = buildGraphUrl(state);
    expect(url.startsWith('?')).toBe(true);
    expect(parseGraphUrl(url)).toEqual(state);
  });
});
