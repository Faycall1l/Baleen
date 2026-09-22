// Pure camera math: world <-> screen transforms, zoom, pan, fit. The canvas
// renderer applies these without keeping its own physics or animation state.

import { clamp } from './geometry';
import type { Camera } from './types';

export const MIN_RATIO = 0.05;
export const MAX_RATIO = 20;

export interface Viewport {
  w: number;
  h: number;
}

export function worldToScreen(
  p: { x: number; y: number },
  cam: Camera,
  viewport: Viewport,
): { x: number; y: number } {
  return {
    x: (p.x - cam.x) * cam.ratio + viewport.w / 2,
    y: (p.y - cam.y) * cam.ratio + viewport.h / 2,
  };
}

export function screenToWorld(
  p: { x: number; y: number },
  cam: Camera,
  viewport: Viewport,
): { x: number; y: number } {
  return {
    x: cam.x + (p.x - viewport.w / 2) / cam.ratio,
    y: cam.y + (p.y - viewport.h / 2) / cam.ratio,
  };
}

/** Zoom keeping the world point under the cursor fixed. */
export function zoomAt(
  cam: Camera,
  viewport: Viewport,
  sx: number,
  sy: number,
  factor: number,
): Camera {
  const ratio = clamp(cam.ratio * factor, MIN_RATIO, MAX_RATIO);
  if (ratio === cam.ratio) return cam;
  const wx = cam.x + (sx - viewport.w / 2) / cam.ratio;
  const wy = cam.y + (sy - viewport.h / 2) / cam.ratio;
  return {
    x: wx - (sx - viewport.w / 2) / ratio,
    y: wy - (sy - viewport.h / 2) / ratio,
    ratio,
  };
}

export function panBy(cam: Camera, dxScreen: number, dyScreen: number): Camera {
  return {
    x: cam.x - dxScreen / cam.ratio,
    y: cam.y - dyScreen / cam.ratio,
    ratio: cam.ratio,
  };
}

/** Frame all points with a margin; empty input centers at default zoom. */
export function fitView(
  points: { x: number; y: number }[],
  viewport: Viewport,
  padding = 60,
): Camera {
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const p of points) {
    if (!Number.isFinite(p.x) || !Number.isFinite(p.y)) continue;
    if (p.x < minX) minX = p.x;
    if (p.x > maxX) maxX = p.x;
    if (p.y < minY) minY = p.y;
    if (p.y > maxY) maxY = p.y;
  }
  if (!Number.isFinite(minX)) return { x: 0, y: 0, ratio: 1 };
  const bw = Math.max(maxX - minX, 1);
  const bh = Math.max(maxY - minY, 1);
  const ratio = clamp(
    Math.min(viewport.w / (bw + padding * 2), viewport.h / (bh + padding * 2)),
    MIN_RATIO,
    MAX_RATIO,
  );
  return { x: (minX + maxX) / 2, y: (minY + maxY) / 2, ratio };
}
