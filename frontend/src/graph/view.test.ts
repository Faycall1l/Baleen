import { describe, expect, it } from 'vitest';

import { eventTitle } from './nodes';

describe('eventTitle', () => {
  it('composes informative titles', () => {
    expect(eventTitle({ type: 'insider_trade', ticker: 'NVDA', transaction_type: 'buy' })).toBe('NVDA bought');
    expect(eventTitle({ type: 'options_flow', ticker: 'NVDA', contract_type: 'call', strike: 250 })).toBe(
      'NVDA call 250',
    );
    expect(eventTitle({})).toBe('Unparsed');
  });
});
