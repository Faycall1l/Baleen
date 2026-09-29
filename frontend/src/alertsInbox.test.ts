import { describe, expect, it } from 'vitest';

import { filterAlerts, maxId, ruleOptions, shortRuleLabel, unreadCount } from './alertsInbox';
import type { Alert } from './types';

function alert(id: number, rule_name: string, title: string): Alert {
  return {
    id,
    rule_name,
    event_id: `e${id}`,
    tweet_id: `t${id}`,
    tweet_url: 'https://x.com/unusual_whales/status/1',
    title,
    summary: 'summary',
    graph_refs: [],
    created_at: '2026-09-29T00:00:00Z',
  };
}

const ALERTS = [
  alert(1, 'unusual_options_sweep', 'NVDA $130 calls sweep 5.2x'),
  alert(2, 'politician_trades_in_oversight_sector', 'Cassidy sold UNH'),
  alert(3, 'unusual_options_sweep', 'AMD $180 puts sweep 3.1x'),
];

describe('alerts inbox', () => {
  it('lists one filter chip per rule present', () => {
    expect(ruleOptions(ALERTS)).toEqual([
      'politician_trades_in_oversight_sector',
      'unusual_options_sweep',
    ]);
  });

  it('filters by rule and free text together', () => {
    expect(filterAlerts(ALERTS, { rule: 'all', query: '' })).toHaveLength(3);
    expect(
      filterAlerts(ALERTS, { rule: 'unusual_options_sweep', query: '' }).map((a) => a.id),
    ).toEqual([1, 3]);
    expect(
      filterAlerts(ALERTS, { rule: 'all', query: 'cassidy' }).map((a) => a.id),
    ).toEqual([2]);
    expect(
      filterAlerts(ALERTS, { rule: 'unusual_options_sweep', query: 'cassidy' }),
    ).toHaveLength(0);
  });

  it('counts unread as everything past the seen max', () => {
    expect(maxId(ALERTS)).toBe(3);
    expect(unreadCount(ALERTS, 0)).toBe(3);
    expect(unreadCount(ALERTS, 2)).toBe(1);
    expect(unreadCount(ALERTS, 3)).toBe(0);
  });

  it('shortens known rule names for chips, passes others through', () => {
    expect(shortRuleLabel('unusual_options_sweep')).toBe('options sweep');
    expect(shortRuleLabel('some_future_rule')).toBe('some_future_rule');
  });
});
