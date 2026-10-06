import { shortRuleLabel } from '../alertsInbox';
import type { Alert } from '../types';
import type { AlertRule } from '../api';

interface Props {
  alerts: Alert[];
  total: number;
  rules: string[];
  ruleStates: AlertRule[];
  error: string | null;
  onAlertClick?: (alert: Alert) => void;
  rule: string;
  onRuleChange: (rule: string) => void;
  query: string;
  onQueryChange: (query: string) => void;
  onToggleRule: (name: string, active: boolean) => void;
  seenMax: number;
}

function fmtDate(iso: string | null | undefined): string {
  if (typeof iso !== 'string' || iso === '') return '—';
  const ms = Date.parse(iso);
  return Number.isNaN(ms) ? '—' : new Date(ms).toLocaleString();
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

export default function AlertsPanel({
  alerts,
  total,
  rules,
  ruleStates,
  error,
  onAlertClick,
  rule,
  onRuleChange,
  query,
  onQueryChange,
  onToggleRule,
  seenMax,
}: Props) {
  return (
    <>
      <h2>Alerts</h2>
      <div className="inbox-controls">
        <div className="inbox-chips" role="group" aria-label="Filter by rule">
          {['all', ...rules].map((r) => (
            <button
              key={r}
              type="button"
              className={`inbox-chip${rule === r ? ' active' : ''}`}
              aria-pressed={rule === r}
              onClick={() => onRuleChange(r)}
            >
              {r === 'all' ? 'All' : shortRuleLabel(r)}
            </button>
          ))}
        </div>
        <input
          className="inbox-search"
          value={query}
          placeholder="Filter alerts…"
          aria-label="Filter alerts by text"
          onChange={(e) => onQueryChange(e.target.value)}
        />
        {ruleStates.length > 0 && (
          <div className="inbox-rules" aria-label="Mute rules">
            {ruleStates.map((r) => (
              <button
                key={r.name}
                type="button"
                className={`inbox-rule${r.active ? '' : ' muted'}`}
                aria-pressed={r.active}
                title={r.active ? `Mute ${r.name}` : `Unmute ${r.name}`}
                onClick={() => onToggleRule(r.name, !r.active)}
              >
                {shortRuleLabel(r.name)} · {r.active ? 'on' : 'muted'}
              </button>
            ))}
          </div>
        )}
      </div>
      {error && (
        <div className="empty-hint">
          <div className="error">{error}</div>
        </div>
      )}
      {!error && total === 0 && (
        <div className="empty-hint">
          No alerts yet. They fire when trades match a watch rule — oversight-sector
          trades, $1M+ insider buys, unusual options sweeps.
        </div>
      )}
      {!error && total > 0 && alerts.length === 0 && (
        <div className="empty-hint">No alerts match these filters.</div>
      )}
      {alerts.map((a) => {
        const badge = RULE_BADGE[a.rule_name];
        return (
          <div
            className={`alert-item${onAlertClick ? ' clickable' : ''}`}
            key={a.id}
            onClick={onAlertClick ? () => onAlertClick(a) : undefined}
          >
            <div className="alert-title">
              {a.id > seenMax && (
                <span className="unread-dot" aria-label="Unread alert" />
              )}
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
