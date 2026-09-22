// Canvas 2D renderer: pure pixels from a view model, no animation loop, no
// physics, no retained scene graph. Repaints happen only when inputs change
// (data, camera, selection, filters). Everything drawn here is a direct
// function of arguments, which is what makes the view predictable.

import type { Camera } from './types';
import type { ViewEdge, ViewModel, ViewNode } from './types';

export interface PaintSize {
  w: number;
  h: number;
  dpr: number;
}

function applyCamera(ctx: CanvasRenderingContext2D, cam: Camera, size: PaintSize): void {
  ctx.setTransform(
    size.dpr * cam.ratio,
    0,
    0,
    size.dpr * cam.ratio,
    size.dpr * (size.w / 2 - cam.x * cam.ratio),
    size.dpr * (size.h / 2 - cam.y * cam.ratio),
  );
}

function edgeControl(
  ax: number,
  ay: number,
  bx: number,
  by: number,
): { cx: number; cy: number } {
  const mx = (ax + bx) / 2;
  const my = (ay + by) / 2;
  const dx = bx - ax;
  const dy = by - ay;
  const len = Math.hypot(dx, dy) || 1;
  // Gentle perpendicular bow so parallel edges separate.
  return { cx: mx - (dy / len) * len * 0.12, cy: my + (dx / len) * len * 0.12 };
}

function paintEdge(
  ctx: CanvasRenderingContext2D,
  byId: Map<string, ViewNode>,
  e: ViewEdge,
  cam: Camera,
): void {
  const a = byId.get(e.source);
  const b = byId.get(e.target);
  if (!a || !b) return;
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  if (dx === 0 && dy === 0) {
    // Self-loop: small ring offset above the node.
    ctx.strokeStyle = e.color;
    ctx.globalAlpha = e.dimmed ? 0.1 : 0.5;
    ctx.lineWidth = 1.2;
    ctx.beginPath();
    ctx.arc(a.x, a.y - a.size - 4, 5, 0, 2 * Math.PI);
    ctx.stroke();
    ctx.globalAlpha = 1;
    return;
  }
  const c = edgeControl(a.x, a.y, b.x, b.y);
  ctx.strokeStyle = e.color;
  ctx.globalAlpha = e.dimmed ? 0.1 : 0.5;
  ctx.lineWidth = 1.2;
  ctx.beginPath();
  ctx.moveTo(a.x, a.y);
  ctx.quadraticCurveTo(c.cx, c.cy, b.x, b.y);
  ctx.stroke();
  // Arrowhead at the target end, oriented along the curve tangent.
  const t = 0.92;
  const mt = 1 - t;
  const tx = 2 * mt * (c.cx - a.x) + 2 * t * (b.x - c.cx);
  const ty = 2 * mt * (c.cy - a.y) + 2 * t * (b.y - c.cy);
  const ang = Math.atan2(ty, tx);
  const s = 6 / cam.ratio + b.size * 0.35;
  ctx.beginPath();
  ctx.moveTo(b.x, b.y);
  ctx.lineTo(b.x - s * Math.cos(ang - 0.42), b.y - s * Math.sin(ang - 0.42));
  ctx.moveTo(b.x, b.y);
  ctx.lineTo(b.x - s * Math.cos(ang + 0.42), b.y - s * Math.sin(ang + 0.42));
  ctx.stroke();
  ctx.globalAlpha = 1;
}

function paintNode(ctx: CanvasRenderingContext2D, n: ViewNode, cam: Camera): void {
  const r = n.size;
  ctx.globalAlpha = n.dimmed ? 0.12 : 1;
  // Halo.
  ctx.fillStyle = n.color;
  ctx.globalAlpha = (n.dimmed ? 0.12 : 1) * 0.15;
  ctx.beginPath();
  ctx.arc(n.x, n.y, r * 2.2, 0, 2 * Math.PI);
  ctx.fill();
  // Body.
  ctx.globalAlpha = n.dimmed ? 0.12 : 1;
  ctx.beginPath();
  ctx.arc(n.x, n.y, r, 0, 2 * Math.PI);
  ctx.fill();
  // Darker rim + specular dot for a physical feel.
  ctx.strokeStyle = n.borderColor;
  ctx.lineWidth = Math.max(1 / cam.ratio, r * 0.16);
  ctx.stroke();
  ctx.fillStyle = 'rgba(255,255,255,0.55)';
  ctx.beginPath();
  ctx.arc(n.x - 0.3 * r, n.y - 0.3 * r, 0.32 * r, 0, 2 * Math.PI);
  ctx.fill();
  // Selection ring.
  if (n.selected) {
    ctx.strokeStyle = '#e0a458';
    ctx.lineWidth = 2 / cam.ratio;
    ctx.beginPath();
    ctx.arc(n.x, n.y, r + 3 / cam.ratio, 0, 2 * Math.PI);
    ctx.stroke();
  }
  ctx.globalAlpha = 1;
}

function paintLabel(ctx: CanvasRenderingContext2D, n: ViewNode, cam: Camera): void {
  if (!n.label || n.dimmed) return;
  // Hubs and zoomed reads carry labels; the selected node always does.
  if (!n.selected && n.size * cam.ratio < 8) return;
  const px = 13 / cam.ratio;
  ctx.font = `500 ${px}px system-ui, sans-serif`;
  ctx.textAlign = 'left';
  ctx.textBaseline = 'middle';
  const x = n.x + n.size + 4 / cam.ratio;
  const y = n.y;
  ctx.lineWidth = 3 / cam.ratio;
  ctx.strokeStyle = '#1b1b1f';
  ctx.strokeText(n.label, x, y);
  ctx.fillStyle = '#d6d6dd';
  ctx.fillText(n.label, x, y);
}

export function paint(
  ctx: CanvasRenderingContext2D,
  model: ViewModel,
  cam: Camera,
  size: PaintSize,
  bg = '#1b1b1f',
): void {
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, size.w * size.dpr, size.h * size.dpr);
  applyCamera(ctx, cam, size);
  const byId = new Map(model.nodes.map((n) => [n.id, n]));
  for (const e of model.edges) paintEdge(ctx, byId, e, cam);
  for (const n of model.nodes) paintNode(ctx, n, cam);
  for (const n of model.nodes) paintLabel(ctx, n, cam);
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.globalAlpha = 1;
}
