interface Props {
  armed: boolean;
  onArm: () => void;
  onCancel: () => void;
}

// Shared "how is this connected" entry (R53b): arms path-picking from any
// inspector; the next canvas click or search pick becomes the target.
export default function PathFromButton({ armed, onArm, onCancel }: Props) {
  if (!armed) {
    return (
      <button type="button" className="node-link" onClick={onArm} title="Pick another node to show the connection">
        Path from here
      </button>
    );
  }
  return (
    <div className="path-armed">
      <span>Pick a target node — click the canvas or search.</span>
      <button type="button" className="node-link" onClick={onCancel}>
        Cancel
      </button>
    </div>
  );
}
