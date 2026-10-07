import { describe, expect, it } from 'vitest';

import { describeHealth } from './HealthDot';

describe('describeHealth', () => {
  it('reads checking before the first poll, offline only after a failed check', () => {
    expect(describeHealth(null, false).label).toBe('···');
    expect(describeHealth(null, true).label).toBe('OFFLINE');
  });

  it('names failing checks in degraded detail', () => {
    const summary = describeHealth({
      status: 'degraded',
      app: 'baleen',
      checks: { postgres: { status: 'ok' }, neo4j: { status: 'down' } },
    });
    expect(summary.label).toBe('DEGRADED');
    expect(summary.detail).toContain('neo4j');
  });
});
