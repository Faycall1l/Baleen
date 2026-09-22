import { theme } from '../theme';

interface Props {
  labels: string[];
  counts: Map<string, number>;
  hiddenTypes: Set<string>;
  interactive: boolean;
  onToggleType: (label: string) => void;
}

export default function GraphLegend({ labels, counts, hiddenTypes, interactive, onToggleType }: Props) {
  return (
    <div className="graph-legend">
      {labels.map((label) => {
        const hidden = interactive && hiddenTypes.has(label);
        const count = counts.get(label) ?? 0;
        return (
          <span
            key={label}
            className={`graph-legend-item${hidden ? ' off' : ''}${interactive ? ' clickable' : ''}`}
            onClick={interactive ? () => onToggleType(label) : undefined}
            title={interactive ? `Toggle ${label} nodes` : `${count} in view`}
          >
            <span
              className="graph-legend-dot"
              style={{ background: theme.nodeColors[label as keyof typeof theme.nodeColors] }}
            />
            {label} · {count}
          </span>
        );
      })}
    </div>
  );
}
