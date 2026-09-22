// Pointer hit-testing in world coordinates. Smallest containing circle
// wins, so dense hubs don't swallow their neighbors.

export interface HitCandidate {
  id: string;
  x: number;
  y: number;
  r: number;
}

export function hitNodeAt(
  candidates: HitCandidate[],
  wx: number,
  wy: number,
): string | null {
  let best: string | null = null;
  let bestR = Infinity;
  for (const c of candidates) {
    if (!Number.isFinite(c.x) || !Number.isFinite(c.y) || !Number.isFinite(c.r)) continue;
    const dx = wx - c.x;
    const dy = wy - c.y;
    if (dx * dx + dy * dy <= (c.r + 2) * (c.r + 2) && c.r < bestR) {
      best = c.id;
      bestR = c.r;
    }
  }
  return best;
}
