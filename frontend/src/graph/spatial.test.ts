import { describe, expect, it } from 'vitest';

import { nearestInDirection, type PointNode } from './spatial';

const NODES: PointNode[] = [
  { id: 'c', x: 0, y: 0 },
  { id: 'right-near', x: 10, y: 1 },
  { id: 'right-far', x: 100, y: 0 },
  { id: 'up', x: 0, y: -50 },
  { id: 'down', x: 1, y: 50 },
  { id: 'left', x: -30, y: 0 },
];

describe('nearestInDirection', () => {
  it('picks the nearest node ahead, preferring angle over distance', () => {
    // right-near is closer but slightly off-axis; right-far is dead ahead
    // yet much further — angle wins.
    expect(nearestInDirection(NODES, 'c', 1, 0)).toBe('right-far');
    expect(nearestInDirection(NODES, 'c', 0, -1)).toBe('up');
    expect(nearestInDirection(NODES, 'c', 0, 1)).toBe('down');
    expect(nearestInDirection(NODES, 'c', -1, 0)).toBe('left');
  });

  it('returns null with nowhere to go', () => {
    expect(nearestInDirection([{ id: 'solo', x: 0, y: 0 }], 'solo', 1, 0)).toBeNull();
    expect(nearestInDirection(NODES, 'missing', 1, 0)).toBeNull();
    expect(nearestInDirection(NODES, 'c', 0, 0)).toBeNull();
  });

  it('ignores nodes behind and exactly sideways', () => {
    const nodes: PointNode[] = [
      { id: 'c', x: 0, y: 0 },
      { id: 'side', x: 0, y: 10 },
      { id: 'ahead', x: 5, y: 1 },
    ];
    expect(nearestInDirection(nodes, 'c', 1, 0)).toBe('ahead');
  });
});
