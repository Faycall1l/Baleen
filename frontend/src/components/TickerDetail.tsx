import { formatExpiry, formatRatio, formatStrike, sentimentClass } from '../flow';
import { matchAlerts } from '../personView';
import type { Alert, GraphNode, TickerCard } from '../types';

interface Props {
  node: GraphNode;
  card: TickerCard | null;
  cardError: string | null;
  alerts: Alert[];
  onClose: () => void;
  onAlertClick?: (alert: Alert) => void;
  onViewInTape: (ticker: string) => void;
}

export default function TickerDetail({ node, card, cardError, alerts, onClose, onAlertClick, onViewInTape }: Props) {
  const trades = card ? matchAlerts(card.trades, alerts) : [];
  const prints = card ? matchAlerts(card.flow, alerts) : [];
  return (
    <div className="node-detail">
      <div className="node-title">
        <h3>{card?.ticker ?? node.title}</h3>
        <div className="label">
          Ticker{card?.sector ? ` · ${card.sector}` : ` · ${node.id}`}
        </div>
        <div style={{ marginTop: 6, display: 'flex', gap: 8 }}>
          <button onClick={onClose}>Close</button>
          {card && (
            <button onClick={() => onViewInTape(card.ticker)} title="Filter the flow tape to this ticker">
              View in tape
            </button>
          )}
        </div>
      </div>

      {cardError && (
        <div className="empty-hint">
          <div className="error">{cardError}</div>
        </div>
      )}
      {!card && !cardError && <div className="empty-hint">Loading record…</div>}
      {card && (
        <div className="props">
          {prints.length > 0 && (
            <div className="node-links">
              <div className="node-links-title">Recent flow · {prints.length}</div>
              <table className="flow-tape">
                <thead>
                  <tr>
                    <th scope="col">Side</th>
                    <th scope="col" className="num">Strike</th>
                    <th scope="col">Expiry</th>
                    <th scope="col" className="num">Ratio</th>
                  </tr>
                </thead>
                <tbody>
                  {prints.map(({ row: f, alerts: printAlerts }) => (
                    <tr key={f.event_id} className={sentimentClass(f.sentiment ?? 'neutral')}>
                      <td>{f.side === 'call' ? 'CALL' : f.side === 'put' ? 'PUT' : '—'}</td>
                      <td className="num">{formatStrike(f.strike)}</td>
                      <td>{formatExpiry(f.expiration)}</td>
                      <td className="num ratio">
                        {formatRatio(f.ratio)}
                        {printAlerts.length > 0 ? ' ●' : ''}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <div className="node-links">
            <div className="node-links-title">Trades · {trades.length}</div>
            {trades.length === 0 && <div className="empty-hint">No disclosed trades in the graph.</div>}
            {trades.map(({ row: t, alerts: tradeAlerts }) => (
              <div className="alert-item" key={t.event_id}>
                <div className="alert-title">
                  <b>{t.who ?? '—'}</b> · {t.side ?? '—'}
                  {tradeAlerts.length > 0 && <span className="alert-badge">alert</span>}
                </div>
                <div className="alert-meta">
                  <span>{t.date ?? ''}</span>
                  <span>{t.amount ?? ''}</span>
                  {t.source_url && (
                    <a href={t.source_url} target="_blank" rel="noreferrer">
                      filing ↗
                    </a>
                  )}
                </div>
                {tradeAlerts.length > 0 && onAlertClick && (
                  <div className="node-links">
                    {tradeAlerts.map((a) => (
                      <button key={a.id} className="node-link" onClick={() => onAlertClick(a)}>
                        {a.title} ↗
                      </button>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
