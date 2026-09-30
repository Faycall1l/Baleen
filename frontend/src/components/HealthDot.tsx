import type { Health } from '../types';

export interface HealthSummary {
  label: string;
  cls: 'ok' | 'degraded' | 'offline';
  detail: string;
}

// A 10px dot alone fails color-blind users and says nothing actionable, so
// the pill carries text plus a tooltip naming the failing check.
export function describeHealth(health: Health | null): HealthSummary {
  if (!health) {
    return { label: 'OFFLINE', cls: 'offline', detail: 'Backend unreachable' };
  }
  const down = Object.entries(health.checks)
    .filter(([, c]) => c.status !== 'ok')
    .map(([name]) => name);
  if (down.length > 0) {
    return { label: 'DEGRADED', cls: 'degraded', detail: `degraded: ${down.join(', ')} down` };
  }
  return { label: 'LIVE', cls: 'ok', detail: 'all systems ok' };
}

export default function HealthDot({ health }: { health: Health | null }) {
  const summary = describeHealth(health);
  return (
    <span className={`health-pill ${summary.cls}`} title={summary.detail}>
      <span className={`health-dot ${summary.cls}`} aria-hidden="true" />
      {summary.label}
    </span>
  );
}
