// Spatial keyboard navigation over canvas nodes (R28).
//
// Arrow keys move selection to the nearest node in that direction: among
// candidates strictly ahead (positive projection on the direction), win by
// smallest angle off-axis, ties by distance. Pure and deterministic so the
// steering rule unit-tests without a browser.

export interface PointNode {
  id: string;
  x: number;
  y: number;
}

export function nearestInDirection(
  nodes: PointNode[],
  fromId: string,
  dx: number,
  dy: number,
): string | null {
  const from = nodes.find((n) => n.id === fromId);
  if (!from) return null;
  const len = Math.hypot(dx, dy);
  if (len === 0 || !Number.isFinite(len)) return null;
  const ux = dx / len;
  const uy = dy / len;
  let best: string | null = null;
  let bestAngle = Infinity;
  let bestDist = Infinity;
  for (const n of nodes) {
    if (n.id === fromId) continue;
    const vx = n.x - from.x;
    const vy = n.y - from.y;
    const dist = Math.hypot(vx, vy);
    if (dist === 0 || !Number.isFinite(dist)) continue;
    const cos = (vx * ux + vy * uy) / dist;
    if (cos <= 0) continue;
    const angle = Math.acos(Math.min(1, Math.max(-1, cos)));
    if (angle < bestAngle - 1e-9 || (Math.abs(angle - bestAngle) <= 1e-9 && dist < bestDist)) {
      best = n.id;
      bestAngle = angle;
      bestDist = dist;
    }
  }
  return best;
}
