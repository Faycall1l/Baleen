import type { LeaderboardEntry } from '../types';

interface Props {
  leaders: LeaderboardEntry[];
  error: string | null;
}

export default function LeaderboardPanel({ leaders, error }: Props) {
  return (
    <>
      <h2>Leaderboard</h2>
      {error && (
        <div className="empty-hint">
          <div className="error">{error}</div>
        </div>
      )}
      {!error && leaders.length === 0 && (
        <div className="empty-hint">
          No ranked trades yet. Returns post once filings age enough to score.
        </div>
      )}
      {leaders.map((l, i) => (
        <div className="alert-item" key={`${l.politician}-${l.ticker}-${i}`}>
          <div className="alert-title">
            <span className="alert-badge">#{i + 1}</span> {l.politician} · {l.ticker}
          </div>
          <div className="alert-summary">
            {l.side} · {l.amount} · {l.date} ·{' '}
            <b>
              {l.ret_pct > 0 ? '+' : ''}
              {l.ret_pct.toFixed(1)}%
            </b>{' '}
            since trade
            {l.excess_pct != null && (
              <>
                {' '}({l.excess_pct > 0 ? '+' : ''}
                {l.excess_pct.toFixed(1)}% vs market)
              </>
            )}
          </div>
          <div className="alert-meta">
            <a href={l.source_url} target="_blank" rel="noreferrer">
              filing ↗
            </a>
          </div>
        </div>
      ))}
    </>
  );
}
