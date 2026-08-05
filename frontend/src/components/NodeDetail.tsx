import type { GraphNode } from '../types';

interface Props {
  node: GraphNode;
  onClose: () => void;
}

function fmt(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

// Properties that are noise in the detail panel.
const HIDDEN = new Set(['tweet_id', 'tweet_url', 'id', 'name', 'ticker']);

export default function NodeDetail({ node, onClose }: Props) {
  const entries = Object.entries(node.props).filter(([k]) => !HIDDEN.has(k));

  return (
    <div className="node-detail">
      <div className="node-title">
        <h3>{node.title}</h3>
        <div className="label">
          {node.label} · {node.id}
        </div>
        {node.tweet_url && (
          <a href={node.tweet_url} target="_blank" rel="noreferrer">
            Source tweet ↗
          </a>
        )}
        <div style={{ marginTop: 6 }}>
          <button onClick={onClose}>Close</button>
        </div>
      </div>

      <div className="props">
        <dl>
          {entries.map(([key, value]) => (
            <div className="prop" key={key}>
              <dt>{key}</dt>
              <dd>{fmt(value)}</dd>
            </div>
          ))}
        </dl>
      </div>
    </div>
  );
}
