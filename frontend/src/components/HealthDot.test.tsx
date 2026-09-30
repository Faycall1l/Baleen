import { describe, expect, it } from 'vitest';

import { describeHealth } from './HealthDot';

describe('health pill', () => {
  it('reads OFFLINE when the backend is unreachable', () => {
    expect(describeHealth(null)).toEqual({
      label: 'OFFLINE',
      cls: 'offline',
      detail: 'Backend unreachable',
    });
  });

  it('reads LIVE when every check passes', () => {
    expect(
      describeHealth({
        status: 'ok',
        app: 'baleen',
        checks: { postgres: { status: 'ok' }, neo4j: { status: 'ok' } },
      }),
    ).toEqual({ label: 'LIVE', cls: 'ok', detail: 'all systems ok' });
  });

  it('names the failing check when degraded', () => {
    expect(
      describeHealth({
        status: 'degraded',
        app: 'baleen',
        checks: { postgres: { status: 'ok' }, neo4j: { status: 'down', detail: 'timeout' } },
      }),
    ).toEqual({ label: 'DEGRADED', cls: 'degraded', detail: 'degraded: neo4j down' });
  });
});
