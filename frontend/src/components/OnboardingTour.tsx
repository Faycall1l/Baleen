import { useState } from 'react';

import { ONBOARDING_STEPS } from '../onboarding';

interface Props {
  onDone: () => void;
}

export default function OnboardingTour({ onDone }: Props) {
  const [index, setIndex] = useState(0);
  const step = ONBOARDING_STEPS[index];
  const last = index === ONBOARDING_STEPS.length - 1;

  return (
    <div className="onboarding-overlay" role="dialog" aria-label="Welcome to Baleen">
      <div className="onboarding-card">
        <div className="onboarding-kicker">BALEEN · 60-SECOND TOUR</div>
        <h2>{step.title}</h2>
        <p>{step.body}</p>
        <div className="onboarding-progress" aria-label={`Step ${index + 1} of ${ONBOARDING_STEPS.length}`}>
          {ONBOARDING_STEPS.map((s, i) => (
            <span key={s.id} className={`onboarding-pip${i === index ? ' active' : ''}`} />
          ))}
        </div>
        <div className="onboarding-actions">
          <button type="button" className="onboarding-skip" onClick={onDone}>
            Skip tour
          </button>
          {index > 0 && (
            <button type="button" onClick={() => setIndex(index - 1)}>
              Back
            </button>
          )}
          <button type="button" onClick={() => (last ? onDone() : setIndex(index + 1))}>
            {last ? 'Start investigating' : 'Next'}
          </button>
        </div>
      </div>
    </div>
  );
}
