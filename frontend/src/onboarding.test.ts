import { describe, expect, it } from 'vitest';

import { ONBOARDING_STEPS } from './onboarding';

describe('onboarding tour', () => {
  it('covers leads, ask, alerts, and tiles in order', () => {
    expect(ONBOARDING_STEPS.map((s) => s.id)).toEqual([
      'leads',
      'ask',
      'alerts',
      'tiles',
    ]);
  });

  it('gives every step a title and a body with no empties', () => {
    for (const step of ONBOARDING_STEPS) {
      expect(step.title.length).toBeGreaterThan(0);
      expect(step.body.length).toBeGreaterThan(20);
    }
  });
});
