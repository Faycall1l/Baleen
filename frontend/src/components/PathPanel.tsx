import type { PathResponse } from '../api';

interface Props {
  result: PathResponse | null;
  error: string | null;
  isSelectable: (id: string) => boolean;
  onNodeClick: (id: string) => void;
}

export default function PathPanel({ result, error, isSelectable, onNodeClick }: Props) {
  return (
    <>
      <h2>Connection</h2>
      {error && (
        <div className="empty-hint">
          <div className="error">{error}</div>
        </div>
      )}
      {!error && !result && <div className="empty-hint">Pick two nodes to trace their connection.</div>}
      {result && result.nodes.length === 0 && (
        <div className="empty-hint">
          No path found between {result.from} and {result.to} within 6 hops.
        </div>
      )}
      {result && result.nodes.length > 0 && (
        <div className="path-steps">
          <div className="path-length">
            {result.length === 0 ? 'Same node' : `${result.length} hop${result.length > 1 ? 's' : ''}`}
          </div>
          {result.nodes.map((node, i) => (
            <div className="path-step" key={`${node.id}-${i}`}>
              {i > 0 && (
                <div className="path-rel">{result.rels[i - 1]?.replace(/_/g, ' ').toLowerCase()}</div>
              )}
              <button
                type="button"
                className="node-link"
                disabled={!isSelectable(node.id)}
                onClick={() => onNodeClick(node.id)}
                title={isSelectable(node.id) ? `Select ${node.title ?? node.id}` : 'Not in the loaded view'}
              >
                <span className="graph-search-label">{node.label}</span> {node.title ?? node.id} ↗
              </button>
            </div>
          ))}
        </div>
      )}
    </>
  );
}
