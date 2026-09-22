// Tiny pure geometry helpers for the canvas graph.

export function clamp(v: number, lo: number, hi: number): number {
  if (v < lo) return lo;
  if (v > hi) return hi;
  return v;
}

export function isFinitePoint(p: { x: number; y: number } | null | undefined): p is {
  x: number;
  y: number;
} {
  return (
    p !== null &&
    p !== undefined &&
    Number.isFinite(p.x) &&
    Number.isFinite(p.y)
  );
}

export function centroid(points: { x: number; y: number }[]): { x: number; y: number } | null {
  const valid = points.filter(isFinitePoint);
  if (valid.length === 0) return null;
  return {
    x: valid.reduce((s, p) => s + p.x, 0) / valid.length,
    y: valid.reduce((s, p) => s + p.y, 0) / valid.length,
  };
}

/** Deterministic [0,1) hash for stable seeding (no Math.random in layout). */
export function hashStr(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return (h >>> 0) / 4294967295;
}
