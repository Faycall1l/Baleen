import { matchAlerts } from '../personView';
import type { Alert, CommitteeCard, GraphNode } from '../types';

interface Props {
  node: GraphNode;
  card: CommitteeCard | null;
  cardError: string | null;
  alerts: Alert[];
  onClose: () => void;
  onAlertClick?: (alert: Alert) => void;
  onMemberClick?: (politicianId: string) => void;
}

export default function CommitteeDetail({ node, card, cardError, alerts, onClose, onAlertClick, onMemberClick }: Props) {
  const trades = card ? matchAlerts(card.trades, alerts) : [];
  return (
    <div className="node-detail">
      <div className="node-title">
        <h3>{card?.name ?? node.title ?? node.id}</h3>
        <div className="label">Committee · {card?.id ?? node.id}</div>
        <div style={{ marginTop: 6 }}>
          <button onClick={onClose}>Close</button>
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
          {card.sectors.length > 0 && (
            <div className="node-links">
              <div className="node-links-title">Oversees</div>
              {card.sectors.map((s) => (
                <span key={s} className="ref-chip">
                  {s}
                </span>
              ))}
            </div>
          )}
          {card.members.length > 0 && (
            <div className="node-links">
              <div className="node-links-title">Members · {card.members.length}</div>
              {card.members.map((m) => (
                <button
                  key={m.id}
                  className="node-link"
                  onClick={onMemberClick ? () => onMemberClick(m.id) : undefined}
                  disabled={!onMemberClick}
                >
                  {m.name ?? m.id}
                  {m.party ? ` (${m.party})` : ''} ↗
                </button>
              ))}
            </div>
          )}
          <div className="node-links">
            <div className="node-links-title">Member trades · {trades.length}</div>
            {trades.length === 0 && <div className="empty-hint">No member trades in the graph.</div>}
            {trades.map(({ row: t, alerts: tradeAlerts }) => (
              <div className="alert-item" key={t.event_id}>
                <div className="alert-title">
                  <b>{t.who ?? '—'}</b> · {t.ticker ?? '—'} {t.side ?? ''}
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
