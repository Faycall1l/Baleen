import { describe, expect, it } from 'vitest';

import { dayLabel, groupBriefsByDay } from './reportsView';
import type { Report } from './types';

function brief(id: number, created_at?: string): Report {
  return {
    id,
    kind: 'brief',
    title: `Brief ${id}`,
    body: 'body',
    graph_refs: ['NVDA'],
    created_at,
  };
}

const TODAY = new Date('2026-09-30T12:00:00Z');

describe('reports view', () => {
  it('labels today and yesterday relative to now', () => {
    expect(dayLabel('2026-09-30', TODAY)).toBe('Today');
    expect(dayLabel('2026-09-29', TODAY)).toBe('Yesterday');
    expect(dayLabel('2026-09-20', TODAY)).toBe('2026-09-20');
    expect(dayLabel('undated', TODAY)).toBe('Undated');
  });

  it('groups briefs by day, newest day and newest brief first', () => {
    const groups = groupBriefsByDay(
      [
        brief(1, '2026-09-29T10:00:00Z'),
        brief(2, '2026-09-30T10:00:00Z'),
        brief(3, '2026-09-29T12:00:00Z'),
        brief(4),
      ],
      TODAY,
    );
    expect(groups.map((g) => g.label)).toEqual(['Today', 'Yesterday', 'Undated']);
    expect(groups[1].items.map((b) => b.id)).toEqual([3, 1]);
  });
});
