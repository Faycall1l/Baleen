import { describe, expect, it } from 'vitest';

import { ONBOARDING_STEPS } from './onboarding';

describe('onboarding tour', () => {
  it('covers tape, ask, alerts, and graph in order', () => {
    expect(ONBOARDING_STEPS.map((s) => s.id)).toEqual([
      'flow',
      'ask',
      'alerts',
      'graph',
    ]);
  });

  it('gives every step a title and a body with no empties', () => {
    for (const step of ONBOARDING_STEPS) {
      expect(step.title.length).toBeGreaterThan(0);
      expect(step.body.length).toBeGreaterThan(20);
    }
  });
});
