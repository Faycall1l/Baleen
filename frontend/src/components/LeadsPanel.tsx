import { useMemo } from 'react';

import { LEAD_TAG, toLeads } from '../leads';
import type { SignalsBundle } from '../types';

interface Props {
  signals: SignalsBundle | null;
  error: string | null;
  onTickerClick: (ticker: string) => void;
}

/** Story leads: today's pitches for a congressional-trading beat —
 * numbered, headlined like a story, every ticker tappable into the graph.
 * The window label keeps the timeframe honest.
 */
export default function LeadsPanel({ signals, error, onTickerClick }: Props) {
  const leads = useMemo(() => toLeads(signals), [signals]);
  return (
    <>
      <h2>Story leads</h2>
      {error && (
        <div className="empty-hint">
          <div className="error">{error}</div>
        </div>
      )}
      {!error && !signals && <div className="empty-hint">Loading leads…</div>}
      {!error && signals && leads.length === 0 && (
        <div className="empty-hint">Quiet on this beat — no leads in window.</div>
      )}
      {signals && (
        <div className="signal-list">
          {signals.window_days > 0 && (
            <div className="lead-window">
              Last {signals.window_days} days · {leads.length} lead{leads.length === 1 ? '' : 's'}
            </div>
          )}
          {leads.map((lead, i) => (
            <div key={lead.key} className="alert-item signal-row">
              <div className="alert-title">
                <span className="lead-rank">#{i + 1}</span>
                <span className="alert-badge">{LEAD_TAG[lead.kind]}</span>
              </div>
              <div className="signal-headline">
                {lead.ticker ? (
                  <button
                    type="button"
                    className="node-link"
                    onClick={() => lead.ticker && onTickerClick(lead.ticker)}
                  >
                    {lead.headline}
                  </button>
                ) : (
                  <span>{lead.headline}</span>
                )}
              </div>
              <div className="alert-summary">
                {lead.dek}{' '}
                {lead.symbols.map((s) => (
                  <button key={s} type="button" className="node-link" onClick={() => onTickerClick(s)}>
                    {s}
                  </button>
                ))}{' '}
                {lead.url && (
                  <a href={lead.url} target="_blank" rel="noreferrer">
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
