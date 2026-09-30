import { matchAlerts } from '../personView';
import type { Alert, GraphNode, PersonCard } from '../types';

interface Props {
  node: GraphNode;
  person: PersonCard | null;
  personError: string | null;
  alerts: Alert[];
  onClose: () => void;
  onAlertClick?: (alert: Alert) => void;
}

export default function PoliticianDetail({ node, person, personError, alerts, onClose, onAlertClick }: Props) {
  const joined = person ? matchAlerts(person.trades, alerts) : [];
  return (
    <div className="node-detail">
      <div className="node-title">
        <h3>{person?.name ?? node.title}</h3>
        <div className="label">
          Politician · {[person?.chamber, person?.party].filter(Boolean).join(' · ') || node.id}
        </div>
        <div style={{ marginTop: 6 }}>
          <button onClick={onClose}>Close</button>
        </div>
      </div>

      {personError && (
        <div className="empty-hint">
          <div className="error">{personError}</div>
        </div>
      )}
      {!person && !personError && <div className="empty-hint">Loading record…</div>}
      {person && (
        <div className="props">
          {person.committees.length > 0 && (
            <div className="node-links">
              <div className="node-links-title">Committees</div>
              {person.committees.map((c) => (
                <span key={c.id} className="ref-chip">
                  {c.name ?? c.id}
                </span>
              ))}
            </div>
          )}
          <div className="node-links">
            <div className="node-links-title">Trades · {joined.length}</div>
            {joined.length === 0 && <div className="empty-hint">No disclosed trades in the graph.</div>}
            {joined.map(({ trade, alerts: tradeAlerts }) => (
              <div className="alert-item" key={trade.event_id}>
                <div className="alert-title">
                  <b>{trade.ticker ?? '—'}</b> · {trade.side ?? '—'}
                  {tradeAlerts.length > 0 && <span className="alert-badge">alert</span>}
                </div>
                <div className="alert-meta">
                  <span>{trade.date ?? ''}</span>
                  <span>{trade.amount ?? ''}</span>
                  {trade.source_url && (
                    <a href={trade.source_url} target="_blank" rel="noreferrer">
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
