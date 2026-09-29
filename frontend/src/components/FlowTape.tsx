import {
  formatContracts,
  formatExpiry,
  formatRatio,
  formatStrike,
  formatTapeTime,
  sentimentClass,
} from '../flow';
import type { FlowFilters, FlowPrint } from '../types';

interface Props {
  flow: FlowPrint[];
  count: number;
  error: string | null;
  filters: FlowFilters;
  onFiltersChange: (filters: FlowFilters) => void;
  onRetry: () => void;
}

export default function FlowTape({ flow, count, error, filters, onFiltersChange, onRetry }: Props) {
  return (
    <>
      <h2>Flow tape</h2>
      <div className="flow-controls">
        <label>
          Tickers
          <input
            value={filters.tickers.join(', ')}
            placeholder="NVDA, AMD"
            onChange={(e) =>
              onFiltersChange({
                ...filters,
                tickers: e.target.value
                  .split(',')
                  .map((t) => t.trim().toUpperCase())
                  .filter(Boolean),
              })
            }
          />
        </label>
        <label>
          Side
          <select
            value={filters.side}
            onChange={(e) =>
              onFiltersChange({ ...filters, side: e.target.value as FlowFilters['side'] })
            }
          >
            <option value="all">Calls + puts</option>
            <option value="call">Calls</option>
            <option value="put">Puts</option>
          </select>
        </label>
        <label>
          Min ratio
          <select
            value={filters.min_ratio}
            onChange={(e) => onFiltersChange({ ...filters, min_ratio: Number(e.target.value) })}
          >
            {[1, 2, 3, 5].map((ratio) => (
              <option key={ratio} value={ratio}>
                {ratio.toFixed(1)}x
              </option>
            ))}
          </select>
        </label>
      </div>

      {error && (
        <div className="empty-hint">
          <div className="error">{error}</div>
          <button type="button" onClick={onRetry}>
            Retry tape
          </button>
        </div>
      )}
      {!error && flow.length === 0 && (
        <div className="empty-hint">No unusual prints match these filters.</div>
      )}
      {flow.length > 0 && (
        <>
          <div className="flow-status">
            {count} prints · min {filters.min_ratio.toFixed(1)}x · {filters.window_days}d
          </div>
          <table className="flow-tape">
            <caption className="visually-hidden">
              Unusual options prints ranked by volume divided by open interest
            </caption>
            <thead>
              <tr>
                <th scope="col">Ticker</th>
                <th scope="col">Side</th>
                <th scope="col" className="num">Strike</th>
                <th scope="col">Expiry</th>
                <th scope="col" className="num">Vol</th>
                <th scope="col" className="num">OI</th>
                <th scope="col" className="num">Ratio</th>
                <th scope="col">Bias</th>
                <th scope="col">Seen</th>
                <th scope="col">Source</th>
              </tr>
            </thead>
            <tbody>
              {flow.map((print) => (
                <tr key={print.event_id} className={sentimentClass(print.sentiment)}>
                  <td className="ticker">{print.ticker}</td>
                  <td>{print.side === 'call' ? 'CALL' : 'PUT'}</td>
                  <td className="num">{formatStrike(print.strike)}</td>
                  <td>{formatExpiry(print.expiration)}</td>
                  <td className="num">{formatContracts(print.volume)}</td>
                  <td className="num">{formatContracts(print.open_interest)}</td>
                  <td className="num ratio">{formatRatio(print.ratio)}</td>
                  <td>{print.sentiment}</td>
                  <td>{formatTapeTime(print.first_seen_at)}</td>
                  <td>
                    {print.source_url ? (
                      <a href={print.source_url} target="_blank" rel="noreferrer">
                        quote ↗
                      </a>
                    ) : (
                      '—'
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </>
  );
}
