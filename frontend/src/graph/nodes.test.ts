import { describe, expect, it } from 'vitest';

import { nodeSubline } from './nodes';

describe('nodeSubline', () => {
  it('summarizes options flow with volume, ratio, and bias', () => {
    expect(
      nodeSubline('Event', {
        type: 'options_flow',
        volume: 87960,
        open_interest: 28,
        sentiment: 'bullish',
      }),
    ).toBe('VOL 87,960 · 3141.4x OI · BULLISH');
    expect(nodeSubline('Event', { type: 'options_flow' })).toBeNull();
  });

  it('summarizes trades with side, amount, and date', () => {
    expect(
      nodeSubline('Event', {
        type: 'congress_trade',
        transaction_type: 'buy',
        amount_range: '$1,001 - $15,000',
        transaction_date: '2026-09-01',
      }),
    ).toBe('BUY · $1,001 - $15,000 · 2026-09-01');
    expect(
      nodeSubline('Event', {
        type: 'insider_trade',
        transaction_type: 'sell',
        amount_range: '$4,960,397.88',
        transaction_date: '2026-09-28',
      }),
    ).toBe('SELL · $4,960,397.88 · 2026-09-28');
  });

  it('summarizes people and leaves the rest single-line', () => {
    expect(nodeSubline('Politician', { chamber: 'house', party: 'D' })).toBe('house · D');
    expect(nodeSubline('Insider', { role: 'Chief Executive Officer of Things' })).toBe(
      'Chief Executive Officer…',
    );
    expect(nodeSubline('Ticker', { ticker: 'NVDA' })).toBeNull();
    expect(nodeSubline('Event', { type: 'unparsed' })).toBeNull();
  });
});
