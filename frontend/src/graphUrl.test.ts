import { describe, expect, it } from 'vitest';

import { buildGraphUrl, parseGraphUrl } from './graphUrl';

describe('graphUrl', () => {
  it('parses selection, hidden types, and tiles', () => {
    expect(parseGraphUrl('?node=Ticker%3ANVDA&hide=Event%2CSector&tiles=flow%2Calerts')).toEqual({
      node: 'Ticker:NVDA',
      hidden: ['Event', 'Sector'],
      tiles: ['flow', 'alerts'],
      embed: null,
    });
  });

  it('reads blanks as absent and unknown tiles as empty', () => {
    expect(parseGraphUrl('')).toEqual({ node: null, hidden: [], tiles: null, embed: null });
    expect(parseGraphUrl('?node=&hide=,,')).toEqual({ node: null, hidden: [], tiles: null, embed: null });
    expect(parseGraphUrl('?tiles=bogus')).toEqual({ node: null, hidden: [], tiles: [], embed: null });
  });

  it('honors the legacy single-drawer param', () => {
    expect(parseGraphUrl('?drawer=alerts')).toEqual({
      node: null,
      hidden: [],
      tiles: ['alerts'],
      embed: null,
    });
  });

  it('round-trips state through build and parse', () => {
    const state: { node: string; hidden: string[]; tiles: ['board', 'flow']; embed: null } = {
      node: 'Ticker:NVDA',
      hidden: ['Event'],
      tiles: ['board', 'flow'],
      embed: null,
    };
    const url = buildGraphUrl(state);
    expect(url.startsWith('?')).toBe(true);
    expect(parseGraphUrl(url)).toEqual(state);
  });

  it('round-trips a boardless workspace', () => {
    const url = buildGraphUrl({ node: null, hidden: [], tiles: ['flow', 'alerts'] });
    expect(parseGraphUrl(url).tiles).toEqual(['flow', 'alerts']);
  });

  it('parses embed mode and rejects unknown panels', () => {
    expect(parseGraphUrl('?embed=leads').embed).toBe('leads');
    expect(parseGraphUrl('?embed=bogus').embed).toBeNull();
    expect(parseGraphUrl('?embed=leads&tiles=board').tiles).toEqual(['board']);
  });
});
