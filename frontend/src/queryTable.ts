// Pure helpers for rendering grounded query results as a terminal table.

export const MAX_TABLE_ROWS = 50;

export function isUrl(value: unknown): value is string {
  return typeof value === 'string' && /^(https?:\/\/)/.test(value);
}

export function formatCell(value: unknown): string {
  if (value == null) return '—';
  if (typeof value === 'boolean') return value ? 'TRUE' : 'FALSE';
  if (typeof value === 'number') return value.toLocaleString('en-US');
  if (typeof value === 'string') return formatTemporal(value);
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

// Neo4j serializes datetimes with nanoseconds and an offset
// (2026-10-29T00:00:00.000000000+00:00). Show the date alone at midnight UTC,
// otherwise date and minutes; anything else passes through untouched.
const NEO4J_DATETIME = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}):\d{2}(?:\.\d+)?(Z|[+-]\d{2}:\d{2})$/;

function formatTemporal(value: string): string {
  const m = NEO4J_DATETIME.exec(value);
  if (!m) return value;
  const [, date, hm, tz] = m;
  if (hm === '00:00' && (tz === 'Z' || tz === '+00:00')) return date;
  return `${date} ${hm}${tz === 'Z' || tz === '+00:00' ? ' UTC' : ` ${tz}`}`;
}

export function visibleRows(rows: Record<string, unknown>[]): Record<string, unknown>[] {
  return rows.slice(0, MAX_TABLE_ROWS);
}
