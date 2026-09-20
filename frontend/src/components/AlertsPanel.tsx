import type { Alert } from '../types';

interface Props {
  alerts: Alert[];
  error: string | null;
}

function fmtDate(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

// Short display labels + tints per rule. Unknown rules fall back to the raw
// name in the default badge style, so new rules never render broken.
const RULE_BADGE: Record<string, { label: string; color: string; background: string }> = {
  politician_trades_in_oversight_sector: {
    label: 'oversight',
    color: '#e0a458',
    background: '#3a2f22',
  },
  insider_large_buy: {
    label: 'insider $1M+',
    color: '#4db6ac',
    background: 'rgba(77,182,172,0.12)',
  },
  unusual_options_sweep: {
    label: 'options sweep',
    color: '#7ab8e8',
    background: 'rgba(122,184,232,0.12)',
  },
};

export default function AlertsPanel({ alerts, error }: Props) {
  return (
    <>
      <h2>Alerts</h2>
      {error && (
        <div className="empty-hint">
          <div className="error">{error}</div>
        </div>
      )}
      {!error && alerts.length === 0 && (
        <div className="empty-hint">
          No alerts yet. They fire when trades match a watch rule — oversight-sector
          trades, $1M+ insider buys, unusual options sweeps.
        </div>
      )}
      {alerts.map((a) => {
        const badge = RULE_BADGE[a.rule_name];
        return (
          <div className="alert-item" key={a.id}>
            <div className="alert-title">
              <span
                className="alert-badge"
                style={
                  badge
                    ? { color: badge.color, background: badge.background }
                    : undefined
                }
              >
                {badge ? badge.label : a.rule_name}
              </span>{' '}
              {a.title}
            </div>
            <div className="alert-summary">{a.summary}</div>
            <div className="alert-meta">
              <span>{fmtDate(a.created_at)}</span>
              <a href={a.tweet_url} target="_blank" rel="noreferrer">
                source ↗
              </a>
            </div>
          </div>
        );
      })}
    </>
  );
}
