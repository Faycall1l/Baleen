import type { Health } from '../types';

export default function HealthDot({ health }: { health: Health | null }) {
  if (!health) {
    return <span className="health-dot offline" title="Backend unreachable" />;
  }
  const down = Object.entries(health.checks)
    .filter(([, c]) => c.status !== 'ok')
    .map(([name]) => name);
  const title = down.length > 0 ? `degraded: ${down.join(', ')} down` : 'all systems ok';
  const cls = health.status === 'ok' ? 'ok' : 'degraded';
  return <span className={`health-dot ${cls}`} title={title} />;
}
