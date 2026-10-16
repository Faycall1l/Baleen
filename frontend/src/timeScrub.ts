// Time scrubber helpers: the canvas can freeze on an as-of snapshot
// ("what did the graph look like last Tuesday?") and jump back to live.
// Day granularity, 30-day window: matches the options-radar horizon and
// keeps each frozen frame to one bounded render.

export const SCRUB_DAYS = 30;

export function daysAgoToISO(daysAgo: number, nowMs: number): string {
  return new Date(nowMs - daysAgo * 86400 * 1000).toISOString();
}

export function frameLabel(beforeISO: string | null): string {
  if (!beforeISO) return 'LIVE';
  return `frozen ${beforeISO.slice(0, 10)}`;
}
