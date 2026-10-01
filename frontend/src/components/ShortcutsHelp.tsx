import { SHORTCUTS } from '../shortcuts';

interface Props {
  onClose: () => void;
}

export default function ShortcutsHelp({ onClose }: Props) {
  return (
    <div className="onboarding-overlay" role="dialog" aria-label="Keyboard shortcuts">
      <div className="onboarding-card">
        <div className="onboarding-kicker">BALEEN · KEYBOARD</div>
        <h2>Shortcuts</h2>
        <dl className="shortcuts-list">
          {SHORTCUTS.map((s) => (
            <div className="shortcut-row" key={s.keys}>
              <dt>{s.keys}</dt>
              <dd>{s.action}</dd>
            </div>
          ))}
        </dl>
        <div className="onboarding-actions">
          <button type="button" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
