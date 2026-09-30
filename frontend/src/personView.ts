import type { Alert, PersonTrade } from './types';

// Join person trades to fired alerts by event id (R17b): the card shows
// which trades already raised an alert without a second round-trip.
export interface TradeWithAlerts {
  trade: PersonTrade;
  alerts: Alert[];
}

export function matchAlerts(trades: PersonTrade[], alerts: Alert[]): TradeWithAlerts[] {
  const byEvent = new Map<string, Alert[]>();
  for (const alert of alerts) {
    const list = byEvent.get(alert.event_id) ?? [];
    list.push(alert);
    byEvent.set(alert.event_id, list);
  }
  return trades.map((trade) => ({
    trade,
    alerts: byEvent.get(trade.event_id) ?? [],
  }));
}

export function personIdForNode(label: string, id: string, props: Record<string, unknown>): string | null {
  if (label !== 'Politician') return null;
  const raw = props['id'];
  if (typeof raw === 'string' && raw.length > 0) return raw;
  const suffix = id.split(':').slice(1).join(':');
  return suffix.length > 0 ? suffix : null;
}
