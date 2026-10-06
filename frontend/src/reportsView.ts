import type { Report } from './types';

// Reports view helpers (R12): the tile mixes the daily digest with an
// undifferentiated brief scroll. Split them: one digest header card, then
// briefs grouped by day with their graph refs as chips.

export interface BriefDay {
  day: string;
  label: string;
  items: Report[];
}

export function briefDayKey(report: Report): string {
  return report.created_at?.slice(0, 10) ?? 'undated';
}

export function dayLabel(day: string, today = new Date()): string {
  if (day === 'undated') return 'Undated';
  const iso = today.toISOString().slice(0, 10);
  if (day === iso) return 'Today';
  const yesterday = new Date(today.getTime() - 24 * 3600 * 1000)
    .toISOString()
    .slice(0, 10);
  if (day === yesterday) return 'Yesterday';
  return day;
}

export function groupBriefsByDay(briefs: Report[], today = new Date()): BriefDay[] {
  const byDay = new Map<string, Report[]>();
  for (const brief of briefs) {
    const key = briefDayKey(brief);
    const list = byDay.get(key) ?? [];
    list.push(brief);
    byDay.set(key, list);
  }
  return [...byDay.entries()]
    .sort(([a], [b]) => {
      // Undated briefs sink to the bottom regardless of lexicography.
      if (a === b) return 0;
      if (a === 'undated') return 1;
      if (b === 'undated') return -1;
      return a < b ? 1 : -1;
    })
    .map(([day, items]) => ({
      day,
      label: dayLabel(day, today),
      items: [...items].sort((x, y) => (x.id < y.id ? 1 : -1)),
    }));
}
