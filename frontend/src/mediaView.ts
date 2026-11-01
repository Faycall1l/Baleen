/** Pure presentation helpers for the media panel (testable without a clock). */

export function fmtAge(iso: string, now: number = Date.now()): string {
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return '—';
  const minutes = Math.round((now - ms) / 60_000);
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes}m ago`;
  if (minutes < 24 * 60) return `${Math.round(minutes / 60)}h ago`;
  return new Date(ms).toLocaleDateString();
}
