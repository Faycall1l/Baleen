// Pure helpers for rendering grounded query results as a terminal table.

export const MAX_TABLE_ROWS = 50;

export function isUrl(value: unknown): value is string {
  return typeof value === 'string' && /^(https?:\/\/)/.test(value);
}

export function formatCell(value: unknown): string {
  if (value == null) return '—';
  if (typeof value === 'boolean') return value ? 'TRUE' : 'FALSE';
  if (typeof value === 'number') return value.toLocaleString('en-US');
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

export function visibleRows(rows: Record<string, unknown>[]): Record<string, unknown>[] {
  return rows.slice(0, MAX_TABLE_ROWS);
}
