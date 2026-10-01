import type { Alert, PersonTrade } from './types';

// Join event-carrying rows to fired alerts by event id (R17b/R24b): cards
// show which rows already raised an alert without a second round-trip.
export interface RowWithAlerts<T> {
  row: T;
  alerts: Alert[];
}

export function matchAlerts<T extends { event_id: string }>(rows: T[], alerts: Alert[]): RowWithAlerts<T>[] {
  const byEvent = new Map<string, Alert[]>();
  for (const alert of alerts) {
    const list = byEvent.get(alert.event_id) ?? [];
    list.push(alert);
    byEvent.set(alert.event_id, list);
  }
  return rows.map((row) => ({
    row,
    alerts: byEvent.get(row.event_id) ?? [],
  }));
}

export type TradeWithAlerts = RowWithAlerts<PersonTrade>;

export function personIdForNode(label: string, id: string, props: Record<string, unknown>): string | null {
  if (label !== 'Politician') return null;
  const raw = props['id'];
  if (typeof raw === 'string' && raw.length > 0) return raw;
  const suffix = id.split(':').slice(1).join(':');
  return suffix.length > 0 ? suffix : null;
}
