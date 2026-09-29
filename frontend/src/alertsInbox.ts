import type { Alert } from './types';

export const SEEN_MAX_KEY = 'baleen.alerts.seenMax';

export interface InboxFilter {
  rule: string; // 'all' or a rule_name
  query: string;
}

export function ruleOptions(alerts: Alert[]): string[] {
  const rules = new Set<string>();
  for (const a of alerts) rules.add(a.rule_name);
  return [...rules].sort();
}

const SHORT_LABELS: Record<string, string> = {
  politician_trades_in_oversight_sector: 'oversight',
  insider_large_buy: 'insider $1M+',
  unusual_options_sweep: 'options sweep',
};

export function shortRuleLabel(rule: string): string {
  return SHORT_LABELS[rule] ?? rule;
}

export function filterAlerts(alerts: Alert[], filter: InboxFilter): Alert[] {
  const q = filter.query.trim().toLowerCase();
  return alerts.filter((a) => {
    if (filter.rule !== 'all' && a.rule_name !== filter.rule) return false;
    if (!q) return true;
    return `${a.title} ${a.summary}`.toLowerCase().includes(q);
  });
}

export function loadSeenMax(): number {
  try {
    const raw = localStorage.getItem(SEEN_MAX_KEY);
    const n = raw == null ? 0 : Number(raw);
    return Number.isFinite(n) && n > 0 ? n : 0;
  } catch {
    return 0;
  }
}

export function saveSeenMax(id: number): void {
  try {
    localStorage.setItem(SEEN_MAX_KEY, String(id));
  } catch {
    /* private mode etc. — unread badges just stay on */
  }
}

export function maxId(alerts: Alert[]): number {
  return alerts.reduce((m, a) => Math.max(m, a.id), 0);
}

export function unreadCount(alerts: Alert[], seenMax: number): number {
  return alerts.filter((a) => a.id > seenMax).length;
}
