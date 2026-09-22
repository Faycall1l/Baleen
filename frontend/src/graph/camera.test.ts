import { describe, expect, it } from 'vitest';

import { fitView, panBy, screenToWorld, worldToScreen, zoomAt } from './camera';

const VP = { w: 1000, h: 800 };

describe('world/screen round-trip', () => {
  it('inverts exactly', () => {
    const cam = { x: 10, y: -20, ratio: 2.5 };
    const p = { x: 123.5, y: 456.25 };
    const back = screenToWorld(worldToScreen(p, cam, VP), cam, VP);
    expect(back.x).toBeCloseTo(p.x, 9);
    expect(back.y).toBeCloseTo(p.y, 9);
  });
});

describe('zoomAt', () => {
  it('keeps the world point under the cursor fixed', () => {
    const cam = { x: 0, y: 0, ratio: 1 };
    const before = screenToWorld({ x: 700, y: 100 }, cam, VP);
    const zoomed = zoomAt(cam, VP, 700, 100, 2);
    expect(zoomed.ratio).toBe(2);
    const after = screenToWorld({ x: 700, y: 100 }, zoomed, VP);
    expect(after.x).toBeCloseTo(before.x, 9);
    expect(after.y).toBeCloseTo(before.y, 9);
  });

  it('clamps to the ratio bounds', () => {
    const cam = { x: 0, y: 0, ratio: 1 };
    expect(zoomAt(cam, VP, 500, 400, 1000).ratio).toBeLessThanOrEqual(20);
    expect(zoomAt(cam, VP, 500, 400, 0.00001).ratio).toBeGreaterThanOrEqual(0.05);
  });
});

describe('panBy', () => {
  it('moves in screen pixels scaled by zoom', () => {
    const cam = { x: 0, y: 0, ratio: 2 };
    expect(panBy(cam, 100, 0)).toEqual({ x: -50, y: 0, ratio: 2 });
  });
});

describe('fitView', () => {
  it('frames every point with margin', () => {
    const pts = [
      { x: -100, y: -50 },
      { x: 100, y: 50 },
    ];
    const cam = fitView(pts, VP);
    for (const p of pts) {
      const s = worldToScreen(p, cam, VP);
      expect(s.x).toBeGreaterThanOrEqual(0);
      expect(s.x).toBeLessThanOrEqual(VP.w);
      expect(s.y).toBeGreaterThanOrEqual(0);
      expect(s.y).toBeLessThanOrEqual(VP.h);
    }
  });

  it('handles empty and degenerate input', () => {
    expect(fitView([], VP)).toEqual({ x: 0, y: 0, ratio: 1 });
    expect(fitView([{ x: 5, y: 5 }], VP).ratio).toBeGreaterThan(1);
  });
});
