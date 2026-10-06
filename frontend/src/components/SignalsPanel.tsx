import { useMemo } from 'react';

import { kindTag, toSignalRows } from '../signalsView';
import type { SignalsBundle } from '../types';

interface Props {
  signals: SignalsBundle | null;
  error: string | null;
  onTickerClick: (ticker: string) => void;
}

/** Top signals (S1): the engine's five sections collapse into one ranked
 * list. Every row leads somewhere — tickers open their node (tape filter
 * when the node is missing), filings open the source, uncovered symbols
 * tap through one by one. Strength bars are within-kind percentiles.
 */
export default function SignalsPanel({ signals, error, onTickerClick }: Props) {
  const rows = useMemo(() => toSignalRows(signals), [signals]);
  return (
    <>
      <h2>Signals</h2>
      {error && (
        <div className="empty-hint">
          <div className="error">{error}</div>
        </div>
      )}
      {!error && !signals && <div className="empty-hint">Loading signals…</div>}
      {!error && signals && rows.length === 0 && (
        <div className="empty-hint">No signals in window.</div>
      )}
      {signals && (
        <div className="signal-list">
          {rows.map((r) => (
            <div key={r.key} className="alert-item signal-row">
              <div className="alert-title">
                <span className="alert-badge">{kindTag(r.kind)}</span>
                <span className="signal-score" title={`Top ${100 - r.score}% of its kind`}>
                  <span className="signal-score-bar" style={{ width: `${Math.max(4, r.score)}%` }} />
                </span>
              </div>
              <div className="signal-headline">
                {r.ticker ? (
                  <button type="button" className="node-link" onClick={() => r.ticker && onTickerClick(r.ticker)}>
                    {r.headline}
                  </button>
                ) : (
                  <span>{r.headline}</span>
                )}
              </div>
              <div className="alert-summary">
                {r.detail}{' '}
                {r.symbols.map((s) => (
                  <button key={s} type="button" className="node-link" onClick={() => onTickerClick(s)}>
                    {s}
                  </button>
                ))}{' '}
                {r.url && (
                  <a href={r.url} target="_blank" rel="noreferrer">
                    filing ↗
                  </a>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </>
  );
}
