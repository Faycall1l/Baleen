import { describe, expect, it } from 'vitest';

import { soloHidden } from './typeFilters';

const ALL = ['Politician', 'Event', 'Ticker'];

describe('soloHidden', () => {
  it('hides everything except the alt-clicked type', () => {
    expect(soloHidden(ALL, 'Ticker', new Set())).toEqual(new Set(['Politician', 'Event']));
  });

  it('restores all when alt-clicking the soloed type', () => {
    expect(soloHidden(ALL, 'Ticker', new Set(['Politician', 'Event']))).toEqual(new Set());
  });

  it('keeps unknown labels out of the computation', () => {
    expect(soloHidden(ALL, 'Ticker', new Set(['Mystery']))).toEqual(
      new Set(['Politician', 'Event']),
    );
  });
});
