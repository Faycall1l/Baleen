import { describe, expect, it } from 'vitest';

import { centroid, clamp, hashStr, isFinitePoint } from './geometry';

describe('clamp', () => {
  it('bounds values', () => {
    expect(clamp(5, 0, 10)).toBe(5);
    expect(clamp(-1, 0, 10)).toBe(0);
    expect(clamp(11, 0, 10)).toBe(10);
  });
});

describe('isFinitePoint', () => {
  it('rejects missing and non-finite points', () => {
    expect(isFinitePoint({ x: 1, y: 2 })).toBe(true);
    expect(isFinitePoint(null)).toBe(false);
    expect(isFinitePoint(undefined)).toBe(false);
    expect(isFinitePoint({ x: NaN, y: 0 })).toBe(false);
    expect(isFinitePoint({ x: 0, y: Infinity })).toBe(false);
  });
});

describe('centroid', () => {
  it('averages valid points and ignores junk', () => {
    expect(centroid([{ x: 0, y: 0 }, { x: 4, y: 4 }])).toEqual({ x: 2, y: 2 });
    expect(centroid([])).toBeNull();
    expect(centroid([{ x: NaN, y: 0 }])).toBeNull();
  });
});

describe('hashStr', () => {
  it('is deterministic and bounded', () => {
    expect(hashStr('abc')).toBe(hashStr('abc'));
    expect(hashStr('abc')).not.toBe(hashStr('abd'));
    expect(hashStr('x')).toBeGreaterThanOrEqual(0);
    expect(hashStr('x')).toBeLessThan(1);
  });
});
