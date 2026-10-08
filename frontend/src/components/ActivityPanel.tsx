import { useMemo } from 'react';

import type { ActivityBundle } from '../types';

interface Props {
  activity: ActivityBundle | null;
  error: string | null;
  onTickerClick: (ticker: string) => void;
}

function deltaLabel(delta: number): string {
  if (delta > 0) return `+${delta}`;
  return String(delta);
}

/** Activity digest: what is moving right now. Movers rank tickers by event
 * acceleration vs the prior window, newcomers are first-seen tickers, and
 * fresh is the latest wire. Every ticker taps through to its node.
 */
export default function ActivityPanel({ activity, error, onTickerClick }: Props) {
  const movers = useMemo(
    () => (activity?.movers ?? []).filter((m) => typeof m.ticker === 'string' && m.ticker !== ''),
    [activity],
  );
  const newcomers = useMemo(
    () => (activity?.newcomers ?? []).filter((n) => typeof n.ticker === 'string' && n.ticker !== ''),
    [activity],
  );
  const fresh = useMemo(() => activity?.fresh ?? [], [activity]);
  return (
    <>
      <h2>Activity</h2>
      {error && (
        <div className="empty-hint">
          <div className="error">{error}</div>
        </div>
      )}
      {!error && !activity && <div className="empty-hint">Loading activity…</div>}
      {!error && activity && movers.length === 0 && newcomers.length === 0 && fresh.length === 0 && (
        <div className="empty-hint">Quiet window — nothing moved.</div>
      )}
      {activity && (
        <div className="signal-list">
          {activity.window_days > 0 && (
            <div className="lead-window">
              Last {activity.window_days} days vs prior {activity.window_days}
            </div>
          )}
          {movers.length > 0 && (
            <div className="alert-item signal-row">
              <div className="alert-title">
                <span className="alert-badge">Movers</span>
              </div>
              {movers.slice(0, 8).map((m) => (
                <div key={m.ticker} className="signal-headline">
                  <button type="button" className="node-link" onClick={() => m.ticker && onTickerClick(m.ticker)}>
                    {m.ticker}
                  </button>{' '}
                  <span className={m.delta > 0 ? 'signal-up' : m.delta < 0 ? 'signal-down' : ''}>
                    {deltaLabel(Number(m.delta) || 0)} ({Number(m.recent) || 0} vs {Number(m.prior) || 0})
                  </span>
                </div>
              ))}
            </div>
          )}
          {newcomers.length > 0 && (
            <div className="alert-item signal-row">
              <div className="alert-title">
                <span className="alert-badge">New names</span>
              </div>
              <div className="alert-summary">
                {newcomers.slice(0, 8).map((n, i) => (
                  <span key={n.ticker}>
                    {i > 0 && ', '}
                    <button type="button" className="node-link" onClick={() => n.ticker && onTickerClick(n.ticker)}>
                      {n.ticker}
                    </button>
                  </span>
                ))}
              </div>
            </div>
          )}
          {fresh.length > 0 && (
            <div className="alert-item signal-row">
              <div className="alert-title">
                <span className="alert-badge">Fresh</span>
              </div>
              {fresh.slice(0, 8).map((f, i) => (
                <div key={f.event_id ?? `fresh-${i}`} className="alert-summary">
                  {f.ticker ? (
                    <button type="button" className="node-link" onClick={() => f.ticker && onTickerClick(f.ticker)}>
                      {f.ticker}
                    </button>
                  ) : (
                    <span>Untagged</span>
                  )}
                  {f.kind ? ` · ${String(f.kind).replace(/_/g, ' ')}` : ''}
                  {f.who ? ` · ${f.who}` : ''}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </>
  );
}
