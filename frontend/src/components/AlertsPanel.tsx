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
      {alerts.map((a) => (
        <div className="alert-item" key={a.id}>
          <div className="alert-title">
            <span className="alert-badge">{a.rule_name}</span> {a.title}
          </div>
          <div className="alert-summary">{a.summary}</div>
          <div className="alert-meta">
            <span>{fmtDate(a.created_at)}</span>
            <a href={a.tweet_url} target="_blank" rel="noreferrer">
              source ↗
            </a>
          </div>
        </div>
      ))}
    </>
  );
}
